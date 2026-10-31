#!/usr/bin/env python3
"""
config.yaml linter for the Pi Home Automation Appliance Provisioning Framework.

Usage:
    python3 lint.py config.yaml
    python3 lint.py config.yaml --strict   # treat warnings as errors

Exit codes:
    0  clean
    1  warnings only
    2  one or more errors
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

console = Console()

# ---------------------------------------------------------------------------
# Schema types
# ---------------------------------------------------------------------------

@dataclass
class FieldSpec:
    """Describes one field within a module's config section."""
    presence: str                          # "required" or "optional"
    types: tuple                           # accepted Python types
    hint: str = ""                         # shown in error messages
    values: list | None = None             # finite set of accepted values
    is_file: bool = False                  # value must be a path to an existing file
    validator: Callable | None = None      # (value) -> error_str | None


def _required(types, hint="", **kwargs) -> FieldSpec:
    return FieldSpec("required", types, hint, **kwargs)

def _optional(types, hint="", **kwargs) -> FieldSpec:
    return FieldSpec("optional", types, hint, **kwargs)


# ---------------------------------------------------------------------------
# Per-module field schemas
# ---------------------------------------------------------------------------

_PIN_RE   = re.compile(r"^\d{3}-\d{2}-\d{3}$")
_HOST_RE  = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$")
_MAC_RE   = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_VER_RE   = re.compile(r"^3\.11\.\d+$")
_PUBKEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _check_pin(v):
    if not _PIN_RE.match(str(v)):
        return f"must be in format XXX-XX-XXX with digits only, e.g. '031-45-154' — got '{v}'"

def _check_hostname(v):
    if not _HOST_RE.match(str(v)):
        return f"'{v}' is not a valid hostname (letters, digits and hyphens only; cannot start or end with a hyphen)"

def _check_pubkey(v):
    if not _PUBKEY_RE.match(str(v)):
        return f"must be a 44-character base64 WireGuard public key ending in '=' — got '{v}' ({len(str(v))} chars)"

def _check_port(v):
    if not isinstance(v, int) or not (1 <= v <= 65535):
        return f"must be an integer between 1 and 65535 — got '{v}'"

def _check_py311_version(v):
    if not _VER_RE.match(str(v)):
        return f"must be a 3.11.x release string, e.g. '3.11.15' — got '{v}'"

def _check_mac(v):
    if not _MAC_RE.match(str(v)):
        return f"must be a MAC address in the format AA:BB:CC:DD:EE:FF — got '{v}'"

def _check_hc_uuid(v):
    # healthchecks.io UUIDs are hex-and-dash strings; just check it's non-empty
    if not str(v).strip():
        return "healthcheck UUID cannot be empty"


