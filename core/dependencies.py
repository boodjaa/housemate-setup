"""
Dependency resolution (spec section 6).

Homebridge and WireGuard are mandatory and always run. The optional modules
(mqtt, pai, sprinklerd, aqualinkd) are not implemented yet in this first
iteration, but the resolution graph is defined here now so that turning one
on later is purely a matter of adding its module class to MODULE_REGISTRY --
no changes to this file or the orchestrator are needed.
"""

from __future__ import annotations

import logging

# Modules that always run, in execution order.
REQUIRED_MODULES: list[str] = ["system", "homebridge", "wireguard"]

# Modules a user can opt into via `<name>: {enabled: true}` in the YAML.
OPTIONAL_MODULES: list[str] = ["mqtt", "pai", "sprinklerd", "aqualinkd"]

# child -> [parents it depends on]. A child is only ever auto-enabled when
# something that needs it is enabled; it is never enabled "just because".
DEPENDENCIES: dict[str, list[str]] = {
    "pai": ["mqtt"],
    "sprinklerd": ["mqtt"],
    "aqualinkd": ["mqtt"],
}


def resolve_dependencies(config: dict, logger: logging.Logger) -> list[str]:
    """Work out the final, dependency-complete, ordered list of modules to run.

    Mutates `config` in place so that any module force-enabled as a
    dependency has an `enabled: true` (and an empty settings dict if it
    didn't have one at all) entry, matching what a module's __init__ expects
    to find.
    """
    enabled: set[str] = set(REQUIRED_MODULES)

    for name in OPTIONAL_MODULES:
        section = config.get(name) or {}
        if section.get("enabled"):
            enabled.add(name)

    # Repeatedly walk the dependency map until a fixed point -- this handles
    # multi-level chains correctly even though today's graph is only one
    # level deep (pai/sprinklerd/aqualinkd -> mqtt).
    changed = True
    while changed:
        changed = False
        for module_name in list(enabled):
            for dependency in DEPENDENCIES.get(module_name, []):
                if dependency in enabled:
                    continue
                existing_section = config.get(dependency)
                explicitly_disabled = (
                    isinstance(existing_section, dict)
                    and existing_section.get("enabled") is False
                )
                if explicitly_disabled:
                    logger.warning(
                        "%s disabled but required as a dependency of %s. Enabling automatically...",
                        dependency, module_name,
                    )
                else:
                    logger.info(
                        "Enabling '%s' automatically (required by '%s')",
                        dependency, module_name,
                    )
                config.setdefault(dependency, {})
                config[dependency]["enabled"] = True
                enabled.add(dependency)
                changed = True

    return _ordered(enabled)


def _ordered(enabled: set[str]) -> list[str]:
    """Return enabled modules in a stable, dependency-respecting order:
    required modules first, then mqtt (a dependency for the rest), then
    everything else alphabetically for determinism.
    """
    order: list[str] = []
    for name in REQUIRED_MODULES:
        if name in enabled:
            order.append(name)
    if "mqtt" in enabled:
        order.append("mqtt")
    for name in sorted(enabled - set(order)):
        order.append(name)
    return order
