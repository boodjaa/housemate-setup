"""
Sudo timestamp keepalive for long-running install steps.

When a script takes longer than the system's sudo timeout (typically 5–15
minutes, configurable in /etc/sudoers via `Defaults timestamp_timeout`),
any `sudo` call the script makes internally will block waiting for a
password -- which hangs an unattended install indefinitely.

SudoKeepalive solves this by running `sudo -v` in a background thread on
a regular interval. `sudo -v` does nothing except refresh the timestamp;
no command is executed. The context manager ensures the thread is always
stopped when the guarded block exits, whether normally or on exception.

Usage
-----
Wrap any runner.run() call that might take longer than the sudo timeout:

    from core.sudo_keepalive import SudoKeepalive

    with SudoKeepalive(logger=self.logger):
        self.runner.run(["bash", long_script, "--confirm-install"])

The default refresh interval is 60 seconds, which is well within any
realistic sudo timeout. Only use this around genuinely long operations
(source builds, large package installs, external installer scripts) --
it is unnecessary overhead for normal apt-get or systemctl calls.
"""

from __future__ import annotations

import logging
import subprocess
import threading


class SudoKeepalive:
    """Context manager that refreshes the sudo timestamp in the background.

    Args:
        interval : seconds between each `sudo -v` refresh. Default 60 --
                   safe for any timeout >= 2 minutes. Lower values give
                   more headroom at negligible cost.
        logger   : optional logger; keepalive activity is logged at DEBUG
                   level so it appears in the log file but not the UI.
        dry_run  : if True, the refresh thread is started but sudo -v is
                   never actually called. This keeps dry-run output honest
                   (no real sudo interaction) while still exercising the
                   context manager path in tests.
    """

    def __init__(
        self,
        interval: int = 60,
        logger: logging.Logger | None = None,
        dry_run: bool = False,
    ):
        self.interval = interval
        self.logger = logger
        self.dry_run = dry_run
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._refresh_loop, daemon=True)

    def __enter__(self) -> "SudoKeepalive":
        self._thread.start()
        if self.logger:
            self.logger.debug(
                "sudo keepalive started (interval=%ds, dry_run=%s)",
                self.interval, self.dry_run,
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval + 5)
        if self.logger:
            self.logger.debug("sudo keepalive stopped")
        return False  # never suppress exceptions

    def _refresh_loop(self) -> None:
        # Wait `interval` seconds before the first refresh -- the outer
        # sudo session is fresh at this point and doesn't need it yet.
        while not self._stop.wait(self.interval):
            if self.dry_run:
                if self.logger:
                    self.logger.debug("[DRY-RUN] Would run: sudo -v")
                continue
            try:
                result = subprocess.run(
                    ["sudo", "-v"],
                    capture_output=True,
                    timeout=10,
                )
                if self.logger:
                    if result.returncode == 0:
                        self.logger.debug("sudo -v: timestamp refreshed")
                    else:
                        self.logger.warning(
                            "sudo -v returned %d -- sudo session may have expired",
                            result.returncode,
                        )
            except subprocess.TimeoutExpired:
                if self.logger:
                    self.logger.warning("sudo -v timed out -- sudo may be prompting for a password")
            except OSError as exc:
                if self.logger:
                    self.logger.warning("sudo -v failed: %s", exc)
