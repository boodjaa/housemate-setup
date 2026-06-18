"""
AqualinkD module.

Install:   Runs official install script from sfeakes/AqualinkD repo.
Configure: Renders /etc/pai/pai.conf from the YAML settings using a Jinja2 
           template.
Enable:    Reloads systemd, enables the aqualinkd.service unit, and starts/restarts 
           it depending on whether the configuration changed.
"""

from __future__ import annotations

from pathlib import Path

from modules.base import Module, ModuleError

BIN_PATH = "/usr/local/bin/aqualinkd"
CONFIG_PATH = "/etc/aqualinkd.conf"
SERVICE_PATH = "/etc/systemd/system/aqualinkd.service"

class AqualinkDModule(Module):
    name = "aqualinkd"
    required = False

    def validate(self) -> None:
        required_keys = ["panel_type", "mqtt_address"]
        for key in required_keys:
            if key not in self.settings:
                raise ModuleError(f"Missing required AqualinkD setting: '{key}'")

    # -- install ----------------------------------------------------------
    def install(self) -> None:
        if Path(BIN_PATH).exists():
            self.logger.info("AqualinkD already installed at %s... Skipping.", BIN_PATH)
            return

        install_script = "/tmp/aqualinkd_install.sh"
        self.logger.info("Downloading AqualinkD install script...")
        
        # Download to a file first to avoid shell=True and pipefail issues
        self.runner.run(["curl", "-fsSL", "https://install.aqualinkd.com", "-o", install_script])

        self.logger.info("Running install script for AqualinkD...")
        try:
            self.runner.run(["bash", install_script, "-s", "--", "latest"])
        finally:
            # Clean up the temporary script regardless of success or failure
            Path(install_script).unlink(missing_ok=True)

    # -- configure --------------------------------------------------------
    def configure(self) -> bool:
        # The Jinja template inserts variables without quotes (e.g. HOST = {{ endpoint }}).
        # We must wrap string values in quotes here so the resulting .conf file 
        # is valid Python syntax.
        def _fmt(val):
            if val is None:
                return "None"
            if isinstance(val, bool):
                return "True" if val else "False"
            
            # Explicitly cast to string. This ensures that if a user forgets to 
            # quote a numeric password like 1234 in YAML, it still gets wrapped 
            # in quotes for the PAI config file.
            return f"'{str(val)}'"

        # FIX: Actually apply the _fmt() helper to the values!
        context = {
            "panel_type": self.settings["panel_type"],
            "mqtt_address": self.settings["mqtt_address"]
        }
        
        conf_changed = self.templates.render_to_file("aqualinkd/aqualinkd.conf.j2", context, CONFIG_PATH)
        
        # Track if anything changed so enable() knows whether to restart the service
        self._last_configure_changed = bool(conf_changed)
        return self._last_configure_changed

    # -- enable -----------------------------------------------------------
    def enable(self) -> None:
        self.runner.run(["systemctl", "daemon-reload"])
        self.runner.run(["systemctl", "enable", "aqualinkd.service"])
        
        config_changed = getattr(self, "_last_configure_changed", False)
        if config_changed:
            self.runner.run(["systemctl", "restart", "aqualinkd.service"], check=False)
        else:
            self.runner.run(["systemctl", "start", "aqualinkd.service"], check=False)

    # -- status -----------------------------------------------------------
    def status(self) -> bool:
        installed = Path(BIN_PATH).exists()
        active = self.runner.query(["systemctl", "is-active", "--quiet", "aqualinkd.service"]).ok
        return installed and active