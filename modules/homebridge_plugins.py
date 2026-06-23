"""
Homebridge plugin configuration transformers.

Architecture
------------
Two mechanisms for turning a YAML plugin block into homebridge JSON:

  PluginSchema  : declarative, covers ~90% of plugins. Define the
                  plugin_key, _bridge config, and any hardcoded fields.
                  All other YAML fields are passed through with
                  snake_case → camelCase conversion automatically.

  Custom function: for plugins that need type dispatch (mqttthing),
                  per-item hardcoded defaults (Denon TV), or other logic
                  that can't be expressed in a flat schema.

Both types live in the same ACCESSORY_TRANSFORMERS / PLATFORM_TRANSFORMERS
registries and are invoked via apply_plugin().

Adding a new plugin
-------------------
Simple (flat or has a list collection):
  1. Add a PluginSchema entry to the registry at the bottom.
  2. Done. snake_case YAML fields are camelCased automatically.

Complex (type dispatch, per-item defaults, nested logic):
  1. Write a transform_<name>() function returning list[dict].
  2. Register the function in the registry.

YAML convention
---------------
Write plugin fields in snake_case; they are converted to camelCase.
For fields a plugin spells in non-standard case (e.g. Camera-ffmpeg's
`tlsmqtt`) write the key exactly as it should appear in the JSON --
keys without underscores pass through unchanged.
"""

from __future__ import annotations

import copy
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_username() -> str:
    octets = [0x02] + [secrets.randbits(8) for _ in range(5)]
    return ":".join(f"{o:02X}" for o in octets)


def _to_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip().rstrip(",") for v in value]
    return [v.strip().rstrip(",") for v in str(value).split(",") if v.strip()]


def _to_int_list(value) -> list[int]:
    return [int(v) for v in _to_list(value)]


def _mqtt_url(host: str) -> str:
    if host.startswith("mqtt://") or host.startswith("mqtts://"):
        return host
    return f"mqtt://{host}"


def _snake_to_camel(s: str) -> str:
    if not s == "temperature_unit":
        parts = s.split("_")
        return parts[0] + "".join(p.capitalize() for p in parts[1:])
    return s


def _camel_dict(d: dict) -> dict:
    """Recursively convert all dict keys from snake_case to camelCase.
    Scalar values (including booleans) pass through unchanged so tojson
    renders them as JSON true/false rather than "True"/"False".
    """
    out = {}
    for k, v in d.items():
        key = _snake_to_camel(k)
        if isinstance(v, dict):
            out[key] = _camel_dict(v)
        elif isinstance(v, list):
            out[key] = [_camel_dict(i) if isinstance(i, dict) else i for i in v]
        else:
            out[key] = v
    return out


# ---------------------------------------------------------------------------
# Declarative schema types
# ---------------------------------------------------------------------------

@dataclass
class PluginSchema:
    """Declarative configuration for a homebridge plugin.

    plugin_key  : value of the 'platform' or 'accessory' JSON field.
    hardcoded   : fields always written to the output regardless of what the
                  user provides in the YAML. Takes priority over user values.
                  Use sparingly -- only for fields that must never be changed
                  (e.g. Shelly's admin.enabled).
    dict_to_list: YAML keys whose values are dicts (name → config) that
                  need to become JSON lists. Most plugins use YAML lists
                  directly and don't need this.

    Child bridge
    ------------
    Set `child_bridge: true` under the plugin in config.yaml to add a
    homebridge child bridge (_bridge block) to that plugin's config entry.
    A unique username is auto-generated; optionally pin it with
    `bridge_username: "AA:BB:CC:DD:EE:FF"` for stability after HomeKit
    pairing. Additional _bridge fields:
      bridge_port: 12345        # optional port
      bridge_name: "My Bridge"  # optional display name
      bridge_pin:  "123-45-678" # optional HomeKit pin (some platforms)
    """
    plugin_key: str
    hardcoded: dict = field(default_factory=dict)
    dict_to_list: list[str] = field(default_factory=list)


