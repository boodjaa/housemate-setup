"""
Node-RED module. Optional.

Install:   Downloads the official Node-RED linux installer script to a
           temporary file and executes it as the original (non-root) user
           via runuser. The script uses sudo internally for the parts that
           need elevation (Node.js install, service file creation), but the
           outer invocation must be a regular user -- running it as root
           causes npm packages, cache dirs, and user data to end up owned
           by root.

           A SudoKeepalive runs alongside the install to prevent the sudo
           session from expiring mid-script -- the installer can take 10+
           minutes on a Pi 4, long enough to outlast the default timeout.

Configure: Installs any configured npm plugins into the user's .node-red
           directory, and optionally copies a pre-built flows.json.

Enable:    Enables and starts nodered.service.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from core.runner import CommandError
from core.sudo_keepalive import SudoKeepalive
from modules.base import Module, ModuleError

INSTALL_SCRIPT_URL = (
    "https://raw.githubusercontent.com/node-red/linux-installers"
    "/master/deb/update-nodejs-and-nodered"
)
SERVICE_NAME = "nodered"


class NoderedModule(Module):
    name = "nodered"
    required = False

    def validate(self) -> None:
        pass

    # -- install -------------------------------------------------------------
    def install(self) -> None:
        if self._is_installed():
            self.logger.info("node-red already installed, running installer to update...")

        user = self._invoking_user()
        if user is None:
            if self.dry_run:
                user = "<sudo-user>"
            else:
                raise ModuleError(
                    "Node-RED's install script must be executed as a regular user "
                    "(it calls sudo internally for the parts requiring root). "
                    "SUDO_USER is not set -- make sure you are invoking setup.py "
                    "via sudo rather than logging in directly as root: "
                    "sudo ./setup.py config.yaml"
                )

        with tempfile.TemporaryDirectory(prefix="nodered-install-") as tmp:
            script_path = str(Path(tmp) / "install-nodered.sh")

            # Download as root; check curl's exit code before executing anything.
            self.runner.run(["curl", "-sL", INSTALL_SCRIPT_URL, "-o", script_path])

            # tempfile creates directories 0700 (owner-only). The non-root
            # user running the script via runuser can't enter a root-owned
            # 0700 directory, so open it up first.
            self.runner.run(["chmod", "755", str(Path(tmp))])
            self.runner.run(["chmod", "644", script_path])

            # SudoKeepalive refreshes the sudo timestamp every 60s so the
            # session stays alive for the full install duration. Without it,
            # the installer hangs when it makes internal sudo calls after the
            # default timeout expires.
            with SudoKeepalive(interval=60, logger=self.logger, dry_run=self.dry_run):
                self.runner.run(
                    [
                        "runuser", "-u", user, "--",
                        "bash", script_path,
                        "--confirm-install",
                        "--confirm-pi",
                        "--node24",
                    ],
                    timeout=1800,
                )

    # -- configure -----------------------------------------------------------
    def configure(self) -> bool:
        changed = False

        # Install npm plugins into the user's .node-red directory
        plugins = self.settings.get("plugins") or {}
        for plugin_name, plugin_cfg in plugins.items():
            if not (plugin_cfg or {}).get("enabled"):
                continue
            self._ensure_plugin(plugin_name)
            changed = True

        # Copy a pre-built flows.json if one is provided and not already present
        flows_src = self.settings.get("flows")
        if flows_src:
            node_red_dir = self._node_red_dir()
            if node_red_dir is None:
                self.logger.warning(
                    "Cannot determine user home directory to copy flows.json -- "
                    "SUDO_USER is not set"
                )
            else:
                dest = node_red_dir / "flows.json"
                if dest.exists():
                    self.logger.info(
                        "flows.json already exists at %s, skipping copy", dest
                    )
                else:
                    if not self.dry_run:
                        shutil.copy2(flows_src, dest)
                    self.logger.info("Copied %s to %s", flows_src, dest)
                    changed = True
        else:
            self.logger.info("No flows.json provided, skipping")

        return changed

    def _ensure_plugin(self, plugin_name: str) -> None:
        node_red_dir = self._node_red_dir()
        prefix = str(node_red_dir) if node_red_dir else "/root/.node-red"
        try:
            self.runner.run(["npm", "install", "--prefix", prefix, plugin_name])
        except CommandError as exc:
            raise ModuleError(
                f"Failed to install Node-RED plugin '{plugin_name}': {exc}"
            ) from exc

    # -- enable --------------------------------------------------------------
    def enable(self) -> None:
        self.runner.run(["systemctl", "enable", SERVICE_NAME], check=False)
        self.runner.run(["systemctl", "start",  SERVICE_NAME], check=False)

    # -- status --------------------------------------------------------------
    def status(self) -> bool:
        return (
            self._is_installed()
            and self.runner.query(["systemctl", "is-active", "--quiet", SERVICE_NAME]).ok
        )

    # -- helpers -------------------------------------------------------------
    def _is_installed(self) -> bool:
        return self.runner.query(["which", "node-red"]).ok

    def _invoking_user(self) -> str | None:
        """Return the user who invoked sudo, or None if running directly as root."""
        return os.environ.get("SUDO_USER") or None

    def _node_red_dir(self) -> Path | None:
        """Return the path to the invoking user's .node-red directory.

        Derived from the invoking username rather than hardcoded to
        /home/admin so the module works regardless of what the user account
        is named on this particular machine.
        """
        user = self._invoking_user()
        if user is None:
            return None
        return Path(f"/home/{user}/.node-red")
