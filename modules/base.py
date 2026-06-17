"""
Module package: every appliance feature (homebridge, wireguard, and later
mqtt/pai/sprinklerd/aqualinkd) is a self-contained subclass of Module.

This is the contract from spec section 5. The orchestrator in setup.py only
ever calls these five methods, in this order, on whatever modules
dependency resolution decided should run -- it never needs to know
anything about homebridge's apt repo or wireguard's key generation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.context import Context


class ModuleError(Exception):
    """Raised by a module when one of its phases cannot complete."""


class Module(ABC):
    #: short machine name, matches the YAML section / registry key
    name: str = "module"
    #: True for homebridge/wireguard -- a failure here aborts the whole run
    required: bool = False

    def __init__(self, settings: dict, ctx: Context):
        self.settings = settings or {}
        self.ctx = ctx

    # -- convenience accessors used by subclasses ------------------------
    @property
    def runner(self):
        return self.ctx.runner

    @property
    def templates(self):
        return self.ctx.templates

    @property
    def logger(self):
        return self.ctx.logger

    @property
    def dry_run(self) -> bool:
        return self.ctx.dry_run

    # -- the contract every module must implement ------------------------
    @abstractmethod
    def validate(self) -> None:
        """Raise ModuleError if this module's settings are unusable.

        Structural checks (required keys present, right types) already
        happened in core.config.validate_config before we got here; this
        is for module-specific deep checks (e.g. a value only this module
        understands).
        """

    @abstractmethod
    def install(self) -> None:
        """Install required packages/binaries. Must be safe to rerun:
        check current state first and skip work that's already done."""

    @abstractmethod
    def configure(self) -> None:
        """Render and write configuration files. Must be safe to rerun:
        only write when content actually changed."""

    @abstractmethod
    def enable(self) -> None:
        """Enable (and, if config changed, restart) the relevant service(s)."""

    @abstractmethod
    def status(self) -> bool:
        """Return True if this module is already fully installed, configured,
        and enabled -- used for the "skip if already installed" idempotency
        the orchestrator surfaces in the status display."""