# Keys stripped from plugin_cfg before passing through to homebridge --
# these are framework-level settings, not homebridge config fields.
_SKIP = frozenset({
    "enabled",
    "child_bridge",
    "bridge_username", "bridge_port", "bridge_name", "bridge_pin",
})


def _build_child_bridge(plugin_cfg: dict) -> dict | None:
    """Return a _bridge dict if child_bridge: true, otherwise None."""
    if not plugin_cfg.get("child_bridge"):
        return None
    bridge: dict[str, Any] = {
        "username": plugin_cfg.get("bridge_username") or _generate_username()
    }
    if plugin_cfg.get("bridge_port") is not None:
        bridge["port"] = plugin_cfg["bridge_port"]
    if plugin_cfg.get("bridge_name") is not None:
        bridge["name"] = plugin_cfg["bridge_name"]
    if plugin_cfg.get("bridge_pin") is not None:
        bridge["pin"] = plugin_cfg["bridge_pin"]
    return bridge


def _transform_from_schema(
    plugin_cfg: dict,
    schema: PluginSchema,
    output: str,        # "accessories" or "platforms"
) -> list[dict]:
    """Apply a PluginSchema to produce a homebridge config dict.

    All user fields (minus framework keys) are passed through with
    snake_case → camelCase conversion. Hardcoded fields are then applied
    on top. The platform/accessory key is always set last.
    """
    user_fields = {k: v for k, v in plugin_cfg.items() if k not in _SKIP}
    out = _camel_dict(user_fields)

    # Convert any dict-keyed YAML collections to JSON lists
    for yaml_key in schema.dict_to_list:
        camel_key = _snake_to_camel(yaml_key)
        raw = out.get(camel_key)
        if isinstance(raw, dict):
            out[camel_key] = [
                _camel_dict(v) if isinstance(v, dict) else v
                for v in raw.values()
            ]

    # Hardcoded values win over user-provided values
    out.update(schema.hardcoded)

    # The platform/accessory identifier always wins
    id_key = "accessory" if output == "accessories" else "platform"
    out[id_key] = schema.plugin_key

    # Child bridge -- only present when explicitly requested
    bridge = _build_child_bridge(plugin_cfg)
    if bridge is not None:
        out["_bridge"] = bridge

    return [out]


def apply_plugin(
    plugin_name: str,
    plugin_cfg: dict,
    accessory_registry: dict,
    platform_registry: dict,
) -> tuple[str | None, list[dict]]:
    """Resolve and apply a plugin from one of the two registries.

    Returns (output_type, results) where output_type is 'accessories'
    or 'platforms', or (None, []) if the plugin is not registered
    (install-only plugin with no config contribution).

    Does NOT catch exceptions -- the caller (homebridge.configure)
    wraps this in a try/except and raises a ModuleError.
    """
    if plugin_name in accessory_registry:
        handler = accessory_registry[plugin_name]
        output = "accessories"
    elif plugin_name in platform_registry:
        handler = platform_registry[plugin_name]
        output = "platforms"
    else:
        return None, []

    if isinstance(handler, PluginSchema):
        return output, _transform_from_schema(plugin_cfg, handler, output)
    # Custom callable
    return output, handler(plugin_cfg)


# ---------------------------------------------------------------------------
# mqttthing -- custom transformer (needs per-type topic dispatch)
# ---------------------------------------------------------------------------

