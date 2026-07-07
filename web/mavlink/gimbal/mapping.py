"""云台模块 MAVLink 协议映射。

对应《MAVLink协议字段映射文档》第八部分：
- 8.1 云台角度控制
- 8.2 云台模式配置
- 8.3 目标点指向
- 8.4 云台能力范围
"""
from __future__ import annotations

from typing import Any, Dict, Optional


MAV_CMD_DO_SET_ROI_LOCATION = 195
MAV_CMD_DO_MOUNT_CONFIGURE = 204
MAV_CMD_DO_MOUNT_CONTROL = 205


MOUNT_MODE_MAP = {
    "RESET": 0,
    "FPV": 2,
    "FREE": 3,
    "FOLLOW": 4,
}


GIMBAL_COMMAND_MAP: Dict[str, Dict[str, Any]] = {
    "gimbalRotate": {
        "mavlink_command": "MAV_CMD_DO_MOUNT_CONTROL",
        "command_id": MAV_CMD_DO_MOUNT_CONTROL,
        "fallback": "viewlink.turn_to/viewlink.move",
    },
    "gimbal_reset": {
        "mavlink_command": "MAV_CMD_DO_MOUNT_CONTROL",
        "command_id": MAV_CMD_DO_MOUNT_CONTROL,
        "fallback": "viewlink.home",
    },
    "set_gimbal_mode": {
        "mavlink_command": "MAV_CMD_DO_MOUNT_CONFIGURE",
        "command_id": MAV_CMD_DO_MOUNT_CONFIGURE,
        "fallback": None,
    },
    "camera_look_at": {
        "mavlink_command": "MAV_CMD_DO_SET_ROI_LOCATION",
        "command_id": MAV_CMD_DO_SET_ROI_LOCATION,
        "fallback": None,
    },
}


GIMBAL_CAPABILITY = {
    "max_pitch_speed": 180,
    "max_yaw_speed": 180,
    "pitch_range": [-90, 30],
    "yaw_range": [-180, 180],
}


def build_gimbal_command(method: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把云台 API 方法转换成 MAVLink COMMAND_LONG 参数。"""
    data = payload or {}
    spec = GIMBAL_COMMAND_MAP[method]

    return {
        "method": method,
        "fallback": spec["fallback"],
        "mavlink": {
            "command": spec["command_id"],
            "command_name": spec["mavlink_command"],
            "params": _gimbal_command_params(method, data),
        },
    }


def _gimbal_command_params(method: str, data: Dict[str, Any]) -> list[float]:
    if method == "gimbalRotate":
        pitch = _clamp(float(data.get("pitch", 0.0)), -90.0, 30.0)
        roll = float(data.get("roll", 0.0))
        yaw = float(data.get("yaw", 0.0))
        mount_mode = MOUNT_MODE_MAP.get(str(data.get("mode", "RESET")).upper(), MOUNT_MODE_MAP["RESET"])
        return [pitch * 100, roll * 100, yaw * 100, 0, 0, 0, mount_mode]

    if method == "gimbal_reset":
        return [0, 0, 0, 0, 0, 0, MOUNT_MODE_MAP["RESET"]]

    if method == "set_gimbal_mode":
        mode = str(data.get("mode", "FOLLOW")).upper()
        return [MOUNT_MODE_MAP.get(mode, MOUNT_MODE_MAP["FOLLOW"]), 0, 0, 0, 0, 0, 0]

    if method == "camera_look_at":
        return [
            0,
            0,
            0,
            0,
            float(data.get("latitude", 0.0)),
            float(data.get("longitude", 0.0)),
            float(data.get("height", data.get("altitude", 0.0))),
        ]

    raise KeyError(f"Unsupported gimbal method: {method}")


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
