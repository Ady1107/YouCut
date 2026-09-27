"""
YouCut — Logging configuration.

Sets up rotating file handler and console handler for application-wide logging.
Log file is stored at ~/.youcut/youcut.log with rotation at 5MB, keeping 3 backups.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_log_directory() -> Path:
    """Get the directory where log files are stored."""
    log_dir = Path.home() / ".youcut"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def setup_logging(level: int = logging.DEBUG) -> logging.Logger:
    """
    Configure application-wide logging with rotating file and console handlers.

    Args:
        level: The minimum logging level. Defaults to DEBUG.

    Returns:
        The root logger configured for YouCut.
    """
    logger = logging.getLogger("youcut")
    logger.setLevel(level)

    # Prevent duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    log_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)-8s] [%(name)s.%(funcName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler — 5MB per file, keep 3 backups
    log_file = get_log_directory() / "youcut.log"
    file_handler = RotatingFileHandler(
        str(log_file),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(log_format)
    logger.addHandler(file_handler)

    # Console handler — INFO and above for dev mode
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)
    logger.addHandler(console_handler)

    logger.info("Logging initialized. Log file: %s", log_file)
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a child logger under the youcut namespace.

    Args:
        name: Module name for the child logger (e.g., 'downloader', 'validator').

    Returns:
        A logger instance scoped under 'youcut.<name>'.
    """
    return logging.getLogger(f"youcut.{name}")
