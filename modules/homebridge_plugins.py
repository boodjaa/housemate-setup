"""
Homebridge plugin configuration transformers.

Each plugin that supports per-accessory configuration (i.e. plugins where
the same plugin can manage multiple independently-configured accessories,
like homebridge-mqttthing) needs a transformer that converts the friendly
YAML structure from the config file into the exact JSON shape homebridge
expects under its "accessories" key.

Architecture
------------
The split between this file and the Jinja2 template is deliberate:

  Python (here)     : all field mapping, type coercion, URL construction,
                      and hardcoded per-type defaults. Output is a plain
                      Python list of dicts.

  Jinja2 template   : only structural JSON rendering. It receives the
                      pre-built list and does `{{ accessories | tojson }}`.

This keeps the Jinja2 template free of type-specific if/else chains and
makes adding a new accessory type a data change (add a schema entry below)
rather than a code change in the template.

Adding a new plugin
-------------------
1. Define a TYPE_SCHEMA dict mapping YAML keys to homebridge JSON keys.
2. Implement a transform_<plugin>_accessories() function.
3. Register it in PLUGIN_TRANSFORMERS at the bottom of this file.
"""

from __future__ import annotations

import secrets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_username() -> str:
    """Generate a MAC-address-shaped 'username' for the homebridge bridge.

    The high bit pattern (02) marks it as a locally-administered address,
    matching the convention homebridge itself uses when it self-generates one.
    """
    octets = [0x02] + [secrets.randbits(8) for _ in range(5)]
    return ":".join(f"{o:02X}" for o in octets)

