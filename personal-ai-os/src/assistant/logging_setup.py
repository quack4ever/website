"""Logging: the system's diary.

WHAT IS A LOG?
--------------
A running text file describing what the program did.  When something goes
wrong, this is the first place to look:  `assistant logs`.

Two separate streams, on purpose:
  * this file  -> the *diary*  (debugging detail, rotates, can be noisy)
  * audit.py   -> the *record* (security-relevant facts, append-only, terse)

Secrets are stripped from the diary before anything is written.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from typing import Optional

from . import paths, redact

_CONFIGURED = False
_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"


class _RedactingFilter(logging.Filter):
    """Runs on every log record and blacks out anything secret-shaped."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact.redact_text(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def setup(level: str = "INFO", to_console: bool = False, logfile: Optional[str] = None) -> None:
    """Configure logging once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger("assistant")
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    root.propagate = False
    formatter = logging.Formatter(_FORMAT)
    redactor = _RedactingFilter()

    try:
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        target = logfile or str(paths.app_log_path())
        # Rotate at 5 MB, keep 3 old files, so logs can never fill your disk.
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        handler.addFilter(redactor)
        root.addHandler(handler)
    except OSError as exc:  # disk full, unwritable path, ...
        fallback = logging.StreamHandler(sys.stderr)
        fallback.setFormatter(formatter)
        fallback.addFilter(redactor)
        root.addHandler(fallback)
        root.warning("Could not open log file, logging to stderr instead: %s", exc)

    if to_console:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        console.addFilter(redactor)
        root.addHandler(console)

    _CONFIGURED = True


def get(name: str) -> logging.Logger:
    """Get a logger.  Name it after the module: ``get(__name__)``."""
    if not _CONFIGURED:
        setup()
    short = name.split("assistant.")[-1] if "assistant." in name else name
    return logging.getLogger("assistant." + short)
