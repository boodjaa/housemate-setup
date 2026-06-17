# Pi Home Automation Appliance Provisioning Framework

Unattended, idempotent provisioning of a Raspberry Pi into a Homebridge +
WireGuard home-automation appliance, driven by a single YAML config file.

This is the **first iteration**: only the two mandatory modules
(Homebridge, WireGuard) are implemented. The architecture (orchestrator,
dependency resolver, module contract, templating, idempotency, logging,
live status UI) is built to spec and ready for the optional modules
(MQTT, PAI, SprinklerD, AqualinkD) to be dropped in later without touching
`setup.py` or `core/`.

## Requirements

- Raspberry Pi OS (or other Debian-based distro) on the target device
- Python 3.11+ (tested with 3.12; written for forward-compat with 3.13)
- Root access on the target device (`sudo`)

## Setup

On the Raspberry Pi:

```bash
git clone <this repo> pi-appliance   # or copy the directory over
cd pi-appliance
pip install -r requirements.txt --break-system-packages
cp config.example.yaml my-config.yaml
# edit my-config.yaml with your homebridge/wireguard settings
sudo ./setup.py my-config.yaml
```

## Dry-run mode

You don't need a Pi or root to try this out. `--dry-run`:

- Never executes apt/npm/systemctl/wg/curl/gpg commands (they're logged,
  not run)
- Still performs real, read-only status checks (`dpkg-query`, `wg pubkey`
  on an existing key, etc.) so the preview reflects actual system state
  where one exists
- Writes rendered config files into `./dry-run-output/` (mirroring the
  real absolute paths, e.g. `./dry-run-output/etc/wireguard/wg0.conf`)
  instead of the real system locations

```bash
./setup.py config.example.yaml --dry-run
```

## CLI flags

| Flag | Description |
|---|---|
| `config` | Path to the YAML config file (positional, required) |
| `--dry-run` | Preview only; no root required, no system changes |
| `--verbose` / `-v` | Also echo log lines to the console |
| `--log-file PATH` | Override the log location (default `/var/log/hub-setup.log`, falling back to `./logs/hub-setup.log`) |

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

## Idempotency

Every module is safe to rerun:

- **Packages**: checked via `dpkg-query` before any `apt-get install`; skipped if already installed.
- **Configs**: rendered, then SHA-256 compared against the existing file; only written if content actually changed.
- **Secrets**: homebridge's bridge `username` and wireguard's private key are read back from existing config files and reused rather than regenerated on every run.
- **Services**: `systemctl enable` unconditionally (a no-op if already enabled); `restart` only if the config changed this run, otherwise just `start`.

## Failure handling

- If a **mandatory** module (homebridge/wireguard) fails any phase, the run aborts immediately, remaining modules are marked "not started", and the process exits `1`.
- (Once optional modules exist) a failure in an optional module logs the error and continues with the rest, exiting `2` ("succeeded with warnings") rather than `1`.
- Full command output (stdout/stderr) is only ever shown for the commands that actually failed -- everything else stays quiet behind the live status tree.

## Extending with a new module

1. Add `modules/<name>.py` with a class implementing `modules.base.Module`.
2. Register it in `modules/__init__.py`'s `MODULE_REGISTRY`.
3. Add its `DEPENDENCIES` entry in `core/dependencies.py` if it requires another optional module (e.g. MQTT).
4. Add any Jinja2 templates it needs under `templates/<name>/`.

No changes to `setup.py` are required.
