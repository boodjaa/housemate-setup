"""
Shared run-time context handed to every module.

Bundles the pieces every module needs (the command runner, the template
renderer, the logger, the dry-run flag, and the full resolved config in
case a module needs to read another module's settings) so module
constructors stay simple: Module(settings, ctx).
"""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from core.runner import CommandRunner
from core.templates import TemplateRenderer


@dataclass
class Context:
    runner: CommandRunner
    templates: TemplateRenderer
    logger: Logger
    config: dict
    dry_run: bool = False