MODULE_SCHEMAS: dict[str, dict[str, FieldSpec]] = {
    "base": {
        "hostname":    _required((str,),  "a valid hostname string",       validator=_check_hostname),
        "ssh":         _required((bool,), "true or false"),
        "vnc":         _required((bool,), "true or false"),
        "healthcheck": _optional((str,),  "a healthchecks.io check UUID",  validator=_check_hc_uuid),
    },
    "homebridge": {
        "client_id": _required((str,),  "a display name for the homebridge bridge"),
        "port":      _required((int,),  "bridge port number (1–65535)",   validator=_check_port),
        "ui_port":   _required((int,),  "UI port number (1–65535)",       validator=_check_port),
        "pin":       _required((str,),  "HomeKit pairing pin",            validator=_check_pin),
        "plugins":   _optional((dict,), "mapping of plugin names to their config"),
    },
    "wireguard": {
        "endpoint":             _required((str,),  "hostname:port of the WireGuard server"),
        "server_pubkey":        _required((str,),  "44-character base64 public key",    validator=_check_pubkey),
        "address":              _optional((str,),  "client IP/CIDR, e.g. 10.10.0.2/24"),
        "allowed_ips":          _optional((str,),  "e.g. 0.0.0.0/0"),
        "persistent_keepalive": _optional((int,),  "seconds between keepalives, e.g. 25"),
        "dns":                  _optional((str,),  "DNS server IP address"),
    },
    "python311": {
        "enabled": _required((bool,), "true or false"),
        "version": _optional((str,),  "a 3.11.x version string, e.g. '3.11.15'", validator=_check_py311_version),
    },
    "nodered": {
        "enabled": _required((bool,), "true or false"),
        "flows":   _optional((str,),  "path to an existing flows.json file",      is_file=True),
        "plugins": _optional((dict,), "mapping of npm package names to {enabled: bool}"),
    },
    "mqtt": {
        "enabled":         _optional((bool,), "true or false (auto-enabled when PAI/SprinklerD/AqualinkD need it)"),
        "listener":        _optional((int,),  "MQTT port number, typically 1883"),
        "allow_anonymous": _optional((bool,), "true or false"),
    },
    "pai": {
        "enabled":            _optional((bool,),     "true or false"),
        "ip150_endpoint":     _optional((str,),      "IP address of the Paradox IP150 module"),
        "serial_port":        _optional((str,),      "serial device path, e.g. /dev/ttyUSB0"),
        "pc_password":        _optional((str,),      "a string -- quote it, e.g. \"0123\""),
        "interface_password": _optional((str,),      "a string -- quote it, e.g. \"secret\""),
        "mqtt_host":          _optional((str,),      "MQTT broker host"),
    },
    "sprinklerd": {
        "enabled": _required((bool,), "true or false"),
    },
    "aqualinkd": {
        "enabled":      _required((bool,), "true or false"),
        "panel_type":   _optional((str,),  "Aqualink panel type, e.g. 'RS_LX3900'"),
        "mqtt_address": _optional((str,),  "MQTT broker address, e.g. '192.168.1.10'"),
    },
}

# mqttthing accessory types and their known topic fields
_MQTTTHING_TYPES: dict[str, set[str]] = {
    "securitySystem": {"name", "type", "current_state", "get_target", "set_target",
                       "target_values", "current_values", "restrict_state"},
    "motionSensor":   {"name", "type", "motion_detected"},
    "contactSensor":  {"name", "type", "contact_sensor_state"},
    "switch":         {"name", "type", "get_on", "set_on"},
    "lightbulb":      {"name", "type", "get_on", "set_on", "get_brightness", "set_brightness"},
}

# Plugins in the framework's registry (for install-only detection)
_KNOWN_PLUGINS = {
    "homebridge-mqttthing", "homebridge-daikin-local", "homebridge-ueboom",
    "homebridge-denon-tv", "homebridge-shelly", "homebridge-kasa-python",
    "homebridge-google-smarthome", "homebridge-landroid", "homebridge-appletv-enhanced",
    "homebridge-google-nest-sdm", "homebridge-broadlink-rm", "homebridge-connexoon",
    "homebridge-tahoma", "homebridge-camera-ffmpeg", "homebridge-plex",
    "homebridge-weather-plus", "homebridge-network-presence", "homebridge-tplink-smarthome",
    "homebridge-tapo", "homebridge-samsung-tizen", "homebridge-hisense-tv",
}

KNOWN_MODULES = set(MODULE_SCHEMAS.keys())


# ---------------------------------------------------------------------------
# Issue collector
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    level: str        # "error", "warning" or "info" (info never affects the exit code)
    module: str       # top-level module the issue is grouped under
    message: str
    suggestion: str = ""
    where: str = ""   # finer-grained location, e.g. "plugins.homebridge-plex"


