"""Centralized logging configuration.

Logs go to stderr and to a rotating file under the data dir. Secrets are never
logged anywhere in this codebase; the config object intentionally never prints
the API key (see config.py).
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_CONFIGURED = False


def setup_logging(logs_dir: Path | None = None, level: str = "INFO") -> logging.Logger:
    """Configure root logging once. Safe to call repeatedly (idempotent)."""
    global _CONFIGURED
    logger = logging.getLogger("jobagent")
    if _CONFIGURED:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            logs_dir / "jobagent.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    logger.propagate = False
    _CONFIGURED = True
    return logger


def get_logger(name: str = "jobagent") -> logging.Logger:
    """Return a namespaced child logger (e.g. 'jobagent.source')."""
    return logging.getLogger(name if name.startswith("jobagent") else f"jobagent.{name}")
