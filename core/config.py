"""
Configuration loading and validation.

The framework is driven entirely by a single YAML file. This module is
responsible for turning that file into a plain Python dict and making sure
it contains everything the enabled modules will need before we touch the
system in any way.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised for any problem with the user-supplied configuration file."""


def load_config(path: str) -> dict[str, Any]:
    """Load and parse the YAML configuration file.

    Raises ConfigError on missing files or invalid YAML, instead of letting
    a raw exception escape -- this keeps main() able to print a single,
    clean error message.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")

    try:
        raw = config_path.read_text()
    except OSError as exc:
        raise ConfigError(f"Could not read configuration file '{path}': {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in '{path}': {exc}") from exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"Top level of '{path}' must be a mapping of module names to settings, "
            f"got {type(data).__name__}"
        )

    return data


# --- Schema validation -----------------------------------------------------
#
# Deliberately hand-rolled rather than pulling in jsonschema/pydantic: the
# validation needs are small, and keeping dependencies minimal keeps the
# "easier testing" rationale from the spec intact. Each module also exposes
# its own validate() for module-specific deep checks; this function only
# covers the structural/required-field checks needed before dependency
# resolution can run.

_PIN_PATTERN = re.compile(r"^\d{3}-\d{2}-\d{3}$")


def _require(section: dict, key: str, where: str) -> Any:
    if key not in section or section[key] in (None, ""):
        raise ConfigError(f"Missing required field '{key}' in '{where}' section")
    return section[key]


def _require_type(value: Any, expected_type: type, field: str, where: str) -> None:
    if not isinstance(value, expected_type):
        raise ConfigError(
            f"Field '{field}' in '{where}' must be of type {expected_type.__name__}, "
            f"got {type(value).__name__}"
        )


def validate_config(config: dict[str, Any]) -> None:
    """Validate the structural shape of the config before dependency
    resolution and module execution. Raises ConfigError on the first
    problem found.
    """
    if "homebridge" not in config:
        raise ConfigError(
            "Missing required 'homebridge' section (homebridge is a mandatory module)"
        )
    if "wireguard" not in config:
        raise ConfigError(
            "Missing required 'wireguard' section (wireguard is a mandatory module)"
        )

    homebridge = config["homebridge"]
    _require_type(homebridge, dict, "homebridge", "homebridge")
    _require(homebridge, "bridge_name", "homebridge")
    port = _require(homebridge, "port", "homebridge")
    _require_type(port, int, "port", "homebridge")
    if not (1 <= port <= 65535):
        raise ConfigError(f"homebridge.port must be between 1 and 65535, got {port}")
    pin = _require(homebridge, "pin", "homebridge")
    _require_type(pin, str, "pin", "homebridge")
    if not _PIN_PATTERN.match(pin):
        raise ConfigError(
            f"homebridge.pin must look like 'XXX-XX-XXX' (digits only), got '{pin}'"
        )

    plugins = homebridge.get("plugins", {})
    if plugins:
        _require_type(plugins, dict, "plugins", "homebridge")
        for plugin_name, plugin_cfg in plugins.items():
            _require_type(plugin_cfg, dict, plugin_name, "homebridge.plugins")
            if "enabled" not in plugin_cfg:
                raise ConfigError(
                    f"homebridge.plugins.{plugin_name} is missing 'enabled'"
                )

    wireguard = config["wireguard"]
    _require_type(wireguard, dict, "wireguard", "wireguard")
    _require(wireguard, "endpoint", "wireguard")
    _require(wireguard, "server_pubkey", "wireguard")
    address = wireguard.get("address", "10.10.0.2/24")
    _require_type(address, str, "address", "wireguard")

    # Optional-module sections are only sanity-checked for type here; their
    # own modules (when implemented) own the deep validation. This keeps the
    # framework forward-compatible without needing changes to this file.
    for optional in ("mqtt", "pai", "sprinklerd", "aqualinkd"):
        if optional in config and config[optional] is not None:
            _require_type(config[optional], dict, optional, optional)
