"""
Homebridge module (spec section 7). Always installed -- mandatory module.

Install:   adds the official apt repo + GPG key, apt-get install homebridge.
Configure: renders /var/lib/homebridge/config.json from the YAML, installing
           or updating any configured plugins via npm.
Enable:    systemd unit shipped by the apt package; enabled + restarted only
           if the rendered config actually changed.
"""

from __future__ import annotations

import json
import secrets
import tempfile
from pathlib import Path

from core.runner import CommandError
from modules.base import Module, ModuleError

REPO_KEYRING = "/usr/share/keyrings/homebridge.gpg"
REPO_SOURCES_LIST = "/etc/apt/sources.list.d/homebridge.list"
REPO_KEY_URL = "https://repo.homebridge.io/KEY.gpg"
REPO_LINE = f"deb [signed-by={REPO_KEYRING}] https://repo.homebridge.io stable main\n"

CONFIG_PATH = "/var/lib/homebridge/config.json"
SERVICE_NAME = "homebridge"


class HomebridgeModule(Module):
    name = "homebridge"
    required = True

    def validate(self) -> None:
        plugins = self.settings.get("plugins", {})
        for plugin_name in plugins:
            if not plugin_name.startswith("homebridge-"):
                raise ModuleError(
                    f"Plugin '{plugin_name}' doesn't look like a homebridge plugin "
                    f"(expected a name starting with 'homebridge-')"
                )

    # -- install ----------------------------------------------------------
    def install(self) -> None:
        if self.runner.package_installed("homebridge"):
            self.logger.info("Homebridge already installed... Skipping.")
            return

        self._add_apt_repo()
        self.runner.run(["apt", "update"])
        self.runner.run(["apt", "install", "-y", "homebridge"])

    def _add_apt_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_key_path = str(Path(tmp) / "homebridge-key.gpg")
            self.runner.run(["curl", "-sSfL", REPO_KEY_URL, "-o", tmp_key_path])
            self.runner.run(["gpg", "--batch", "--yes", "--dearmor", "-o", REPO_KEYRING, tmp_key_path])
        self.templates.write_text(REPO_LINE, REPO_SOURCES_LIST)

    # -- configure ---------------------------------------------------------
    def configure(self) -> bool:
        plugins = self.settings.get("plugins", {})
        context = {
            "bridge_name": self.settings["bridge_name"],
            "port": self.settings["port"],
            "pin": self.settings["pin"]
        }
        changed = self.templates.render_to_file("homebridge/config.json.j2", context, CONFIG_PATH)

        for plugin_name, plugin_cfg in plugins.items():
            if not plugin_cfg.get("enabled"):
                continue
            self._ensure_plugin(plugin_name)

        self._last_configure_changed = changed
        return changed

    def _ensure_plugin(self, plugin_name: str) -> None:
        try:
            self.runner.run(["hb-service", "add", plugin_name])
        except CommandError as exc:
            raise ModuleError(f"Failed to install plugin '{plugin_name}': {exc}") from exc

    # -- enable -------------------------------------------------------------
    def enable(self) -> None:
        config_changed = getattr(self, "_last_configure_changed", False)
        self.runner.run(["hb-service", "start"], check=False)
        if config_changed:
            self.runner.run(["hb-service", "restart"], check=False)
        else:
            self.runner.run(["hb-service", "start"], check=False)

    # -- status --------------------------------------------------------------
    def status(self) -> bool:
        installed = self.runner.package_installed("homebridge")
        active = self.runner.query(["systemctl", "is-active", "--quiet", SERVICE_NAME]).ok
        return installed and active
