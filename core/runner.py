"""
Shell command execution.

Every external command (apt-get, dpkg, npm, wg, systemctl, ...) goes through
this one chokepoint so we get consistent logging, consistent dry-run
behavior, and the "only show output on error" UI rule from the spec in one
place instead of scattered across every module.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui import StatusUI  # Avoid circular imports at runtime


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
        cwd: str | None = None,
        ui: "StatusUI | None" = None,
    ) -> Result:
        """Run a command (list of args, or a string if shell=True).

        In dry-run mode the command is logged but never actually executed,
        and a synthetic successful Result is returned so module logic can
        proceed through its normal control flow during a dry run.
        
        Output is streamed line-by-line to the logger. If the logger has a 
        console handler (verbose mode), this provides live terminal output.
        """
        printable = self._format(cmd)

        if self.dry_run:
            self.logger.info(
                "[DRY-RUN] Would run: %s%s", printable, f" (cwd={cwd})" if cwd else ""
            )
            result = Result(cmd=printable, returncode=0, dry_run=True)
            self.history.append(result)
            return result

        self.logger.info("Running: %s%s", printable, f" (cwd={cwd})" if cwd else "")
        
        # Suspend the Rich Live UI while the command runs to prevent 
        # terminal output from corrupting the display structure.
        context = ui.suspend_live() if ui else contextlib.nullcontext()
        
        try:
            with context:
                # Use Popen to stream output line-by-line
                proc = subprocess.Popen(
                    cmd,
                    shell=shell,
                    stdin=subprocess.PIPE if input else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    cwd=cwd,
                    text=True,      # Decode bytes to str automatically
                    bufsize=1,      # Line buffered
                )
                
                stdout_lines = []
                stderr_lines = []
                
                # Thread to read stderr live without deadlocking
                def read_stderr():
                    for line in proc.stderr:
                        self.logger.info(line.rstrip('\r\n'))
                        stderr_lines.append(line)
                        
                stderr_thread = threading.Thread(target=read_stderr)
                stderr_thread.start()
                
                # Thread to write stdin live without deadlocking
                stdin_thread = None
                if input:
                    def write_stdin():
                        try:
                            proc.stdin.write(input)
                        except BrokenPipeError:
                            pass
                        finally:
                            proc.stdin.close()
                            
                    stdin_thread = threading.Thread(target=write_stdin)
                    stdin_thread.start()
                
                start_time = time.time()
                
                # Stream stdout line-by-line
                for line in proc.stdout:
                    # Enforce timeout manually since we are blocking on readline()
                    if timeout is not None and (time.time() - start_time) > timeout:
                        proc.kill()
                        stderr_thread.join()
                        if stdin_thread:
                            stdin_thread.join()
                        raise subprocess.TimeoutExpired(
                            cmd, timeout, 
                            output="".join(stdout_lines), 
                            stderr="".join(stderr_lines)
                        )
                        
                    # Log the line. If verbose mode is on, this prints to the console live!
                    self.logger.info(line.rstrip('\r\n'))
                    stdout_lines.append(line)
                    
                stderr_thread.join()
                if stdin_thread:
                    stdin_thread.join()
                proc.wait()
                
        except FileNotFoundError as exc:
            result = Result(cmd=printable, returncode=127, stderr=str(exc))
            self.history.append(result)
            if check:
                raise CommandError(printable, 127, "", str(exc)) from exc
            return result
        except subprocess.TimeoutExpired as exc:
            result = Result(
                cmd=printable, 
                returncode=124, 
                stdout=exc.output or "", 
                stderr=exc.stderr or ""
            )
            self.history.append(result)
            if check:
                raise CommandError(printable, 124, result.stdout, result.stderr) from exc
            return result

        stdout_str = "".join(stdout_lines)
        stderr_str = "".join(stderr_lines)

        result = Result(
            cmd=printable,
            returncode=proc.returncode,
            stdout=stdout_str,
            stderr=stderr_str,
        )
        self.history.append(result)

        if result.ok:
            self.logger.info("OK: %s", printable)
            # Output was already streamed live, no need to log it again
        else:
            self.logger.error("Command failed (%s): %s", result.returncode, printable)
            if check:
                raise CommandError(printable, result.returncode, result.stdout, result.stderr)

        return result

    def run_apt(self, args: list, check: bool = True, ui: "StatusUI | None" = None) -> Result:
        """Run an apt-get command in a way that won't fight a live console
        display (e.g. core.ui.StatusUI) for control of the terminal.

        Two apt/dpkg behaviours cause exactly that kind of corruption when
        something else is also managing the terminal:

        - By default, apt allocates its own pseudo-terminal for dpkg so
          dpkg's fancy/colored progress bar renders correctly when apt's
          output is going to a real terminal. That pty and our Live display
          both then write cursor-movement sequences to the *same* physical
          terminal, and neither knows about the other's writes -- `-o
          Dpkg::Use-Pty=0` turns off apt's pty allocation so dpkg just
          writes plain line-based output instead.
        - debconf can still attempt an interactive prompt for some packages
          even with `-y`; DEBIAN_FRONTEND=noninteractive heads that off so
          nothing tries to pop a dialog onto the same terminal mid-install.
        """
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        full_cmd = ["apt-get", "-o", "Dpkg::Use-Pty=0", *args]
        return self.run(full_cmd, check=check, env=env, ui=ui)

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