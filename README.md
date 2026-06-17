# HM Provisioning Script

## Setup

On the Raspberry Pi:

```bash
git clone https://github.com/boodjaa/housemate-setup
cd housemate-setup
pip install -r requirements.txt --break-system-packages
cp config.example.yaml config.yaml
# edit config.yaml with settings
sudo ./setup.py config.yaml
```

## Dry-run mode

Mainly for testing

- Never executes apt/npm/systemctl/wg/curl/gpg commands (logged,
  not run)
- Still does read-only status checks (`dpkg-query`, `wg pubkey`
  on an existing key, etc.) so the preview reflects actual system state
  where one exists
- Writes rendered config files into `./dry-run-output/` (mirroring the
  real absolute paths, e.g. `./dry-run-output/etc/wireguard/wg0.conf`)
  instead of the real system locations

```bash
./setup.py config.example.yaml --dry-run
```

## CLI flags

| Flag               | Description                                                                                          |
| ------------------ | ---------------------------------------------------------------------------------------------------- |
| `config`           | Path to the YAML config file (positional, required)                                                  |
| `--dry-run`        | Preview only; no root required, no system changes. Testing purposes                                  |
| `--verbose` / `-v` | Also echo log lines to the console                                                                   |
| `--log-file PATH`  | Override the log location (default `/var/log/hub-setup.log`, falling back to `./logs/hub-setup.log`) |

## Project layout

```
setup.py                  orchestrator (load -> validate -> resolve -> run -> summarize)
core/
  config.py                YAML loading + schema validation
  logger.py                 file logging (INFO/WARNING/ERROR)
  runner.py                 subprocess wrapper: dry-run aware, captures output,
                             only surfaces it on failure; query() for read-only
                             status checks that run for real even in --dry-run
  templates.py               Jinja2 rendering with checksum-based idempotency
                              and dry-run path sandboxing
  dependencies.py            required vs optional module resolution
  context.py                 shared Context object passed into every module
  ui.py                       live status tree with an animated spinner
modules/
  base.py                    abstract Module contract (validate/install/
                              configure/enable/status)
  homebridge.py               mandatory
  wireguard.py                 mandatory
  __init__.py                  MODULE_REGISTRY -- add future modules here only
templates/
  homebridge/config.json.j2
  wireguard/wg0.conf.j2
config.example.yaml         sample configuration
requirements.txt
```

## Adding with a new module

1. Add `modules/<name>.py` with a class implementing `modules.base.Module`.
2. Register it in `modules/__init__.py`'s `MODULE_REGISTRY`.
3. Register it in `core/dependencies.py` as either a `REQUIRED_MODULE` or `OPTIONAL_MODULE`
4. Add its `DEPENDENCIES` entry in `core/dependencies.py` if it requires another optional module (e.g. MQTT).
5. Add any Jinja2 templates it needs under `templates/<name>/`.

No changes to `setup.py` are required.