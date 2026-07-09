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
    "mqtt_no_handler": "未注册业务处理器",
    "mqtt_invalid_topic": "Topic不匹配",
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
    # Dock3 properties/device/organization
    "property_set": "属性设置",
    "property_get": "属性查询",
    "update_topo": "设备拓扑更新",
    "device_binding": "设备绑定",
    "device_unbinding": "设备解绑",
    "device_lock": "设备锁定",
    "device_unlock": "设备解锁",
    "airport_bind_status": "机场绑定状态查询",
    "airport_organization_get": "组织信息查询",
    "airport_organization_bind": "组织绑定",
    "workspace_get": "工作空间查询",
    "workspace_bind_status": "工作空间绑定状态查询",
    "device_capability_query": "设备能力查询",
    "dock_status_query": "机场状态查询",
    # Dock3 command/debug
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
    "drone_format": "飞行器格式化",
    "device_format": "设备格式化",
    "device_reset": "设备复位",
    "device_reboot": "设备重启",
    "format_sd_card": "格式化 SD 卡",
    "debug_mode_open": "开启远程调试",
    "debug_mode_close": "关闭远程调试",
    "battery_maintenance_switch": "电池保养开关",
    "air_conditioner_mode_switch": "空调模式切换",
    "alarm_state_switch": "声光报警开关",
    "battery_store_mode_switch": "电池运行模式切换",
    "sdr_workmode_switch": "图传链路模式切换",
    "sim_slot_switch": "SIM 卡槽切换",
    "esim_activate": "eSIM 激活",
    "esim_operator_switch": "eSIM 运营商切换",
    "rtk_calibration": "RTK 校准",
    "alternate_land_point_set": "备降点设置",
    "rtk_ref_points_set": "RTK 参考点设置",
    "rtk_ref_points_query": "RTK 参考点查询",
    "device_exit_rtk_mode": "退出 RTK 模式",
    "device_enter_simulation_mode": "进入仿真模式",
    "device_exit_simulation_mode": "退出仿真模式",
    "device_calibration": "设备校准",
    # DRC/flight
    "set_flight_mode": "设置飞行模式",
    "arm": "飞行器解锁",
    "disarm": "飞行器上锁",
    "takeoff": "飞行器起飞",
    "land": "飞行器降落",
    "return_home": "飞行器返航",
    "drc_mode_enter": "进入 DRC 模式",
    "drc_mode_exit": "退出 DRC 模式",
    "heart_beat": "DRC 心跳",
    "drc_heartbeat": "DRC 心跳",
    "stick_control": "DRC 杆量控制",
    "drone_control": "DRC 飞行器控制",
    "payload_control": "DRC 负载控制",
    "drc_speed_push": "DRC 速度控制",
    "drc_heading_push": "DRC 航向控制",
    "drc_altitude_push": "DRC 高度控制",
    "drc_flyto_point_push": "DRC 指点飞行推送",
    "drc_gimbal_speed_push": "DRC 云台速度控制",
    "takeoff_to_point": "起飞到点",
    "fly_to_point_update": "指点飞行更新",
    "fly_to_point_stop": "指点飞行停止",
    # Wayline/mission
    "flighttask_prepare": "航线任务准备",
    "flighttask_execute": "航线任务执行",
    "flighttask_pause": "航线任务暂停",
    "flighttask_recovery": "航线任务恢复",
    "flighttask_progress": "航线任务进度",
    "flighttask_return_home": "航线任务返航",
    "flighttask_return_home_cancel": "取消返航",
    "flighttask_undo": "航线任务撤销",
    "load_wayline": "加载航线",
    "upload_wayline": "上传航线",
    "execute_task": "执行任务",
    "cancel_task": "取消任务",
    "pause_wayline": "暂停航线",
    "resume_wayline": "恢复航线",
    "take_off": "起飞",
    "flyto": "飞向目标点",
    # Media/live/log/HMS
    "file_list_get": "媒体文件列表查询",
    "file_delete": "媒体文件删除",
    "adjust_upload_priority": "调整上传优先级",
    "highest_priority_upload_flighttask_media": "航线媒体最高优先级上传",
    "upload_flighttask_media_prioritize": "航线媒体优先上传",
    "storage_config_get": "存储配置查询",
    "live_capability_query": "直播能力查询",
    "live_start": "开始直播",
    "live_stop": "停止直播",
    "live_set_quality": "设置直播画质",
    "live_lens_change": "切换直播镜头",
    "set_livestream_quality": "设置直播画质",
    "set_livestream_lens": "切换直播镜头",
    "hms_get": "HMS 告警查询",
    "hms_clear": "HMS 告警清除",
    "device_log_list_get": "设备日志列表查询",
    "device_log_upload": "设备日志上传",
    "fileupload_list": "文件上传列表查询",
    "fileupload_start": "文件上传开始",
    "fileupload_update": "文件上传状态更新",
    # Camera/gimbal/payload
    "camera_mode_switch": "相机模式切换",
    "camera_photo_take": "相机拍照",
    "camera_recording_start": "相机开始录像",
    "camera_recording_stop": "相机停止录像",
    "camera_focal_length_set": "相机焦距设置",
    "camera_point_focus_action": "相机点对焦",
    "camera_exposure_mode_set": "相机曝光模式设置",
    "camera_focus_mode_set": "相机对焦模式设置",
    "camera_focus_value_set": "相机焦点值设置",
    "gimbal_rotate": "云台转动",
    "camera_look_at": "相机看向目标",
    "set_gimbal_mode": "设置云台模式",
    "gimbal_reset": "云台复位",
    "payload_authority_grab": "负载控制权抢占",
    "payload_authority_release": "负载控制权释放",
    # Firmware/config
    "ota_create": "固件升级任务创建",
    "ota_cancel": "固件升级取消",
    "firmware_upgrade": "固件升级任务创建",
    "firmware_upgrade_cancel": "固件升级取消",
    "config_update": "配置更新",
    "config_get": "配置查询",
    "config_rollback": "配置回滚",
    "config": "配置查询/更新",
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
    "msg": "msg",
    "error": "error",
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

    if event == "mqtt_no_handler":
        details = _format_details(fields, ("method", "tid", "topic", "error"))
        return f"{timestamp} {_color('[网关]', 'warn')} 未注册业务处理器：{action}，{details}"

    if event == "mqtt_invalid_topic":
        details = _format_details(fields, ("method", "tid", "topic", "reason"))
        return f"{timestamp} {_color('[网关]', 'warn')} Topic 不匹配：{action}，{details}"

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
