"""
Python 3.11 module. Optional -- build CPython 3.11 from source.

Raspberry Pi OS ships Python 3.13 by default; some software (older PAI
versions and various Home Assistant-era integrations being the most
common offenders) still expects 3.11 specifically. This builds CPython
3.11 from source and installs it with `make altinstall`, which
deliberately does NOT touch /usr/bin/python3 or /usr/bin/python -- the
result lands as a separate `python3.11` binary (typically under
/usr/local/bin), leaving the system interpreter untouched.

Building from source takes a long time (15-30+ minutes on a Pi 4), so the
entire point of the idempotency check here is to make sure we only ever
do it once: if any `python3.11` already on PATH reports a 3.11.x version
-- however it got there (apt, a previous run of this module, pyenv,
whatever) -- the build is skipped entirely.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from modules.base import Module, ModuleError

DEFAULT_VERSION = "3.11.15"
PYTHON_BIN = "python3.11"

# The spec's "full optional dependencies" list. `apt-get build-dep python3`
# is attempted first since it's the "correct" way to pull in whatever the
# system's actual Python package was built with, but it depends on deb-src
# lines being uncommented in sources.list, which isn't guaranteed on a
# stock Raspberry Pi OS image -- so it's treated as best-effort, and this
# explicit list is what the build actually relies on.
BUILD_DEPENDENCIES = [
    "build-essential", "gdb", "lcov", "pkg-config",
    "libbz2-dev", "libffi-dev", "libgdbm-dev", "libgdbm-compat-dev",
    "liblzma-dev", "libncurses5-dev", "libreadline-dev", "libsqlite3-dev",
    "libssl-dev", "lzma", "tk-dev", "uuid-dev", "zlib1g-dev",
]

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_PYTHON_VERSION_OUTPUT_RE = re.compile(r"Python (\d+)\.(\d+)\.(\d+)")

# Building Python from source is slow and the steps vary wildly in how
# long they take; these are generous upper bounds so a genuinely hung
# build doesn't block the run forever, without being so tight that a
# slower/thermally-throttled Pi trips them under normal conditions.
DOWNLOAD_TIMEOUT = 600
CONFIGURE_TIMEOUT = 1800
MAKE_TIMEOUT = 7200
ALTINSTALL_TIMEOUT = 1800


class Python311Module(Module):
    name = "python311"
    required = False

    def validate(self) -> None:
        version = self.settings.get("version", DEFAULT_VERSION)
        match = _VERSION_RE.match(version)
        if not match or match.group(1) != "3" or match.group(2) != "11":
            raise ModuleError(
                f"python311.version must be a 3.11.x release (e.g. '3.11.15'), got '{version}'"
            )

    # -- idempotency check, shared by install() and status() ----------------
    def _existing_3_11(self) -> str | None:
        """Return the version string of an already-present python3.11, or
        None if there isn't a working one on PATH. Runs for real even
        during --dry-run -- see core.runner.CommandRunner.query."""
        if not self.runner.command_exists(PYTHON_BIN):
            return None
        result = self.runner.query([PYTHON_BIN, "--version"])
        match = _PYTHON_VERSION_OUTPUT_RE.search(result.stdout + result.stderr)
        if not match or match.group(1) != "3" or match.group(2) != "11":
            return None
        return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"

    # -- install -------------------------------------------------------------
    def install(self) -> None:
        existing = self._existing_3_11()
        if existing:
            self.logger.info(
                "%s (%s) already on PATH, skipping the build entirely", PYTHON_BIN, existing
            )
            return

        version = self.settings.get("version", DEFAULT_VERSION)
        self._install_build_dependencies()
        self._build_and_install(version)

    def _install_build_dependencies(self) -> None:
        # Best-effort: needs deb-src enabled, which a stock image may not have.
        self.runner.run_apt(["build-dep", "-y", "python3"], check=False)
        self.runner.run_apt(["install", "-y", *BUILD_DEPENDENCIES])

    def _build_and_install(self, version: str) -> None:
        url = f"https://www.python.org/ftp/python/{version}/Python-{version}.tgz"
        cpu_count = os.cpu_count() or 1

        with tempfile.TemporaryDirectory(prefix="python311-build-") as tmp:
            tarball = str(Path(tmp) / f"Python-{version}.tgz")
            self.runner.run(["curl", "-sSfL", url, "-o", tarball], timeout=DOWNLOAD_TIMEOUT)
            self.runner.run(["tar", "-xzf", tarball, "-C", tmp])

            src_dir = str(Path(tmp) / f"Python-{version}")
            if not self.dry_run and not Path(src_dir).is_dir():
                raise ModuleError(
                    f"Expected extracted source at {src_dir} but it wasn't there -- "
                    f"the tarball for {version} may not match the expected directory layout"
                )

            self.runner.run(
                ["./configure", "--enable-optimizations"], cwd=src_dir, timeout=CONFIGURE_TIMEOUT
            )
            self.runner.run(["make", f"-j{cpu_count}"], cwd=src_dir, timeout=MAKE_TIMEOUT)
            # altinstall, never plain `install` -- altinstall is what keeps
            # /usr/bin/python3 and /usr/bin/python pointed at the system
            # interpreter Raspberry Pi OS itself depends on. Don't "simplify"
            # this to `install` later; that would clobber the system Python.
            self.runner.run(["make", "altinstall"], cwd=src_dir, timeout=ALTINSTALL_TIMEOUT)

    # -- configure -------------------------------------------------------------
    def configure(self) -> bool:
        # Nothing to render -- this module installs a binary, not a config
        # file. What we do instead is re-verify the interpreter actually
        # works, which catches a build that silently produced a broken
        # binary rather than reporting it as a success.
        version = self._existing_3_11()
        if version:
            self.logger.info("Verified %s reports version %s", PYTHON_BIN, version)
            self._last_version = version
            return False
        if self.dry_run:
            # Expected: the build itself was simulated, not executed, so
            # there's genuinely nothing to verify yet.
            self.logger.info(
                "[DRY-RUN] %s not present (build was simulated, not run) -- expected in a dry run",
                PYTHON_BIN,
            )
            return False
        raise ModuleError(
            f"{PYTHON_BIN} is not on PATH or doesn't report a 3.11.x version after the build -- "
            f"it may have failed silently"
        )

    # -- enable -------------------------------------------------------------
    def enable(self) -> None:
        # No service to enable -- a language runtime isn't a daemon.
        pass

    # -- status -------------------------------------------------------------
    def status(self) -> bool:
        return self._existing_3_11() is not None