@dataclass
class LintResults:
    issues: list[Issue] = field(default_factory=list)
    root: Any = None   # yaml.compose() tree: lets us see quoting + resolved tags

    def node_at(self, path: tuple):
        """Find the YAML scalar node at a key path, e.g. ("base", "hostname")."""
        node = self.root
        for key in path:
            if not isinstance(node, yaml.MappingNode):
                return None
            node = next((v for k, v in node.value if getattr(k, "value", None) == key), None)
            if node is None:
                return None
        return node if isinstance(node, yaml.ScalarNode) else None

    def error(self, module: str, message: str, suggestion: str = "", where: str = "") -> None:
        self.issues.append(Issue("error", module, message, suggestion, where))

    def warning(self, module: str, message: str, suggestion: str = "", where: str = "") -> None:
        self.issues.append(Issue("warning", module, message, suggestion, where))

    def info(self, module: str, message: str, suggestion: str = "", where: str = "") -> None:
        self.issues.append(Issue("info", module, message, suggestion, where))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    def for_module(self, module: str) -> list[Issue]:
        return [i for i in self.issues if i.module == module]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _suggest(word: str, candidates: set[str], cutoff: float = 0.65) -> str:
    """Return a 'did you mean X?' string, or '' if nothing is close enough."""
    matches = difflib.get_close_matches(word, candidates, n=1, cutoff=cutoff)
    return f"did you mean '{matches[0]}'?" if matches else ""


def _type_name(types: tuple) -> str:
    names = [t.__name__ for t in types]
    return " or ".join(names)


# ---------------------------------------------------------------------------
# npm registry lookup (for homebridge plugins the framework doesn't manage)
# ---------------------------------------------------------------------------

NPM_REGISTRY = "https://registry.npmjs.org"
NPM_TIMEOUT = 8


@dataclass
class NpmResult:
    status: str                 # "plugin" | "not_plugin" | "missing" | "unreachable"
    detail: str = ""
    suggestion: str = ""


def _npm_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "pi-appliance-lint"})
    with urllib.request.urlopen(req, timeout=NPM_TIMEOUT) as resp:
        return json.load(resp)


def _npm_suggest(name: str) -> str:
    """Ask the registry's search endpoint for the closest real plugin name."""
    try:
        query = urllib.parse.quote(name)
        data = _npm_get(f"{NPM_REGISTRY}/-/v1/search?text={query}&size=10")
        names = [o["package"]["name"] for o in data.get("objects", [])
                 if "homebridge" in o["package"]["name"]]
        match = difflib.get_close_matches(name, names, n=1, cutoff=0.5)
        return f"did you mean '{match[0]}'?" if match else ""
    except Exception:
        return ""


def check_npm(name: str) -> NpmResult:
    """Look a package up on the npm registry.

    Uses /<package>/latest rather than the full package document -- it is a
    small response and carries the keywords/engines needed to tell a real
    homebridge plugin from an unrelated package that merely exists.
    """
    # Scoped names (@scope/pkg) must have their slash percent-encoded.
    encoded = urllib.parse.quote(name, safe="@")
    try:
        data = _npm_get(f"{NPM_REGISTRY}/{encoded}/latest")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return NpmResult("missing", suggestion=_npm_suggest(name))
        return NpmResult("unreachable", f"registry returned HTTP {exc.code}")
    except Exception as exc:  # network down, DNS failure, timeout, bad JSON
        return NpmResult("unreachable", str(exc))

    keywords = data.get("keywords") or []
    engines = data.get("engines") or {}
    if "homebridge-plugin" in keywords or "homebridge" in engines:
        return NpmResult("plugin", f"v{data.get('version', '?')}")
    return NpmResult("not_plugin", f"v{data.get('version', '?')}")


def check_npm_many(names: list[str]) -> dict[str, NpmResult]:
    """Look several packages up concurrently so a long plugin list stays fast."""
    if not names:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(names, pool.map(check_npm, names)))


# ---------------------------------------------------------------------------
# "Left at the default" detection
# ---------------------------------------------------------------------------

# Used only when no example config is found next to lint.py.
_PLACEHOLDER_CLIENT_IDS = {"homebridge", "my homebridge", "default", "changeme", "example"}


