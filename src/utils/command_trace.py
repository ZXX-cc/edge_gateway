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
_RESET = "\033[0m"
_COLORS = {
    "gateway": "\033[36m",
    "mqtt": "\033[34m",
    "mavlink": "\033[35m",
    "ok": "\033[32m",
    "warn": "\033[33m",
    "error": "\033[31m",
}
_METHOD_ACTIONS = {
    "cover_open": "舱盖打开",
    "cover_close": "舱盖关闭",
    "cover_force_close": "舱盖强制关闭",
    "charge_open": "开始充电",
    "charge_close": "停止充电",
    "putter_open": "推杆打开/夹紧释放",
    "putter_close": "推杆关闭/夹紧",
    "supplement_light_open": "补光灯打开",
    "supplement_light_close": "补光灯关闭",
    "drone_open": "飞机上电",
    "drone_close": "飞机下电",
    "device_reset": "设备复位",
    "device_reboot": "设备重启",
}
_GCS_ACTIONS = {
    "DOOR_OPEN": "舱盖打开",
    "DOOR_CLOSE": "舱盖关闭",
    "DOOR_STOP": "舱盖停止",
    "CHARGE_START": "开始充电",
    "CHARGE_STOP": "停止充电",
    "CLAMP_TIGHTEN": "推杆关闭/夹紧",
    "CLAMP_RELEASE": "推杆打开/夹紧释放",
    "CLAMP_STOP": "推杆停止",
    "ESTOP_ACTIVATE": "急停触发",
    "ESTOP_DEACTIVATE": "急停解除",
    "INITIALIZE": "初始化",
    "FAULT_RECOVERY": "故障恢复",
    "LIGHT_ON": "补光灯打开",
    "LIGHT_OFF": "补光灯关闭",
    "DRONE_POWER_ON": "飞机上电",
    "DRONE_POWER_OFF": "飞机下电",
    "TAKEOFF_START": "起飞流程开始",
    "TAKEOFF_ABORT": "起飞流程终止",
    "LANDING_ABORT": "降落流程终止",
}
_ORDINARY_ACTIONS = {
    0x1101: "舱盖打开",
    0x1102: "舱盖关闭",
    0x1103: "舱盖停止",
    0x0F01: "开始充电",
    0x0F02: "停止充电",
    0x1401: "推杆关闭/夹紧",
    0x1402: "推杆打开/夹紧释放",
    0x1403: "推杆停止",
    0x0A01: "急停触发",
    0x0A02: "急停解除",
    0x0A04: "初始化",
    0x0A05: "故障恢复",
    0x0A06: "补光灯打开",
    0x0A07: "补光灯关闭",
    0x0B01: "飞机上电",
    0x0B02: "飞机下电",
}
_TAKEOFF_ACTIONS = {
    0x01: "起飞流程开始",
    0x02: "起飞流程终止",
    0x04: "降落流程终止",
}
_DETAIL_LABELS = {
    "method": "method",
    "tid": "tid",
    "bid": "bid",
    "result": "result",
    "ok": "发送结果",
    "gcs_command": "GCS",
    "commands": "GCS",
    "ordinary_order": "ordinary",
    "takeoff_land_order": "takeoff",
    "ordinary_raw": "ordinary_response",
    "ordinary_reply_status": "ordinary_status",
    "ordinary_reject_reason_code": "ordinary_reject",
    "takeoff_raw": "takeoff_response",
    "takeoff_reply_status": "takeoff_status",
    "takeoff_reject_reason_code": "takeoff_reject",
    "reply_topic": "reply_topic",
    "source_topic": "source_topic",
    "topic": "topic",
    "reason": "reason",
}


def _log_dir() -> str:
    log_config = Config.get("logging", {}) or {}
    base_dir = log_config.get("dir") or log_config.get("base_dir") or "logs"
    return str(Path(base_dir) / "gateway" / "command_trace")


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


def _color(text: str, color: str) -> str:
    return f"{_COLORS[color]}{text}{_RESET}"


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except ValueError:
        return None


def _command_action_from_fields(fields: dict[str, Any]) -> str:
    method = fields.get("method")
    if method in _METHOD_ACTIONS:
        return _METHOD_ACTIONS[method]

    gcs_command = fields.get("gcs_command")
    if gcs_command in _GCS_ACTIONS:
        return _GCS_ACTIONS[gcs_command]

    commands = fields.get("commands")
    if isinstance(commands, (list, tuple)) and commands:
        actions = [_GCS_ACTIONS.get(str(command), str(command)) for command in commands]
        return " + ".join(actions)

    ordinary_order = _parse_int(fields.get("ordinary_order"))
    if ordinary_order:
        return _ORDINARY_ACTIONS.get(ordinary_order, f"未知普通命令 0x{ordinary_order:04X}")

    takeoff_order = _parse_int(fields.get("takeoff_land_order"))
    if takeoff_order:
        return _TAKEOFF_ACTIONS.get(takeoff_order, f"未知起降命令 0x{takeoff_order:02X}")

    return "未识别命令"


