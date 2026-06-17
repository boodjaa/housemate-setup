"""
Shell command execution.

Every external command (apt-get, dpkg, npm, wg, systemctl, ...) goes through
this one chokepoint so we get consistent logging, consistent dry-run
behavior, and the "only show output on error" UI rule from the spec in one
place instead of scattered across every module.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from dataclasses import dataclass, field


class CommandError(Exception):
    """Raised when a command fails and the caller asked us to check=True.

    Carries the captured stdout/stderr so the caller can decide how (or
    whether) to surface it -- per spec, full command output should only be
    shown to the user when something has gone wrong.
    """

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
    ) -> Result:
        """Run a command (list of args, or a string if shell=True).

        In dry-run mode the command is logged but never actually executed,
        and a synthetic successful Result is returned so module logic can
        proceed through its normal control flow during a dry run.
        """
        printable = self._format(cmd)

        if self.dry_run:
            self.logger.info("[DRY-RUN] Would run: %s", printable)
            result = Result(cmd=printable, returncode=0, dry_run=True)
            self.history.append(result)
            return result

        self.logger.debug("Running: %s", printable)
        try:
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
        """Run a read-only status/inspection command for real, even during
        --dry-run (e.g. dpkg-query, systemctl is-active, npm list, wg pubkey).

        --dry-run means "don't make changes", not "don't look at the
        system" -- a status check that always reported success regardless
        of reality would make the preview lie about what's already done.
        Never raises CommandError: callers inspect Result.ok themselves.
        """
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
        """Check dpkg's database for whether a .deb package is installed.

        This is a read-only query, so it runs for real even in dry-run mode
        -- otherwise dry-run could never tell you what's already on the
        system, which defeats the point of a preview.
        """
        result = self.query(["dpkg-query", "-W", "-f=${Status}", package])
        return result.ok and "install ok installed" in result.stdout

    def command_exists(self, name: str) -> bool:
        return self.query(["which", name]).ok
