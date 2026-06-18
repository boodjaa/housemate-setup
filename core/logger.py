"""
Logging setup.

Per the spec, all activity (INFO/WARNING/ERROR) is recorded to
/var/log/hub-setup.log. The live, human-facing status display is handled
separately by core.ui.StatusUI -- this logger is the durable audit trail,
not the thing the user watches scroll by.
"""

from __future__ import annotations

import logging
from pathlib import Path


DEFAULT_LOG_PATH = "/var/log/hub-setup.log"
FALLBACK_LOG_PATH = "./logs/hub-setup.log"


def setup_logger(log_path: str | None = None, verbose: bool = False) -> logging.Logger:
    """Configure and return the framework-wide logger.

    Always logs to a file. If the preferred location isn't writable (e.g.
    running without root, or during --dry-run on a non-Pi machine), falls
    back to ./logs/hub-setup.log so the run is still recorded.
    """
    logger = logging.getLogger("House-Mate Setup")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    target_path = log_path or DEFAULT_LOG_PATH
    file_handler = None
    for candidate in (target_path, FALLBACK_LOG_PATH):
        try:
            path = Path(candidate)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(path)
            break
        except OSError:
            continue

    if file_handler is not None:
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)
        logger.info("Install started.")
        logger.info("Logging initialized -> %s", file_handler.baseFilename)
    else:
        logger.addHandler(logging.NullHandler())

    if verbose:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.DEBUG)
        logger.addHandler(console_handler)

    return logger
