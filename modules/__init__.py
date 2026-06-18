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

from modules.sysconfig import SysconfigModule
from modules.homebridge import HomebridgeModule
from modules.wireguard import WireGuardModule
from modules.mqtt import MqttModule
from modules.python311 import Python311Module
from modules.pai import PaiModule

MODULE_REGISTRY: dict[str, type] = {
    "base": SysconfigModule,
    "homebridge": HomebridgeModule,
    "wireguard": WireGuardModule,
    "mqtt": MqttModule,
    "pai": PaiModule,
    "python311": Python311Module,
    # "sprinklerd": SprinklerDModule, # not yet implemented
    # "aqualinkd": AqualinkDModule,   # not yet implemented
}

NOT_YET_IMPLEMENTED = {"sprinklerd", "aqualinkd"} - set(MODULE_REGISTRY)