_MQTTTHING_TYPE_SCHEMAS: dict[str, dict] = {
    "securitySystem": {
        "topics": {
            "current_state": "getCurrentState",
            "get_target":    "getTargetState",
            "set_target":    "setTargetState",
        },
        "arrays":     {"target_values": "targetStateValues", "current_values": "currentStateValues"},
        "int_arrays": {"restrict_state": "restrictTargetState"},
        "hardcoded":  {},
    },
    "motionSensor": {
        "topics":     {"motion_detected": "getMotionDetected"},
        "arrays":     {},
        "int_arrays": {},
        "hardcoded":  {"onValue": "True", "offValue": "False"},
    },
    "contactSensor": {
        "topics":     {"contact_sensor_state": "getContactSensorState"},
        "arrays":     {},
        "int_arrays": {},
        "hardcoded":  {"onValue": "True", "offValue": "False"},
    },
    "switch": {
        "topics":     {"get_on": "getOn", "set_on": "setOn"},
        "arrays":     {},
        "int_arrays": {},
        "hardcoded":  {},
    },
    "lightbulb": {
        "topics":     {"get_on": "getOn", "set_on": "setOn",
                       "get_brightness": "getBrightness", "set_brightness": "setBrightness"},
        "arrays":     {},
        "int_arrays": {},
        "hardcoded":  {},
    },
}


def _transform_mqttthing_accessory(acc_cfg: dict, url: str) -> dict:
    acc_type = acc_cfg.get("type")
    schema = _MQTTTHING_TYPE_SCHEMAS.get(acc_type)
    if schema is None:
        raise ValueError(
            f"Unknown mqttthing accessory type '{acc_type}'. "
            f"Known types: {', '.join(_MQTTTHING_TYPE_SCHEMAS)}"
        )
    out: dict = {"accessory": "mqttthing", "type": acc_type, "name": acc_cfg["name"], "url": url}
    topics = {hb: acc_cfg[yk] for yk, hb in schema["topics"].items() if yk in acc_cfg}
    if topics:
        out["topics"] = topics
    for yk, hb in schema["arrays"].items():
        if yk in acc_cfg:
            out[hb] = _to_list(acc_cfg[yk])
    for yk, hb in schema["int_arrays"].items():
        if yk in acc_cfg:
            out[hb] = _to_int_list(acc_cfg[yk])
    out.update(schema["hardcoded"])
    return out


def transform_mqttthing_accessories(plugin_cfg: dict) -> list[dict]:
    url = _mqtt_url(str(plugin_cfg.get("host", "127.0.0.1:1883")))
    result = []
    for acc_key, acc_cfg in (plugin_cfg.get("accessories") or {}).items():
        if not isinstance(acc_cfg, dict):
            raise ValueError(f"Accessory '{acc_key}' must be a mapping")
        result.append(_transform_mqttthing_accessory(acc_cfg, url))
    return result


# ---------------------------------------------------------------------------
# Denon TV -- custom transformer (per-device hardcoded defaults)
# ---------------------------------------------------------------------------

_DENON_DEVICE_DEFAULTS: dict = {
    "port":            8080,
    "generation":      1,
    "zoneControl":     0,
    "refreshInterval": 5,
    "inputs": {
        "getFromDevice": True,
        "getFavoritesFromDevice": False,
        "getQuickSmartSelectFromDevice": False,
        "displayOrder": 1,
    },
    "surrounds": {"displayOrder": 0},
    "log":    {"deviceInfo": True, "success": True, "info": False,
                "warn": True, "error": True, "debug": False},
    "restFul": {"enable": False},
    "mqtt":    {"enable": False, "auth": {"enable": False}},
}

_DENON_TOP_FIELDS = [
    # (yaml_key, hb_key, default)   None default = omit if absent
    ("name",                "name",              None),
    ("host",                "host",              None),
    ("zone_control",        "zoneControl",       0),
    ("generation",          "generation",        1),
    ("info_button_command", "infoButtonCommand", None),
]
_DENON_NESTED = [("power", "power"), ("volume", "volume")]
_DENON_BUTTON_LISTS = [("buttons", "buttons"), ("buttons_z2", "buttonsZ2"), ("buttons_z3", "buttonsZ3")]


