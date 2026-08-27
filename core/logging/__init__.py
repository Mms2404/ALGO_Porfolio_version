"""
Single logging factory. Per-category rotating file loggers under logs/<category>/.

Replaces the old four modules (account / celery / trade / websocket).
Improvements over the originals:
  - RotatingFileHandler (size-based, continuous) instead of the trade logger's
    line-trim, which only ran at creation so files grew unbounded during a run.
  - propagate=False so these file loggers don't also print to the root console.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_FMT = "%(asctime)s - %(levelname)s - %(message)s"
_MAX_BYTES = 2_000_000   # ~2 MB per file
_BACKUPS = 3


def get_logger(
    category: str,
    name: str,
    *,
    fmt: str = _DEFAULT_FMT,
    datefmt: str | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    log_dir = Path(os.getenv("LOG_DIR", "./logs")) / category
    log_dir.mkdir(parents=True, exist_ok=True)
    file_path = log_dir / f"{category}_{name}.log"

    logger = logging.getLogger(f"{category}_{name}")
    logger.setLevel(level)
    if not logger.handlers:
        handler = RotatingFileHandler(file_path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS)
        handler.setFormatter(logging.Formatter(fmt, datefmt))
        logger.addHandler(handler)
        logger.propagate = False
    return logger


# --- convenience wrappers (same names/behaviour as the old modules) ---
def get_account_logger(client_id: str) -> logging.Logger:
    return get_logger("account", client_id)


def get_celery_logger(task_name: str) -> logging.Logger:
    return get_logger("celery", task_name)


def get_trade_logger(trade_id: str) -> logging.Logger:
    return get_logger(
        "trade", trade_id,
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_ws_logger(client_id: str) -> logging.Logger:
    return get_logger(
        "websocket", client_id,
        fmt="%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def now_us() -> str:
    """Timestamp with microsecond precision (websocket tick timing)."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
