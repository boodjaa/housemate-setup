# The Configurator
## Install Guide
[Installation Guide](https://github.com/boodjaa/housemate-setup/wiki)

## Features
Installs:
- Homebridge ✓
- WireGuard ✓
- Mosquitto ✓
- Python 3.11.15 ✓ (from source, takes ages - will skip if already installed)
- PAI ✓
- node-RED ✓
- AqualinkD ✓
- SprinklerD ✗

Configures:
- Hostname ✓
- VNC Server ✓
- SSH ✓
- Cron job scheduling ✓
  - Scheduled boot ✓
  - Health check ping ✓
- Systemd autostart units ✓
- Homebridge ✓
    - Plugins ✓
- WireGuard ✓
- Mosquitto ✓
- PAI ✓
- AqualinkD ✓
- SprinklerD ✗

## CLI flags

| Flag               | Description                                                                                          |
| ------------------ | ---------------------------------------------------------------------------------------------------- |
| `[config]`           | Path to the YAML config file (positional, required)                                                  |
| `--dry-run`        | Preview only; no root required, no system changes. Testing purposes                                  |
| `--verbose` / `-v` | Also echo log lines to the console                                                                   |
| `--log-file PATH`  | Override the log location (default `/var/log/housemate-setup.log`, falling back to `./logs/housemate-setup.log`) |

## Adding with a new module

1. Add `modules/<name>.py` with a class implementing `modules.base.Module`.
2. Register it in `modules/__init__.py`'s `MODULE_REGISTRY`.
3. Register it in `core/dependencies.py` as either a `REQUIRED_MODULE` or `OPTIONAL_MODULE`
4. Add its `DEPENDENCIES` entry in `core/dependencies.py` if it requires another optional module (e.g. MQTT).
5. Add any Jinja2 templates it needs under `templates/<name>/`.

No changes to `setup.py` are required.
