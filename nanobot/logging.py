"""Centralized logging configuration for nanobot.

Uses loguru as the logging backend. Call ``setup_logging()`` once at
startup (CLI entry-points) to configure handlers based on the user's
``LoggingConfig`` or explicit overrides.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.config.schema import LoggingConfig


# ---------------------------------------------------------------------------
# Intercept stdlib logging → loguru
# ---------------------------------------------------------------------------

class _InterceptHandler(logging.Handler):
    """Route stdlib ``logging`` records into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        # Map stdlib level to loguru level name
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find the caller that originated the log call
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_CONFIGURED = False


def setup_logging(
    config: LoggingConfig | None = None,
    *,
    level: str | None = None,
    log_file: str | None = None,
) -> None:
    """Configure loguru handlers for the nanobot process.

    Parameters
    ----------
    config:
        A ``LoggingConfig`` instance (typically from the user's config file).
        If *None*, sensible defaults are used.
    level:
        Override the log level from *config* (e.g. ``"DEBUG"``).  Useful for
        CLI flags like ``--log-level``.
    log_file:
        Override the log file path from *config*.
    """
    global _CONFIGURED

    # Resolve effective values
    effective_level = (level or (config.level if config else None) or "INFO").upper()
    effective_file = log_file or (config.file if config else None) or ""
    effective_format = (
        config.format
        if config
        else (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        )
    )
    effective_rotation = config.rotation if config else "10 MB"
    effective_retention = config.retention if config else "7 days"

    # Remove all existing loguru handlers (including the default stderr one)
    logger.remove()

    # 1. Stderr handler — always present
    logger.add(
        sys.stderr,
        level=effective_level,
        format=effective_format,
        colorize=True,
    )

    # 2. File handler — only when a path is configured
    if effective_file:
        log_path = Path(effective_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # File handler uses a plain (non-coloured) format
        plain_format = (
            "{time:YYYY-MM-DD HH:mm:ss} | "
            "{level: <8} | "
            "{name}:{function}:{line} - "
            "{message}"
        )
        logger.add(
            str(log_path),
            level=effective_level,
            format=plain_format,
            rotation=effective_rotation,
            retention=effective_retention,
            encoding="utf-8",
        )

    # 3. Intercept stdlib logging so third-party libs also go through loguru
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

    # Enable the nanobot logger namespace
    logger.enable("nanobot")

    _CONFIGURED = True


def get_default_log_file() -> Path:
    """Return the default log file path (``~/.nanobot/logs/nanobot.log``)."""
    return Path.home() / ".nanobot" / "logs" / "nanobot.log"
