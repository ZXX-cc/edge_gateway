"""Command-chain trace log for MQTT -> gateway -> nest commands."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from src.utils.config import Config
from src.utils.logger import DailyMonthlyFileHandler


_LOGGER_NAME = "edge_gateway.command_trace"
_configured = False


def _log_dir() -> str:
    log_config = Config.get("logging", {}) or {}
    base_dir = log_config.get("dir") or log_config.get("base_dir") or "logs"
    return str(Path(base_dir) / "command_trace")


def _logger() -> logging.Logger:
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    handler = DailyMonthlyFileHandler(_log_dir(), "command_trace")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _configured = True
    return logger


def _json_default(value: Any):
    if hasattr(value, "name"):
        return value.name
    return str(value)


def trace_command(event: str, **fields: Any) -> None:
    """Write one JSONL command trace entry.

    This logger is intentionally separate from the normal console logger so
    heartbeat, OSD, and poll noise do not hide command-chain evidence.
    """
    record = {
        "time": datetime.now().isoformat(timespec="milliseconds"),
        "event": event,
    }
    record.update(fields)
    _logger().info(json.dumps(record, ensure_ascii=False, default=_json_default))
