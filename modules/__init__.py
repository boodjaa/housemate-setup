"""
Module registry.

MODULE_REGISTRY is the single place the orchestrator looks to turn a
resolved module name into a class to instantiate. This first iteration
only implements the two mandatory modules; mqtt/pai/sprinklerd/aqualinkd
are intentionally left out (the orchestrator treats any enabled module
absent from this registry as "not yet implemented" and reports it as
skipped rather than crashing) -- when a future iteration adds e.g.
modules/mqtt.py with an MqttModule class, registering it here is the
*only* change needed; core/setup.py does not change.
"""

from __future__ import annotations

from modules.homebridge import HomebridgeModule
from modules.wireguard import WireGuardModule

MODULE_REGISTRY: dict[str, type] = {
    "homebridge": HomebridgeModule,
    "wireguard": WireGuardModule,
    # "mqtt": MqttModule,            # not yet implemented
    # "pai": PaiModule,               # not yet implemented
    # "sprinklerd": SprinklerDModule, # not yet implemented
    # "aqualinkd": AqualinkDModule,   # not yet implemented
}

NOT_YET_IMPLEMENTED = {"mqtt", "pai", "sprinklerd", "aqualinkd"} - set(MODULE_REGISTRY)