def _example_config() -> tuple[Path, dict] | None:
    """Locate and parse the repo's example config (config*example*.yaml).

    Reading the default from the example itself means "left at the default"
    keeps tracking whatever the example says, with no hardcoded copy to rot.
    """
    here = Path(__file__).resolve().parent
    for candidate in sorted(here.glob("config*example*.y*ml")):
        try:
            data = yaml.safe_load(candidate.read_text())
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            return candidate.resolve(), data
    return None


def check_default_client_id(results: LintResults, section: dict,
                            config_path: Path | None) -> None:
    client_id = section.get("client_id")
    if not isinstance(client_id, str):
        return
    norm = client_id.strip().lower()
    why = "it is the HomeKit bridge name, so give each appliance its own"

    example = _example_config()
    if example is not None:
        example_path, example_data = example
        if config_path is not None and config_path.resolve() == example_path:
            return   # linting the template itself -- placeholders are expected
        example_id = (example_data.get("homebridge") or {}).get("client_id")
        if isinstance(example_id, str) and norm == example_id.strip().lower():
            results.warning("homebridge",
                            f"'client_id' is still the example value '{client_id}' -- {why}")
            return
    if norm in _PLACEHOLDER_CLIENT_IDS:
        results.warning("homebridge",
                        f"'client_id' '{client_id}' looks like a placeholder -- {why}")


# ---------------------------------------------------------------------------
# Core linting logic
# ---------------------------------------------------------------------------

def _scalar_hint(results: LintResults, path: tuple, want_str: bool) -> str:
    """Explain *why* a value parsed as the wrong type, from how it was written.

    PyYAML resolves unquoted scalars by pattern, so `0123` becomes the integer
    83 (octal), `on` becomes True, `3.11` becomes a float and an all-digit MAC
    becomes a base-60 integer. Quoting is only needed where that happens --
    plain base64 keys and pins are already strings, so they are never flagged.
    """
    node = results.node_at(path)
    if node is None:
        return ""
    quoted = node.style in ("'", '"')
    tag = node.tag.rsplit(":", 1)[-1]
    if want_str and not quoted and tag != "str":
        kind = {"int": "an integer", "float": "a number",
                "bool": "a boolean", "null": "empty"}.get(tag, tag)
        msg = f"unquoted, so YAML read {node.value!r} as {kind}"
        if tag == "int" and ":" in node.value:
            msg += " (colon-separated digits are treated as a base-60 number)"
        elif tag == "int" and re.fullmatch(r"0\d+", node.value):
            msg += " (leading zeros are lost; YAML reads them as octal)"
        return f'{msg} -- wrap it in quotes: "{node.value}"'
    if not want_str and quoted:
        return "it is in quotes, so YAML reads it as text -- remove the quotes"
    return ""


def _check_file(results: LintResults, module: str, field_name: str,
                value: str, config_dir: Path | None) -> None:
    """Check a linked file exists.

    setup.py resolves relative paths against the *working directory*, so
    that is the authoritative check. If the file is missing there but sits
    next to the config file, report that specifically -- it is the common
    "ran the linter/installer from a different folder" mistake -- rather
    than a bare "not found".
    """
    path = Path(value)
    if path.is_file():
        return
    if not path.is_absolute() and config_dir is not None and (config_dir / path).is_file():
        results.warning(
            module,
            f"'{field_name}' ({value}) exists next to the config file but not in the "
            f"current directory -- setup.py resolves relative paths from where it is "
            f"run, so run it from {config_dir} or use an absolute path",
        )
        return
    results.error(module, f"'{field_name}' file not found: {value}")