def _reply_status(fields: dict[str, Any]) -> str:
    rejections = fields.get("rejections")
    if rejections:
        return "拒绝"
    ordinary_reject = _parse_int(fields.get("ordinary_reject_reason_code")) or 0
    takeoff_reject = _parse_int(fields.get("takeoff_reject_reason_code")) or 0
    result = _parse_int(fields.get("result"))
    if ordinary_reject or takeoff_reject or (result is not None and result != 0):
        return "失败/拒绝"
    return "成功"


def _format_details(fields: dict[str, Any], keys: tuple[str, ...]) -> str:
    parts = []
    for key in keys:
        if key in fields:
            label = _DETAIL_LABELS.get(key, key)
            parts.append(f"{label}={_compact_value(fields[key])}")
    return "，".join(parts)


def _format_trace_line(timestamp: str, event: str, label: str, fields: dict[str, Any]) -> str:
    action = _command_action_from_fields(fields)

    if event == "mqtt_command_received":
        details = _format_details(fields, ("method", "tid", "topic"))
        return f"{timestamp} {_color('[网关]', 'mqtt')} 收到 MQTT 命令：{action}，{details}"

    if event == "mqtt_handler_completed":
        details = _format_details(fields, ("method", "tid", "result"))
        return f"{timestamp} {_color('[网关]', 'gateway')} 处理完成：{action}，{details}"

    if event in {"mqtt_reply_sent", "mqtt_error_reply_sent"}:
        status_color = "ok" if _reply_status(fields) == "成功" else "error"
        details = _format_details(fields, ("method", "tid", "result", "reply_topic", "ok"))
        return f"{timestamp} {_color('[网关]', status_color)} 回复 MQTT：{action}，{details}"

    if event == "gateway_nest_command_send":
        details = _format_details(fields, ("method", "gcs_command", "property", "value"))
        return f"{timestamp} {_color('[网关]', 'gateway')} 下发机巢命令：{action}，{details}"

    if event == "gateway_nest_command_fallback":
        details = _format_details(fields, ("method", "reason", "property", "value", "simulation"))
        return f"{timestamp} {_color('[网关]', 'warn')} 未走真实机巢，使用模拟处理：{action}，{details}"

    if event == "gateway_nest_command_unmapped":
        details = _format_details(fields, ("method", "property", "value"))
        return f"{timestamp} {_color('[网关]', 'error')} 找不到机巢命令映射：{action}，{details}"

    if event == "nest_command_encoded":
        details = _format_details(fields, ("commands", "ordinary_order", "takeoff_land_order"))
        return f"{timestamp} {_color('[网关]', 'mavlink')} 编码 MAVLink 命令：{action}，{details}"

    if event == "nest_command_mavlink_sent":
        details = _format_details(fields, ("ordinary_order", "takeoff_land_order"))
        return f"{timestamp} {_color('[网关]', 'mavlink')} 发送 MAVLink 命令：{action}，{details}"

    if event == "nest_order_response_received":
        status = _reply_status(fields)
        status_color = "ok" if status == "成功" else "error"
        details = _format_details(fields, (
            "commands",
            "ordinary_order",
            "takeoff_land_order",
            "ordinary_raw",
            "ordinary_reply_status",
            "ordinary_reject_reason_code",
            "takeoff_raw",
            "takeoff_reply_status",
            "takeoff_reject_reason_code",
            "rejections",
        ))
        return f"{timestamp} {_color('[网关]', status_color)} 收到机巢回复：{action}，结果={status}，{details}"

    preferred_keys = (
        "method",
        "tid",
        "result",
        "topic",
        "reason",
    )
    details = _format_details(fields, preferred_keys)
    return f"{timestamp} {_color('[网关]', 'gateway')} {label}：{details}"


def trace_command(event: str, **fields: Any) -> None:
    """Write one concise command trace entry.

    This logger is intentionally separate from the normal console logger so
    heartbeat, OSD, and poll noise do not hide command-chain evidence.
    """
    timestamp = datetime.now().isoformat(timespec="milliseconds")
    label = fields.pop("label", _EVENT_LABELS.get(event, event))
    _logger().info("%s", _format_trace_line(timestamp, event, label, fields))
