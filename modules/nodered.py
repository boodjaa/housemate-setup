"""
Node-RED module. Optional.

Install:   Downloads the official Node-RED linux installer script to a
           temporary file and executes it as the original (non-root) user
           via runuser. The script uses sudo internally for the parts that
           need elevation (Node.js install, service file creation), but the
           outer invocation must be a regular user -- running it as root
           causes npm packages, cache dirs, and user data to end up owned
           by root.

Configure: No configuration file is rendered -- the installer ships its
           own working service file. Extend this method in a future
           iteration if per-installation settings (port, project dir, etc.)
           are needed.

Enable:    Enables and starts nodered.service.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

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
            self.logger.info("node-red already installed, skipping install")
            return

        user = self._invoking_user()
        if user is None:
            if self.dry_run:
                # In dry-run there's no real system to query, so use a
                # placeholder that makes the logged command readable.
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

            # Download as root into a temp path -- always a separate step so
            # curl's exit code is checked before we try to execute anything.
            self.runner.run(["curl", "-sL", INSTALL_SCRIPT_URL, "-o", script_path])

            # The temp dir is owned by root; make the script readable by the
            # target user so runuser can execute it.
            self.runner.run(["chmod", "644", script_path])

            # Drop from root to the original user for the actual install.
            # runuser is the right tool here: it switches user identity when
            # already running as root, without requiring a password (unlike su).
            #
            # --confirm-install : skip the interactive "are you sure?" prompt
            # --node20          : pin the Node.js LTS major version
            self.runner.run(
                [
                    "runuser", "-u", user, "--",
                    "bash", script_path, "--confirm-install", "--node20",
                ],
                timeout=1800,   # installs Node.js + npm packages; can be slow
            )

    # -- configure -----------------------------------------------------------
    def configure(self) -> bool:
        # Nothing to render yet -- the installer ships its own service file.
        return False

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
        """Check for the node-red binary -- present after a successful install."""
        return self.runner.query(["which", "node-red"]).ok

    def _invoking_user(self) -> str | None:
        """Return the user who invoked sudo, or None if running directly as root.

        sudo always sets SUDO_USER to the original username. If it isn't
        set, the process was either started by root directly (no sudo) or
        is running in a container/CI context with no real user session.
        """
        return os.environ.get("SUDO_USER") or None