def lint_section(
    results: LintResults,
    module: str,
    section: dict,
    schema: dict[str, FieldSpec],
    extra_known: set[str] | None = None,
    config_dir: Path | None = None,
) -> None:
    """Generic field linter for any module section."""
    known = set(schema.keys()) | (extra_known or set())

    # Unknown fields
    for key in section:
        if key not in known:
            suggestion = _suggest(key, known)
            results.warning(module, f"Unknown field '{key}'", suggestion)

    # Field-by-field validation
    for field_name, spec in schema.items():
        if field_name not in section:
            if spec.presence == "required":
                results.error(module, f"Missing required field '{field_name}' ({spec.hint})")
            continue

        value = section[field_name]

        # None means the user wrote the key but left it blank
        if value is None:
            if spec.presence == "required":
                results.error(module, f"'{field_name}' is required but has no value ({spec.hint})")
            continue

        # Type check
        if not isinstance(value, spec.types):
            hint = _scalar_hint(results, (module, field_name), str in spec.types)
            results.error(
                module,
                f"'{field_name}' must be {spec.hint or _type_name(spec.types)}, "
                f"got {type(value).__name__} ({value!r})" + (f" -- {hint}" if hint else ""),
            )
            continue

        # Custom validator
        if spec.validator:
            err = spec.validator(value)
            if err:
                results.error(module, f"'{field_name}': {err}")

        # Valid values (finite set)
        if spec.values and value not in spec.values:
            results.error(
                module,
                f"'{field_name}' is '{value}' — expected one of: {spec.values}",
            )

        # File existence
        if spec.is_file and value:
            _check_file(results, module, field_name, str(value), config_dir)


def lint_homebridge_plugins(results: LintResults, plugins: dict, online: bool = True) -> None:
    # Resolve every plugin the framework doesn't manage against npm up front,
    # concurrently, so one slow lookup doesn't serialise the whole run.
    unmanaged = [n for n, c in plugins.items()
                 if n not in _KNOWN_PLUGINS and n.startswith("homebridge-")]
    npm = check_npm_many(unmanaged) if online else {}

    for plugin_name, plugin_cfg in plugins.items():
        where = f"plugins.{plugin_name}"

        if not plugin_name.startswith("homebridge-"):
            results.error("homebridge", "Plugin name should start with 'homebridge-'",
                          _suggest(plugin_name, _KNOWN_PLUGINS), where)

        if not isinstance(plugin_cfg, dict):
            results.error("homebridge",
                          f"Plugin config must be a mapping, got {type(plugin_cfg).__name__}",
                          where=where)
            continue

        if plugin_name not in _KNOWN_PLUGINS and plugin_name.startswith("homebridge-"):
            _report_unmanaged(results, plugin_name, npm.get(plugin_name), online, where)

        if "enabled" not in plugin_cfg:
            results.warning("homebridge", "Missing 'enabled' -- plugin will not be installed",
                            where=where)

        # Child bridge settings only mean anything alongside child_bridge: true
        bridge_keys = [k for k in ("bridge_username", "bridge_port", "bridge_name", "bridge_pin")
                       if k in plugin_cfg]
        if bridge_keys and not plugin_cfg.get("child_bridge"):
            results.warning(
                "homebridge",
                f"{', '.join(bridge_keys)} set but child_bridge is not true -- they will be ignored",
                where=where,
            )

        if plugin_cfg.get("bridge_username") is not None:
            bu = plugin_cfg["bridge_username"]
            if not isinstance(bu, str):
                hint = _scalar_hint(results, ("homebridge", "plugins", plugin_name, "bridge_username"), True)
                results.error("homebridge",
                              f"'bridge_username' must be a string like AA:BB:CC:DD:EE:FF, got {bu!r}"
                              + (f" -- {hint}" if hint else ""), where=where)
            elif not _MAC_RE.match(bu):
                results.error(
                    "homebridge",
                    f"'bridge_username' must look like AA:BB:CC:DD:EE:FF, got '{bu}'",
                    where=where,
                )

        if plugin_cfg.get("bridge_port") is not None:
            err = _check_port(plugin_cfg["bridge_port"])
            if err:
                results.error("homebridge", f"'bridge_port': {err}", where=where)

        if plugin_name == "homebridge-mqttthing":
            lint_mqttthing(results, plugin_cfg)


