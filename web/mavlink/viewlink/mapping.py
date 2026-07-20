"""ViewLink SDK 云台控制接口映射。

对应《MAVLink协议字段映射文档》第九部分。
本模块只生成 SDK 调用描述，不加载 SDK 动态库。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


CONN_TYPE_MAP = {
    "serial": 0,
    "tcp": 1,
    "udp": 2,
}


SENSOR_MAP = {
    "VISIBLE1": 0,
    "IR": 1,
    "VISIBLE_IR": 2,
    "IR_VISIBLE": 3,
    "VISIBLE2": 4,
}


RECORD_MODE_MAP = {
    "NONE": 0,
    "PHOTO": 1,
    "RECORD": 2,
}

FOCUS_MODE_MAP = {
    "AUTO": 0,
    "AUTO_FOCUS": 0,
    "AFC": 0,
    "MANUAL": 1,
    "MANUAL_FOCUS": 1,
}

LASER_ZOOM_MODE_MAP = {
    "FOLLOW_EO": 0,
    "MANUAL": 1,
}


VIEWLINK_METHOD_MAP: Dict[str, Dict[str, Any]] = {
    "initialize": {"sdk_method": "initialize", "params": ("sdk_dir",)},
    "connect": {"sdk_method": "connect", "params": ("ip", "port", "conn_type")},
    "disconnect": {"sdk_method": "disconnect", "params": ()},
    "is_connected": {"sdk_method": "is_connected", "params": ()},
    "move": {"sdk_method": "move", "params": ("yaw_speed", "pitch_speed")},
    "stop": {"sdk_method": "stop", "params": ()},
    "home": {"sdk_method": "home", "params": ()},
    "turn_to": {"sdk_method": "turn_to", "params": ("yaw", "pitch")},
    "zoom_in": {"sdk_method": "zoom_in", "params": ("speed",)},
    "zoom_out": {"sdk_method": "zoom_out", "params": ("speed",)},
    "stop_zoom": {"sdk_method": "stop_zoom", "params": ()},
    "zoom_to": {"sdk_method": "zoom_to", "params": ("magnification",)},
    "focus_in": {"sdk_method": "focus_in", "params": ("speed",)},
    "focus_out": {"sdk_method": "focus_out", "params": ("speed",)},
    "stop_focus": {"sdk_method": "stop_focus", "params": ()},
    "set_focus_mode": {"sdk_method": "set_focus_mode", "params": ("mode",)},
    "get_focus_mode": {"sdk_method": "get_focus_mode", "params": ()},
    "switch_eo_digital_zoom": {"sdk_method": "switch_eo_digital_zoom", "params": ("on",)},
    "ir_digital_zoom_in": {"sdk_method": "ir_digital_zoom_in", "params": ("speed",)},
    "ir_digital_zoom_out": {"sdk_method": "ir_digital_zoom_out", "params": ("speed",)},
    "switch_defog": {"sdk_method": "switch_defog", "params": ("on",)},
    "is_defog_on": {"sdk_method": "is_defog_on", "params": ()},
    "switch_laser": {"sdk_method": "switch_laser", "params": ("on",)},
    "laser_single": {"sdk_method": "laser_single", "params": ()},
    "laser_zoom_in": {"sdk_method": "laser_zoom_in", "params": ("speed",)},
    "laser_zoom_out": {"sdk_method": "laser_zoom_out", "params": ("speed",)},
    "laser_stop_zoom": {"sdk_method": "laser_stop_zoom", "params": ()},
    "set_laser_zoom_mode": {"sdk_method": "set_laser_zoom_mode", "params": ("mode",)},
    "start_track": {"sdk_method": "start_track", "params": ("x", "y", "width", "height", "template_size", "sensor")},
    "stop_track": {"sdk_method": "stop_track", "params": ()},
    "is_tracking": {"sdk_method": "is_tracking", "params": ()},
    "take_photo": {"sdk_method": "take_photo", "params": ()},
    "start_recording": {"sdk_method": "start_recording", "params": ()},
    "stop_recording": {"sdk_method": "stop_recording", "params": ()},
    "is_recording": {"sdk_method": "is_recording", "params": ()},
    "set_record_mode": {"sdk_method": "set_record_mode", "params": ("mode",)},
    "get_record_mode": {"sdk_method": "get_record_mode", "params": ()},
}


def build_viewlink_call(method: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把文档中的 ViewLink SDK 方法规范化成调用描述。"""
    data = payload or {}
    spec = VIEWLINK_METHOD_MAP[method]
    args = {name: _normalize_value(name, data.get(name)) for name in spec["params"]}

    return {
        "method": method,
        "sdk_method": spec["sdk_method"],
        "args": args,
    }


def _normalize_value(name: str, value: Any) -> Any:
    if name in ("yaw_speed", "pitch_speed"):
        return _clamp_int(value or 0, -16383, 16383)
    if name == "speed":
        return _clamp_int(value or 5, 1, 8)
    if name == "conn_type":
        return CONN_TYPE_MAP.get(str(value or "tcp").lower(), CONN_TYPE_MAP["tcp"])
    if name == "sensor":
        return SENSOR_MAP.get(str(value or "VISIBLE1").upper(), SENSOR_MAP["VISIBLE1"])
    if name == "mode":
        key = str(value if value is not None else "NONE").upper()
        if key in RECORD_MODE_MAP:
            return RECORD_MODE_MAP[key]
        if key in FOCUS_MODE_MAP:
            return FOCUS_MODE_MAP[key]
        if key in LASER_ZOOM_MODE_MAP:
            return LASER_ZOOM_MODE_MAP[key]
        return int(value or 0)
    if name == "on":
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}
    if name == "magnification":
        return max(1.0, float(value or 1.0))
    if name in ("x", "y", "width", "height", "template_size", "port"):
        return int(value or 0)
    if name in ("yaw", "pitch"):
        return float(value or 0.0)
    return value


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))
