"""
Paradox Alarm Interface (PAI) module.

Install:   Creates a dedicated Python 3.11 virtual environment at /opt/pai,
           upgrades packaging tools, and installs paradox-alarm-interface via pip.
Configure: Renders /etc/pai/pai.conf from the YAML settings using a Jinja2 
           template, and writes the systemd service unit file.
Enable:    Reloads systemd, enables the pai.service unit, and starts/restarts 
           it depending on whether the configuration changed.
"""

from __future__ import annotations

from pathlib import Path

from modules.base import Module, ModuleError

SERVICE_NAME = "pai.service"
VENV_DIR = "/opt/pai/venv"
PAI_BIN = f"{VENV_DIR}/bin/pai-service"
CONFIG_PATH = "/etc/pai/pai.conf"
SERVICE_PATH = "/etc/systemd/system/pai.service"

# Static systemd unit file content as per official PAI recommendations
SERVICE_CONTENT = """\
[Unit]
Description=Paradox Alarm Interface
Wants=network-online.target signal.service
After=network-online.target signal.service

[Service]
Type=simple
ExecStart=/opt/pai/venv/bin/pai-service -c /etc/pai/pai.conf
Restart=always
RestartSec=10

# Uncomment if serial access requires root
# User=root

WorkingDirectory=/opt/pai

[Install]
WantedBy=multi-user.target
Alias=pai.service
"""


class PaiModule(Module):
    name = "pai"
    required = False

    def validate(self) -> None:
        required_keys = ["endpoint", "pc_password", "mqtt_host", "interface_password"]
        for key in required_keys:
            if key not in self.settings:
                raise ModuleError(f"Missing required PAI setting: '{key}'")

    # -- install ----------------------------------------------------------
    def install(self) -> None:
        if Path(PAI_BIN).exists():
            self.logger.info("PAI already installed at %s... Skipping.", PAI_BIN)
            return

        self.logger.info("Creating Python 3.11 virtual environment for PAI...")
        self.runner.run(["mkdir", "-p", "/opt/pai"])
        self.runner.run(["python3.11", "-m", "venv", VENV_DIR])
        
        pip_bin = f"{VENV_DIR}/bin/pip"
        self.logger.info("Upgrading packaging tools in venv...")
        self.runner.run([pip_bin, "install", "--upgrade", "pip", "setuptools", "wheel"])
        
        self.logger.info("Installing paradox-alarm-interface...")
        self.runner.run([pip_bin, "install", "paradox-alarm-interface"])

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

        context = {
            "serial_port": _fmt(self.settings.get("serial_port")),
            "pc_password": _fmt(self.settings.get("pc_password")),
            "mqtt_host": _fmt(self.settings.get("mqtt_host")),
            "interface_password": _fmt(self.settings.get("interface_password")),
        }
        
        # Ensure the configuration directory exists
        self.runner.run(["mkdir", "-p", "/etc/pai"])
        
        conf_changed = self.templates.render_to_file("pai/pai.conf.j2", context, CONFIG_PATH)
        service_changed = self.templates.write_text(SERVICE_CONTENT, SERVICE_PATH)
        
        # Track if anything changed so enable() knows whether to restart the service
        self._last_configure_changed = bool(conf_changed) or bool(service_changed)
        return self._last_configure_changed

    # -- enable -----------------------------------------------------------
    def enable(self) -> None:
        self.runner.run(["systemctl", "daemon-reload"])
        self.runner.run(["systemctl", "enable", SERVICE_NAME])
        
        config_changed = getattr(self, "_last_configure_changed", False)
        if config_changed:
            self.runner.run(["systemctl", "restart", SERVICE_NAME], check=False)
        else:
            self.runner.run(["systemctl", "start", SERVICE_NAME], check=False)

    # -- status -----------------------------------------------------------
    def status(self) -> bool:
        installed = Path(PAI_BIN).exists()
        active = self.runner.query(["systemctl", "is-active", "--quiet", SERVICE_NAME]).ok
        return installed and active