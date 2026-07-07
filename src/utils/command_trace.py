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
_EVENT_LABELS = {
    "mqtt_command_received": "MQTT收到命令",
    "mqtt_handler_completed": "网关处理完成",
    "mqtt_reply_sent": "网关回复MQTT",
    "mqtt_error_reply_sent": "网关回复MQTT错误",
    "gateway_nest_command_send": "网关下发机巢",
    "gateway_nest_command_fallback": "网关模拟机巢",
    "gateway_nest_command_unmapped": "网关机巢命令未映射",
    "nest_command_encoded": "网关编码机巢命令",
    "nest_command_mavlink_sent": "网关发送MAVLink",
    "nest_order_response_received": "网关收到机巢回复",
}


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


def _compact_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=_json_default, separators=(",", ":"))
    return str(value)


def trace_command(event: str, **fields: Any) -> None:
    """Write one concise command trace entry.

    This logger is intentionally separate from the normal console logger so
    heartbeat, OSD, and poll noise do not hide command-chain evidence.
    """
    timestamp = datetime.now().isoformat(timespec="milliseconds")
    label = fields.pop("label", _EVENT_LABELS.get(event, event))
    preferred_keys = (
        "method",
        "tid",
        "result",
        "ok",
        "gcs_command",
        "commands",
        "ordinary_order",
        "takeoff_land_order",
        "ordinary_raw",
        "ordinary_reply_status",
        "ordinary_reject_reason_code",
        "takeoff_raw",
        "takeoff_reply_status",
        "takeoff_reject_reason_code",
        "reply_topic",
        "source_topic",
        "topic",
        "reason",
    )
    ordered_keys = [key for key in preferred_keys if key in fields]
    ordered_keys.extend(key for key in fields if key not in ordered_keys)
    detail = " ".join(f"{key}={_compact_value(fields[key])}" for key in ordered_keys)
    _logger().info("%s [%s] %s", timestamp, label, detail)
