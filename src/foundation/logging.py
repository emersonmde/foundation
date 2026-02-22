"""Structured logging — stderr (concise) + file (full detail)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

LOGGER_NAME = "foundation"


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> logging.Logger:
    """Configure the foundation logger with stderr and optional file handlers.

    Args:
        level: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_dir: Directory for log files. If None, only stderr is used.

    Returns:
        The configured 'foundation' logger.
    """
    logger = logging.getLogger(LOGGER_NAME)
    numeric_level = logging.getLevelNamesMapping().get(level.upper())
    if numeric_level is None:
        raise ValueError(f"Invalid log level: {level}")
    logger.setLevel(numeric_level)

    # Clear existing handlers (idempotent setup)
    logger.handlers.clear()

    # Stderr handler — concise, time only
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(stderr_handler)

    # File handler — full detail with ISO timestamps
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "foundation.log")
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(name)s [%(filename)s:%(lineno)d] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        logger.addHandler(file_handler)

    return logger