def _report_unmanaged(results: LintResults, name: str, res: NpmResult | None,
                      online: bool, where: str) -> None:
    """Report on a plugin that the framework has no config schema for."""
    install_only = "installed only -- no config.json entry is generated for it"
    if not online or res is None:
        results.info("homebridge", f"Not managed by the framework ({install_only}); "
                                   "npm check skipped (--offline)", where=where)
    elif res.status == "plugin":
        results.info("homebridge", f"Valid npm plugin ({res.detail}); {install_only}", where=where)
    elif res.status == "not_plugin":
        results.warning("homebridge",
                        f"Exists on npm ({res.detail}) but doesn't look like a homebridge plugin "
                        "(no 'homebridge-plugin' keyword or homebridge engine)", where=where)
    elif res.status == "missing":
        results.error("homebridge", f"'{name}' does not exist on npm -- install would fail",
                      res.suggestion, where)
    else:  # unreachable
        results.warning("homebridge",
                        f"Couldn't verify against npm ({res.detail}); {install_only}", where=where)


def lint_mqttthing(results: LintResults, plugin_cfg: dict) -> None:
    accessories = plugin_cfg.get("accessories") or {}
    base_where = "plugins.homebridge-mqttthing"
    if not isinstance(accessories, dict):
        results.error("homebridge",
                      "'accessories' must be a mapping of accessory name -> config",
                      where=base_where)
        return

    known_types = set(_MQTTTHING_TYPES.keys())

    for acc_name, acc_cfg in accessories.items():
        where = f"{base_where}.accessories.{acc_name}"
        if not isinstance(acc_cfg, dict):
            results.error("homebridge", "Accessory must be a mapping of fields", where=where)
            continue

        if "name" not in acc_cfg:
            results.error("homebridge", "Missing required field 'name'", where=where)
        if "type" not in acc_cfg:
            results.error("homebridge", "Missing required field 'type'", where=where)
            continue

        acc_type = acc_cfg["type"]
        if acc_type not in known_types:
            results.error("homebridge", f"Unknown accessory type '{acc_type}'",
                          _suggest(acc_type, known_types), where)
            continue

        known_fields = _MQTTTHING_TYPES[acc_type]
        for key in acc_cfg:
            if key not in known_fields:
                results.warning("homebridge",
                                f"Unknown field '{key}' for type '{acc_type}'",
                                _suggest(key, known_fields), where)


def lint_homebridge(results: LintResults, section: dict, online: bool = True,
                    config_path: Path | None = None) -> None:
    lint_section(results, "homebridge", section, MODULE_SCHEMAS["homebridge"])
    check_default_client_id(results, section, config_path)

    # port vs ui_port clash
    port = section.get("port")
    ui_port = section.get("ui_port")
    if isinstance(port, int) and isinstance(ui_port, int) and port == ui_port:
        results.error("homebridge", f"'port' and 'ui_port' must be different — both are set to {port}")

    # Plugins
    plugins = section.get("plugins")
    if plugins is not None:
        if not isinstance(plugins, dict):
            results.error("homebridge", "'plugins' must be a mapping of plugin names to their config")
        else:
            lint_homebridge_plugins(results, plugins, online)


def lint_config(config: dict, online: bool = True, config_dir: Path | None = None,
                root: Any = None, config_path: Path | None = None) -> LintResults:
    results = LintResults(root=root)

    # Unknown top-level module names
    for key in config:
        if key not in KNOWN_MODULES:
            suggestion = _suggest(key, KNOWN_MODULES)
            results.warning("(top level)", f"Unknown module '{key}'", suggestion)

    # Required modules
    for required in ("base", "homebridge", "wireguard"):
        if required not in config:
            results.error("(top level)", f"Missing required module section '{required}'")

    # Lint each present module
    for module_name, schema in MODULE_SCHEMAS.items():
        if module_name not in config:
            continue

        raw = config[module_name]
        section = raw if isinstance(raw, dict) else {}

        if raw is not None and not isinstance(raw, dict):
            results.error(module_name, f"Section must be a mapping, got {type(raw).__name__}")
            continue

        if module_name == "homebridge":
            lint_homebridge(results, section, online, config_path)
        else:
            lint_section(results, module_name, section, schema, config_dir=config_dir)

    return results


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------

