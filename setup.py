#!/usr/bin/env python3
"""
Raspberry Pi Home Automation Appliance Provisioning Framework
===============================================================

Unattended, idempotent provisioning of a Raspberry Pi into a Homebridge +
WireGuard (+ optional MQTT / PAI / SprinklerD / AqualinkD, in later
iterations) home-automation appliance, driven entirely by a YAML config
file.

Usage:
    sudo ./setup.py config.yaml
    ./setup.py config.yaml --dry-run      # preview with no root, no system changes
    sudo ./setup.py config.yaml --verbose # also echo log lines to the console

This file is intentionally thin: it only orchestrates dependency
resolution, configuration loading, and module lifecycle (validate ->
install -> configure -> enable) per spec section 4. All actual
appliance-specific logic lives in modules/*.py.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rich.console import Console

from core.config import ConfigError, load_config, validate_config
from core.context import Context
from core.dependencies import resolve_dependencies
from core.logger import setup_logger
from core.runner import CommandError, CommandRunner
from core.templates import TemplateRenderer
from core.ui import PHASES, StatusUI
from modules import MODULE_REGISTRY
from modules.base import ModuleError

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SCRIPT_DIR / "templates"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="setup.py",
        description="Raspberry Pi Home Automation Appliance Provisioning Framework",
    )
    parser.add_argument("config", help="Path to the YAML configuration file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would happen: no root required, no packages installed, "
             "no services touched. Rendered files are written under ./dry-run-output/ "
             "instead of their real system paths.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Also echo log lines to the console (in addition to the live status tree).",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Override the log file location (default: /var/log/hub-setup.log, "
             "falling back to ./logs/hub-setup.log if that's not writable).",
    )
    return parser.parse_args(argv)


def run_module(module, ui: StatusUI) -> tuple[bool, Exception | None]:
    """Run all four lifecycle phases for one module, updating the UI as it goes.

    Returns (success, exception). On any phase failure, remaining phases for
    this module are skipped -- there is no point rendering a config for a
    package that failed to install.
    """
    ui.start_module(module.name)
    for phase_name in PHASES:
        ui.start_phase(module.name, phase_name)
        method = getattr(module, phase_name.lower())
        try:
            method()
        except (ModuleError, CommandError, ConfigError) as exc:
            ui.finish_phase(module.name, phase_name, ok=False)
            for remaining in PHASES[PHASES.index(phase_name) + 1:]:
                ui.skip_phase(module.name, remaining)
            ui.finish_module(module.name, ok=False)
            module.logger.error("%s failed during %s: %s", module.name, phase_name, exc)
            return False, exc
        except Exception as exc:  # noqa: BLE001 - a bug in a module must not crash the run
            ui.finish_phase(module.name, phase_name, ok=False)
            for remaining in PHASES[PHASES.index(phase_name) + 1:]:
                ui.skip_phase(module.name, remaining)
            ui.finish_module(module.name, ok=False)
            module.logger.exception("Unexpected error in %s during %s", module.name, phase_name)
            return False, exc
        ui.finish_phase(module.name, phase_name, ok=True)
    ui.finish_module(module.name, ok=True)
    return True, None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console()

    try:
        config = load_config(args.config)
        validate_config(config)
    except ConfigError as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        return 1

    if os.geteuid() != 0 and not args.dry_run:
        console.print(
            "[bold red]This script must be run as root[/bold red] "
            "(try: sudo ./setup.py config.yaml). "
            "Use --dry-run to preview without root."
        )
        return 1

    logger = setup_logger(args.log_file, verbose=args.verbose)
    if args.dry_run:
        logger.info("Running in --dry-run mode: no system changes will be made.")

    enabled_modules = resolve_dependencies(config, logger)
    logger.info("Resolved module set: %s", ", ".join(enabled_modules))

    runner = CommandRunner(logger, dry_run=args.dry_run)
    templates = TemplateRenderer(str(TEMPLATES_DIR), dry_run=args.dry_run)
    ctx = Context(runner=runner, templates=templates, logger=logger, config=config, dry_run=args.dry_run)

    ui = StatusUI(enabled_modules, console=console)
    results: dict[str, bool | None] = {}
    failures: dict[str, Exception] = {}
    wireguard_module = None
    abort = False

    with ui.live():
        for name in enabled_modules:
            module_cls = MODULE_REGISTRY.get(name)
            if module_cls is None:
                ui.skip_module(name, "not yet implemented in this iteration")
                results[name] = None
                continue

            module = module_cls(config.get(name) or {}, ctx)
            ok, exc = run_module(module, ui)
            results[name] = ok
            if name == "wireguard" and ok:
                wireguard_module = module
            if not ok:
                failures[name] = exc
                if getattr(module, "required", False):
                    abort = True
                    break

        if abort:
            started = set(results)
            for name in enabled_modules:
                if name not in started:
                    ui.skip_module(name, "not started - a required module failed")
                    results[name] = None

    ui.print_summary(results)

    if failures:
        console.print("\n[bold]Error details:[/bold]")
        for name, exc in failures.items():
            console.print(f"\n[bold red]{name}[/bold red]:")
            if isinstance(exc, CommandError):
                console.print(f"  command: {exc.cmd}")
                console.print(f"  exit code: {exc.returncode}")
                if exc.stdout.strip():
                    console.print(f"  stdout: {exc.stdout.strip()}")
                if exc.stderr.strip():
                    console.print(f"  stderr: {exc.stderr.strip()}")
            else:
                console.print(f"  {exc}")

    console.print()
    if wireguard_module is not None:
        public_key = getattr(wireguard_module, "_last_public_key", None)
        if public_key:
            console.print(f"[bold]WireGuard Public Key:[/bold] {public_key}")
            console.print(f"  (also saved to ./wireguard_public_key.txt)")

    log_path = logger.handlers[0].baseFilename if logger.handlers else None
    if log_path:
        console.print(f"[dim]Full log: {log_path}[/dim]")
    if args.dry_run:
        console.print("[dim]Dry-run output (rendered configs only, nothing installed): ./dry-run-output/[/dim]")

    if abort:
        return 1
    if failures:
        return 2  # optional module(s) failed, but required modules succeeded
    return 0


if __name__ == "__main__":
    sys.exit(main())