def _to_list(value) -> list:
    """Accept either a YAML list or a comma-separated string.

    Homebridge YAML configs frequently have array-valued fields, and users
    sometimes write them as a comma-separated string rather than a proper
    YAML list (especially when copying from homebridge documentation that
    shows them that way). Accept both so the config isn't picky about
    which style the user picked.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip().rstrip(",") for v in value]
    return [v.strip().rstrip(",") for v in str(value).split(",") if v.strip()]


def _to_int_list(value) -> list[int]:
    """Same as _to_list, but coerces each element to int.

    Used for fields like restrictTargetState where homebridge expects
    integers, not strings, in the JSON array.
    """
    return [int(v) for v in _to_list(value)]


def _mqtt_url(host: str) -> str:
    """Turn a bare host[:port] into a mqtt:// URL.

    homebridge-mqttthing expects a full URL in the `url` field. The YAML
    config lets users write just `127.0.0.1:1883` (or `localhost:1883`)
    without the scheme since it's less noisy; we add the scheme here.
    """
    if host.startswith("mqtt://") or host.startswith("mqtts://"):
        return host
    return f"mqtt://{host}"


# ---------------------------------------------------------------------------
# mqttthing schema
# ---------------------------------------------------------------------------
#
# Each TYPE_SCHEMA entry describes how to map the YAML keys for that
# accessory type into the homebridge JSON shape:
#
#   topics   : { yaml_key -> homebridge topics sub-key }
#               These end up nested under "topics": { ... } in the output.
#   arrays   : { yaml_key -> homebridge top-level key }
#               These are list-valued fields at the top level of the accessory.
#   int_arrays: same as arrays, but values are coerced to int.
#   hardcoded: { homebridge_key -> value }
#               Fields that are always present for this type regardless of
#               what the user put in the YAML (e.g. onValue/offValue for
#               motionSensor, which homebridge-mqttthing always needs but
#               which never need to differ between installations).

_MQTTTHING_TYPE_SCHEMAS: dict[str, dict] = {
    "securitySystem": {
        "topics": {
            "current_state": "getCurrentState",
            "get_target":    "getTargetState",
            "set_target":    "setTargetState",
        },
        "arrays":     {
            "target_values":  "targetStateValues",
            "current_values": "currentStateValues",
        },
        "int_arrays": {
            "restrict_state": "restrictTargetState",
        },
        "hardcoded": {},
    },
    "motionSensor": {
        "topics": {
            "motion_detected": "getMotionDetected",
        },
        "arrays":     {},
        "int_arrays": {},
        "hardcoded": {
            "onValue":  "True",
            "offValue": "False",
        },
    },
    "contactSensor": {
        "topics": {
            "contact_sensor_state": "getContactSensorState",
        },
        "arrays":     {},
        "int_arrays": {},
        "hardcoded": {
            "onValue":  "True",
            "offValue": "False",
        },
    },
    "switch": {
        "topics": {
            "get_on": "getOn",
            "set_on": "setOn",
        },
        "arrays":     {},
        "int_arrays": {},
        "hardcoded": {},
    },
    "lightbulb": {
        "topics": {
            "get_on":         "getOn",
            "set_on":         "setOn",
            "get_brightness": "getBrightness",
            "set_brightness": "setBrightness",
        },
        "arrays":     {},
        "int_arrays": {},
        "hardcoded": {},
    },
}


def _transform_mqttthing_accessory(acc_cfg: dict, url: str) -> dict:
    """Convert a single accessory block from the YAML into its homebridge
    JSON representation for homebridge-mqttthing.

    acc_cfg : the dict under accessories.<key> in the YAML
    url     : the mqtt:// URL, shared across all accessories in this plugin
              instance (derived from the plugin-level `host` key)
    """
    acc_type = acc_cfg.get("type")
    schema = _MQTTTHING_TYPE_SCHEMAS.get(acc_type)
    if schema is None:
        raise ValueError(
            f"Unknown mqttthing accessory type '{acc_type}'. "
            f"Known types: {', '.join(_MQTTTHING_TYPE_SCHEMAS)}"
        )

    out: dict = {
        "accessory": "mqttthing",
        "type":      acc_type,
        "name":      acc_cfg["name"],
        "url":       url,
    }

    # Build the topics sub-object from whichever topic keys are present
    topics = {}
    for yaml_key, hb_key in schema["topics"].items():
        if yaml_key in acc_cfg:
            topics[hb_key] = acc_cfg[yaml_key]
    if topics:
        out["topics"] = topics

    # String-valued arrays
    for yaml_key, hb_key in schema["arrays"].items():
        if yaml_key in acc_cfg:
            out[hb_key] = _to_list(acc_cfg[yaml_key])

    # Integer-valued arrays
    for yaml_key, hb_key in schema["int_arrays"].items():
        if yaml_key in acc_cfg:
            out[hb_key] = _to_int_list(acc_cfg[yaml_key])

    # Hardcoded per-type defaults
    out.update(schema["hardcoded"])

    return out


def transform_mqttthing_accessories(plugin_cfg: dict) -> list[dict]:
    """Transform a full homebridge-mqttthing plugin block (as parsed from
    the YAML) into a list of accessory dicts ready to be inserted into
    homebridge's config.json `accessories` array.

    plugin_cfg is the dict under `plugins.homebridge-mqttthing` (or
    whatever key the user used) in the YAML.
    """
    host = plugin_cfg.get("host", "127.0.0.1:1883")
    url = _mqtt_url(str(host))
    accessories_cfg = plugin_cfg.get("accessories", {}) or {}
    result = []
    for acc_key, acc_cfg in accessories_cfg.items():
        if not isinstance(acc_cfg, dict):
            raise ValueError(
                f"Accessory '{acc_key}' must be a mapping of settings, got {type(acc_cfg).__name__}"
            )
        result.append(_transform_mqttthing_accessory(acc_cfg, url))
    return result


# Google Nest SDM
def transform_google_nest_sdm(plugin_cfg: dict) -> list[dict]:
    return [{
        "clientId": plugin_cfg["clientId"],
        "clientSecret": plugin_cfg["clientSecret"],
        "projectId": plugin_cfg["projectId"],
        "refreshToken": plugin_cfg["refreshToken"],
        "subscriptionId": plugin_cfg["subscriptionId"],
        "showFan": False,
        "fanDuration": 900,
        "platform": "homebridge-google-nest-sdm",
        "_bridge": {
            "username": _generate_username(),
            "port": 55505,
            "name": "Homebridge Google Nest Sdm"
        }
    }]

# Shelly
def transform_shelly(plugin_cfg: dict) -> list[dict]:
    return [{
        "name": "Shelly",
        "admin": {
            "enabled": True
        },
        "_bridge": {
            "username": _generate_username(),
            "port": 48366
        }
    }]

# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------
#
# Two separate registries -- one for plugins whose output goes into
# homebridge's "accessories" array, one for plugins whose output goes into
# "platforms". homebridge.py's configure() checks both when iterating the
# enabled plugins list.
#
# Plugins not in either registry are simple install-only plugins (e.g.
# homebridge-shelly): they just need `hb-service add <name>` and contribute
# nothing to the config JSON, so the homebridge module handles them without
# calling into this file at all.

ACCESSORY_TRANSFORMERS: dict[str, callable] = {
    "homebridge-mqttthing": transform_mqttthing_accessories,
}

PLATFORM_TRANSFORMERS: dict[str, callable] = {
    "homebridge-google-nest-sdm": transform_google_nest_sdm,
    "homebridge-shelly": transform_shelly
}