def _issue_text(issue: Issue) -> Text:
    t = Text()
    if issue.level == "error":
        t.append("  \u2717 ERROR   ", style="bold red")
    elif issue.level == "warning":
        t.append("  \u26a0 WARNING ", style="bold yellow")
    else:
        t.append("  \u2139 NOTE    ", style="bold blue")
    if issue.where:
        t.append(f"[{issue.where}] ", style="cyan")
    t.append(issue.message)
    if issue.suggestion:
        t.append(f" -- {issue.suggestion}", style="dim italic")
    return t


def render_results(results: LintResults, config: dict) -> None:
    # Top-level issues first, then each module in schema order, then any
    # stragglers so that nothing collected is ever silently dropped.
    ordered = ["(top level)"] + list(MODULE_SCHEMAS.keys())
    for module in {i.module for i in results.issues}:
        if module not in ordered:
            ordered.append(module)

    for module in ordered:
        module_issues = results.for_module(module)
        if not module_issues:
            continue
        console.print(Rule(f"[bold]{module}[/bold]", style="dim"))
        for issue in module_issues:
            console.print(_issue_text(issue))
        console.print()


def render_summary(results: LintResults, strict: bool) -> int:
    """Print the summary and return the exit code."""
    console.print(Rule("[bold]Summary[/bold]", style="dim"))
    errors   = len(results.errors)
    warnings = len(results.warnings)

    if errors == 0 and warnings == 0:
        console.print("  [bold green]✓ All good — no issues found.[/bold green]\n")
        return 0

    parts = []
    if errors:
        parts.append(f"[bold red]{errors} error{'s' if errors != 1 else ''}[/bold red]")
    if warnings:
        parts.append(f"[bold yellow]{warnings} warning{'s' if warnings != 1 else ''}[/bold yellow]")
    console.print("  " + ", ".join(parts))

    if errors or (strict and warnings):
        console.print("  [red]Fix errors before running setup.py[/red]\n")
        return 2
    else:
        console.print("  [yellow]Warnings are advisory — setup.py will still run[/yellow]\n")
        return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint a config.yaml for the Pi Home Automation Framework"
    )
    parser.add_argument("config", help="Path to the YAML config file")
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat warnings as errors (exit code 2)"
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="Skip the npm registry lookup for homebridge plugins (no network needed)"
    )
    args = parser.parse_args()

    path = Path(args.config)
    if not path.is_file():
        console.print(f"[bold red]File not found:[/bold red] {args.config}")
        return 2

    console.print(f"\nLinting [bold]{args.config}[/bold]...\n")

    # --- YAML parse ---------------------------------------------------------
    try:
        raw = path.read_text()
        config = yaml.safe_load(raw)
        root = yaml.compose(raw)
    except yaml.YAMLError as exc:
        console.print(f"[bold red]✗ YAML syntax error:[/bold red] {exc}\n")
        return 2

    if config is None:
        console.print("[bold red]✗ Config file is empty[/bold red]\n")
        return 2

    if not isinstance(config, dict):
        console.print(
            f"[bold red]✗ Top level must be a mapping of module names, "
            f"got {type(config).__name__}[/bold red]\n"
        )
        return 2

    console.print("[green]✓ YAML syntax OK[/green]\n")
    if not args.offline:
        console.print("[dim]Checking unmanaged homebridge plugins against npm...[/dim]\n")

    # --- Lint ---------------------------------------------------------------
    results = lint_config(config, online=not args.offline, config_dir=path.resolve().parent,
                          root=root, config_path=path)
    render_results(results, config)
    return render_summary(results, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
