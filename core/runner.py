"""
Shell command execution.
"""

from __future__ import annotations

import contextlib

import logging
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui import StatusUI  # Avoid circular imports at runtime


class CommandError(Exception):
    def __init__(self, cmd: str, returncode: int, stdout: str, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"Command failed ({returncode}): {cmd}")


@dataclass
class Result:
    cmd: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class CommandRunner:
    logger: logging.Logger
    dry_run: bool = False
    history: list = field(default_factory=list)

    @staticmethod
    def _format(cmd) -> str:
        return cmd if isinstance(cmd, str) else " ".join(shlex.quote(p) for p in cmd)

    def run(
        self,
        cmd,
        check: bool = True,
        shell: bool = False,
        input: str | None = None,
        env: dict | None = None,
        timeout: int | None = None,
        ui: "StatusUI | None" = None,  # Added UI parameter
    ) -> Result:
        """Run a command. If 'ui' is provided, the live display will be 
        suspended during execution to prevent formatting corruption."""
        
        printable = self._format(cmd)

        if self.dry_run:
            self.logger.info("[DRY-RUN] Would run: %s", printable)
            result = Result(cmd=printable, returncode=0, dry_run=True)
            self.history.append(result)
            return result

        self.logger.debug("Running: %s", printable)
        
        # Use the suspend context manager if UI is available
        context = ui.suspend_live() if ui else contextlib.nullcontext()
        
        try:
            with context:
                completed = subprocess.run(
                    cmd,
                    shell=shell,
                    input=input,
                    env=env,
                    timeout=timeout,
                    capture_output=True,
                    text=True,
                )
        except FileNotFoundError as exc:
            result = Result(cmd=printable, returncode=127, stderr=str(exc))
            self.history.append(result)
            if check:
                raise CommandError(printable, 127, "", str(exc)) from exc
            return result
        except subprocess.TimeoutExpired as exc:
            result = Result(cmd=printable, returncode=124, stderr=str(exc))
            self.history.append(result)
            if check:
                raise CommandError(printable, 124, exc.stdout or "", exc.stderr or "") from exc
            return result

        result = Result(
            cmd=printable,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        self.history.append(result)

        if result.ok:
            self.logger.debug("OK: %s", printable)
        else:
            self.logger.error(
                "Command failed (%s): %s\nstdout: %s\nstderr: %s",
                result.returncode, printable, result.stdout, result.stderr,
            )
            if check:
                raise CommandError(printable, result.returncode, result.stdout, result.stderr)

        return result

    def query(self, cmd, timeout: int | None = None, input: str | None = None) -> Result:
        printable = self._format(cmd)
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            result = Result(cmd=printable, returncode=127, stderr=str(exc))
            self.history.append(result)
            return result
        result = Result(
            cmd=printable,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        self.history.append(result)
        return result

    def package_installed(self, package: str) -> bool:
        result = self.query(["dpkg-query", "-W", "-f=${Status}", package])
        return result.ok and "install ok installed" in result.stdout

    def command_exists(self, name: str) -> bool:
        return self.query(["which", name]).ok