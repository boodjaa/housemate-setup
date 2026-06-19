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


def _generate_username() -> str:
    """Generate a MAC-address-shaped 'username' for the homebridge bridge.

    The high bit pattern (02) marks it as a locally-administered address,
    matching the convention homebridge itself uses when it self-generates one.
    """
    octets = [0x02] + [secrets.randbits(8) for _ in range(5)]
    return ":".join(f"{o:02X}" for o in octets)


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
            self.logger.info("homebridge package already installed, skipping install")
            return

        self._add_apt_repo()
        self.runner.run_apt(["update"])
        self.runner.run_apt(["install", "-y", "homebridge"])

        plugins = self.settings.get("plugins", {}) or {}

        for plugin_name, plugin_cfg in plugins.items():
            if not (plugin_cfg or {}).get("enabled"):
                continue
            self._ensure_plugin(plugin_name)

    def _add_apt_repo(self) -> None:
        # Deliberately split into discrete commands (download, then dearmor)
        # rather than `curl | gpg | tee` -- a shell pipeline's exit status
        # reflects only its last stage, which would silently swallow a
        # curl failure (e.g. network/DNS issues) and let install() barrel
        # ahead as if the key had been fetched successfully.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_key_path = str(Path(tmp) / "homebridge-key.gpg")
            self.runner.run(["curl", "-sSfL", REPO_KEY_URL, "-o", tmp_key_path])
            self.runner.run(["gpg", "--batch", "--yes", "--dearmor", "-o", REPO_KEYRING, tmp_key_path])
        self.templates.write_text(REPO_LINE, REPO_SOURCES_LIST)

    # -- configure ---------------------------------------------------------
    def _existing_username(self) -> str | None:
        existing = self.templates.existing_file_text(CONFIG_PATH)
        if not existing:
            return None
        try:
            data = json.loads(existing)
            return data.get("bridge", {}).get("username")
        except (json.JSONDecodeError, AttributeError):
            return None

    def configure(self) -> bool:
        from modules.homebridge_plugins import PLUGIN_TRANSFORMERS
        username = self._existing_username() or _generate_username()
        plugins = self.settings.get("plugins", {}) or {}

        # Build the accessories list by running each enabled plugin's
        # transformer (if it has one). Plugins without a transformer
        # (simple ones that don't need per-accessory config) contribute
        # nothing to the accessories array -- they just need to be
        # installed via npm, which _ensure_plugin() handles below.
        accessories = []
        for plugin_name, plugin_cfg in plugins.items():
            if not (plugin_cfg or {}).get("enabled"):
                continue
            transformer = PLUGIN_TRANSFORMERS.get(plugin_name)
            if transformer:
                try:
                    accessories.extend(transformer(plugin_cfg))
                except (ValueError, KeyError) as exc:
                    raise ModuleError(
                        f"Failed to build accessories config for {plugin_name}: {exc}"
                    ) from exc

        context = {
            "client_id": self.settings["client_id"],
            "port":        self.settings["port"],
            "pin":         self.settings["pin"],
            "username":    username,
            "accessories": accessories,
            "ui_port":        self.settings["ui_port"],
        }
        changed = self.templates.render_to_file("homebridge/config.json.j2", context, CONFIG_PATH)

        self._last_configure_changed = changed
        return changed

    def _ensure_plugin(self, plugin_name: str) -> None:
        # check = self.runner.query(["npm", "list", "-g", plugin_name, "--depth=0"])
        # if check.ok:
        #     self.logger.info("Plugin %s already installed, skipping", plugin_name)
        #     return
        try:
            self.runner.run(["hb-service", "add", plugin_name])
        except CommandError as exc:
            raise ModuleError(f"Failed to install plugin '{plugin_name}': {exc}") from exc

    # -- enable -------------------------------------------------------------
    def enable(self) -> None:
        config_changed = getattr(self, "_last_configure_changed", False)
        # self.runner.run(["hb-service", "start"], check=False)
        if config_changed:
            self.runner.run(["hb-service", "restart"], check=False)
        else:
            self.runner.run(["hb-service", "start"], check=False)

    # -- status --------------------------------------------------------------
    def status(self) -> bool:
        installed = self.runner.package_installed("homebridge")
        active = self.runner.query(["systemctl", "is-active", "--quiet", SERVICE_NAME]).ok
        return installed and active
