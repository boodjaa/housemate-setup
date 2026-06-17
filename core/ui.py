"""
Console status display (spec "Niceties" section).
"""

from __future__ import annotations

import contextlib
import time
from enum import Enum, auto

from rich.console import Console, ConsoleOptions, RenderResult
from rich.live import Live
from rich.text import Text
from rich.tree import Tree

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
PHASES = ("Validate", "Install", "Configure", "Enable")


class PhaseState(Enum):
    PENDING = auto()
    RUNNING = auto()
    DONE = auto()
    FAILED = auto()
    SKIPPED = auto()


class _DynamicTree:
    """
    A wrapper that forces Rich's Live display to re-evaluate the tree 
    on every refresh tick, rather than rendering a static snapshot.
    """
    def __init__(self, ui: "StatusUI"):
        self.ui = ui

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield self.ui._render()


class StatusUI:
    def __init__(self, module_names: list[str], console: Console | None = None):
        self.console = console or Console()
        self.module_names = list(module_names)
        self.module_state: dict[str, PhaseState] = {n: PhaseState.PENDING for n in module_names}
        self.module_reason: dict[str, str] = {}
        self.phase_state: dict[tuple[str, str], PhaseState] = {
            (n, p): PhaseState.PENDING for n in module_names for p in PHASES
        }
        self._live: Live | None = None

    # -- lifecycle -----------------------------------------------------
    def live(self) -> Live:
        self._live = Live(
            _DynamicTree(self), 
            console=self.console, 
            refresh_per_second=10, 
            transient=False
        )
        return self._live

    @contextlib.contextmanager
    def suspend_live(self):
        """
        Temporarily pause the Live display to allow raw terminal output 
        (like apt-get logs) to render without breaking the UI structure.
        """
        if self._live is not None:
            with self._live.suspend():
                yield
        else:
            yield

    def _refresh(self) -> None:
        pass

    # -- state transitions ----------------------------------------------
    def start_module(self, name: str) -> None:
        self.module_state[name] = PhaseState.RUNNING
        self._refresh()

    def finish_module(self, name: str, ok: bool) -> None:
        self.module_state[name] = PhaseState.DONE if ok else PhaseState.FAILED
        self._refresh()

    def skip_module(self, name: str, reason: str) -> None:
        self.module_state[name] = PhaseState.SKIPPED
        self.module_reason[name] = reason
        for phase in PHASES:
            self.phase_state[(name, phase)] = PhaseState.SKIPPED
        self._refresh()

    def start_phase(self, name: str, phase: str) -> None:
        self.phase_state[(name, phase)] = PhaseState.RUNNING
        self._refresh()

    def finish_phase(self, name: str, phase: str, ok: bool = True) -> None:
        self.phase_state[(name, phase)] = PhaseState.DONE if ok else PhaseState.FAILED
        self._refresh()

    def skip_phase(self, name: str, phase: str) -> None:
        self.phase_state[(name, phase)] = PhaseState.SKIPPED
        self._refresh()

    # -- rendering --------------------------------------------------------
    @staticmethod
    def _icon(state: PhaseState) -> Text:
        if state is PhaseState.DONE:
            return Text("✓", style="bold green")
        if state is PhaseState.FAILED:
            return Text("✗", style="bold red")
        if state is PhaseState.RUNNING:
            frame = SPINNER_FRAMES[int(time.time() * 8) % len(SPINNER_FRAMES)]
            return Text(frame, style="bold cyan")
        if state is PhaseState.SKIPPED:
            return Text("–", style="dim")
        return Text("∟", style="dim")

    def _render(self) -> Tree:
        root = Tree("[bold]Installation Status:[/bold]", guide_style="dim")
        for name in self.module_names:
            state = self.module_state[name]
            label = Text()
            label.append_text(self._icon(state))
            label.append(" ")
            label.append(name.replace("_", " ").title(), style="bold" if state != PhaseState.PENDING else "")
            if state is PhaseState.SKIPPED and name in self.module_reason:
                label.append(f"  ({self.module_reason[name]})", style="dim italic")
            branch = root.add(label)
            for phase in PHASES:
                phase_state = self.phase_state[(name, phase)]
                phase_label = Text()
                phase_label.append_text(self._icon(phase_state))
                phase_label.append(" ")
                phase_label.append(phase)
                branch.add(phase_label)
        return root

    def print_summary(self, results: dict[str, bool]) -> None:
        self.console.print()
        self.console.print("[bold]Installation Summary:[/bold]")
        for name in self.module_names:
            state = self.module_state[name]
            if state is PhaseState.DONE:
                self.console.print(f"  [bold green]SUCCESS[/bold green]  {name}")
            elif state is PhaseState.SKIPPED:
                reason = self.module_reason.get(name, "skipped")
                self.console.print(f"  [dim]SKIPPED[/dim]  {name} ({reason})")
            elif state is PhaseState.FAILED:
                self.console.print(f"  [bold red]FAILED[/bold red]   {name}")
            else:
                self.console.print(f"  [dim]PENDING[/dim]  {name}")