def _transform_denon_device(dev_cfg: dict) -> dict:
    out = copy.deepcopy(_DENON_DEVICE_DEFAULTS)
    for yk, hk, default in _DENON_TOP_FIELDS:
        if yk in dev_cfg:
            out[hk] = dev_cfg[yk]
        elif default is not None:
            out[hk] = default
    if "name" not in out or "host" not in out:
        missing = [k for k in ("name", "host") if k not in dev_cfg]
        raise ValueError(f"Denon TV device missing required field(s): {', '.join(missing)}")
    for yk, hk in _DENON_NESTED:
        if yk in dev_cfg and isinstance(dev_cfg[yk], dict):
            out[hk] = _camel_dict(dev_cfg[yk])
    for yk, hk in _DENON_BUTTON_LISTS:
        if yk in dev_cfg and isinstance(dev_cfg[yk], list):
            out[hk] = [_camel_dict(b) if isinstance(b, dict) else b for b in dev_cfg[yk]]
    return out


def transform_denon_tv_platform(plugin_cfg: dict) -> list[dict]:
    devices_cfg = plugin_cfg.get("devices") or {}
    if not isinstance(devices_cfg, dict):
        raise ValueError("homebridge-denon-tv: 'devices' must be a mapping")
    devices = []
    for dev_key, dev_cfg in devices_cfg.items():
        if not isinstance(dev_cfg, dict):
            raise ValueError(f"homebridge-denon-tv: device '{dev_key}' must be a mapping")
        devices.append(_transform_denon_device(dev_cfg))
    out: dict[str, Any] = {
        "platform": "DenonTv",
        "name":     plugin_cfg.get("name", "Denon TV"),
        "devices":  devices,
    }
    bridge = _build_child_bridge(plugin_cfg)
    if bridge is not None:
        out["_bridge"] = bridge
    return [out]


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------
#
# Values can be either a PluginSchema (declarative, preferred for simple
# plugins) or a callable (custom transformer, for complex logic).
# apply_plugin() handles both transparently.
#
# Child bridges: add `child_bridge: true` under ANY plugin in config.yaml
# to opt that plugin into a homebridge child bridge. No schema changes
# needed. See PluginSchema docstring for the full set of bridge_* keys.

ACCESSORY_TRANSFORMERS: dict[str, PluginSchema | Callable] = {
    "homebridge-mqttthing":    transform_mqttthing_accessories,  # type dispatch
    "homebridge-daikin-local": PluginSchema("Daikin-Local"),
    "homebridge-ueboom":       PluginSchema("UEBoomSpeaker"),
}

PLATFORM_TRANSFORMERS: dict[str, PluginSchema | Callable] = {
    # ---- custom transformers ------------------------------------------------
    "homebridge-denon-tv":         transform_denon_tv_platform,  # per-device defaults

    # ---- flat plugins -------------------------------------------------------
    "homebridge-2-0-shelly":           PluginSchema("homebridge-2-0-shelly.Shelly", hardcoded={"admin": {"enabled": True}}),
    "homebridge-kasa-python":      PluginSchema("KasaPython"),
    "homebridge-google-smarthome": PluginSchema("google-smarthome"),
    "homebridge-landroid":         PluginSchema("Lannœdroid"),
    "homebridge-appletv-enhanced": PluginSchema("AppleTVEnhanced"),
    "homebridge-google-nest-sdm":  PluginSchema("homebridge-google-nest-sdm"),
    "homebridge-broadlink-rm":     PluginSchema("BroadlinkRM"),
    "homebridge-connexoon":        PluginSchema("Connexoon"),
    "homebridge-tahoma":           PluginSchema("Tahoma"),

    # ---- plugins with list collections -------------------------------------
    "homebridge-camera-ffmpeg":    PluginSchema("Camera-ffmpeg"),
    "homebridge-plex":             PluginSchema("Plex"),
    "homebridge-weather-plus":     PluginSchema("WeatherPlus"),
    "homebridge-network-presence": PluginSchema("NetworkPresence"),
    "homebridge-tplink-smarthome": PluginSchema("TplinkSmarthome"),
    "homebridge-tapo":             PluginSchema("HomebridgeTPLinkTapo"),
    "homebridge-samsung-tizen":    PluginSchema("SamsungTizen"),
    "homebridge-hisense-tv":       PluginSchema("HiSenseTV"),
}
