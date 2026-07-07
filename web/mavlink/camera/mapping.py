"""相机模块 MAVLink 协议映射。

对应《MAVLink协议字段映射文档》第七部分：
- 7.1 相机控制命令
- 7.2 相机参数设置
- 7.3 媒体文件元数据

本模块只生成映射后的命令/参数/元数据，不发送 MAVLink，不写数据库。
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional


MAV_CMD_IMAGE_START_CAPTURE = 2000
MAV_CMD_IMAGE_STOP_CAPTURE = 2001
MAV_CMD_VIDEO_START_CAPTURE = 2500
MAV_CMD_VIDEO_STOP_CAPTURE = 2501
MAV_CMD_DO_DIGICAM_CONTROL = 203


CAMERA_COMMAND_MAP: Dict[str, Dict[str, Any]] = {
    "camera_photo_take": {
        "mavlink_command": "MAV_CMD_DO_DIGICAM_CONTROL",
        "command_id": MAV_CMD_DO_DIGICAM_CONTROL,
        "fallback": "viewlink.take_photo",
        "description": "单次拍照，param5=1 触发拍照",
    },
    "start_photo": {
        "mavlink_command": "MAV_CMD_IMAGE_START_CAPTURE",
        "command_id": MAV_CMD_IMAGE_START_CAPTURE,
        "fallback": None,
        "description": "开始拍照/连拍/定时拍照",
    },
    "stop_photo": {
        "mavlink_command": "MAV_CMD_IMAGE_STOP_CAPTURE",
        "command_id": MAV_CMD_IMAGE_STOP_CAPTURE,
        "fallback": None,
        "description": "停止拍照",
    },
    "start_record": {
        "mavlink_command": "MAV_CMD_VIDEO_START_CAPTURE",
        "command_id": MAV_CMD_VIDEO_START_CAPTURE,
        "fallback": "viewlink.start_recording",
        "description": "开始录像，param2=帧率",
    },
    "stop_record": {
        "mavlink_command": "MAV_CMD_VIDEO_STOP_CAPTURE",
        "command_id": MAV_CMD_VIDEO_STOP_CAPTURE,
        "fallback": "viewlink.stop_recording",
        "description": "停止录像",
    },
}


CAMERA_COMMAND_ALIASES = {
    "camera_recording_start": "start_record",
    "camera_recording_stop": "stop_record",
    "camera_photo_stop": "stop_photo",
}


CAMERA_SETTING_MAP: Dict[str, Dict[str, Any]] = {
    "set_camera_mode": {"field": "mode", "allowed": ("photo", "video", "playback")},
    "set_exposure_mode": {"field": "mode", "allowed": ("auto", "manual", "shutter_priority", "aperture_priority")},
    "camera_exposure_mode_set": {
        "alias": "set_exposure_mode",
        "field": "mode",
        "allowed": ("auto", "manual", "shutter_priority", "aperture_priority"),
    },
    "zoom": {"fields": ("zoom_ratio", "zoom_direction", "zoom_speed")},
    "camera_focal_length_set": {"alias": "zoom", "fields": ("zoom_ratio", "zoom_direction", "zoom_speed")},
    "set_focus_mode": {"field": "mode", "allowed": ("auto_focus", "manual_focus", "AFC")},
    "camera_focus_mode_set": {"alias": "set_focus_mode", "field": "mode", "allowed": ("auto_focus", "manual_focus", "AFC")},
    "set_focus_value": {"field": "focus_distance"},
    "camera_focus_value_set": {"alias": "set_focus_value", "field": "focus_distance"},
    "spot_focus": {"fields": ("x", "y")},
    "camera_point_focus_action": {"alias": "spot_focus", "fields": ("x", "y")},
    "set_shutter_speed": {"field": "shutter_speed"},
    "set_iso": {"field": "iso"},
    "set_white_balance": {"field": "white_balance"},
    "set_aperture": {"field": "aperture"},
    "set_resolution": {"field": "resolution", "allowed": ("720p", "1080p", "4k")},
    "set_frame_rate": {"field": "frame_rate", "allowed": (30,)},
    "set_video_encoding": {"field": "encoding", "allowed": ("h264",)},
}


def build_camera_command(method: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把上云 API 相机方法转换成 MAVLink COMMAND_LONG 参数。"""
    data = payload or {}
    canonical_method = CAMERA_COMMAND_ALIASES.get(method, method)
    spec = CAMERA_COMMAND_MAP[canonical_method]

    return {
        "method": method,
        "canonical_method": canonical_method,
        "fallback": spec["fallback"],
        "mavlink": {
            "command": spec["command_id"],
            "command_name": spec["mavlink_command"],
            "params": _camera_command_params(canonical_method, data),
        },
        "description": spec["description"],
    }


def build_camera_setting(method: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把相机参数设置请求规范化为配置映射。"""
    data = payload or {}
    spec = CAMERA_SETTING_MAP[method]
    canonical_method = spec.get("alias", method)

    return {
        "method": method,
        "canonical_method": canonical_method,
        "setting": _extract_setting_payload(spec, data),
    }


def build_media_metadata(
    file_type: str,
    telemetry: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    duration: Optional[float] = None,
) -> Dict[str, Any]:
    """生成文档 7.3 的媒体文件元数据。"""
    telemetry = telemetry or {}
    context = context or {}
    capture_time = int(context.get("capture_time", time.time()))
    extension = "jpg" if file_type == "photo" else "mp4"
    file_name = context.get("file_name", f"{capture_time}_{file_type}.{extension}")

    metadata = {
        "mission_id": context.get("mission_id"),
        "device_sn": context.get("device_sn", ""),
        "file_name": file_name,
        "file_type": file_type,
        "latitude": telemetry.get("latitude", telemetry.get("lat", 0.0)),
        "longitude": telemetry.get("longitude", telemetry.get("lon", 0.0)),
        "altitude": telemetry.get("altitude", telemetry.get("alt", 0.0)),
        "capture_time": capture_time,
        "upload_status": context.get("upload_status", "pending"),
    }
    if duration is not None:
        metadata["duration"] = duration
    return metadata


def _camera_command_params(method: str, data: Dict[str, Any]) -> list[float]:
    if method == "camera_photo_take":
        return [0, 0, 0, 0, 1, 0, 0]
    if method == "start_photo":
        interval_ms = float(data.get("interval", 1.0)) * 1000
        count = int(data.get("count", 1))
        return [0, interval_ms, count, 0, 0, 0, 0]
    if method in ("stop_photo", "stop_record"):
        return [0, 0, 0, 0, 0, 0, 0]
    if method == "start_record":
        frame_rate = int(data.get("frame_rate", data.get("framerate", 30)))
        return [0, frame_rate, 0, 0, 0, 0, 0]
    raise KeyError(f"Unsupported camera method: {method}")


def _extract_setting_payload(spec: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    if "field" in spec:
        field = spec["field"]
        value = data.get(field)
        return {field: value}

    return {field: data.get(field) for field in spec.get("fields", ()) if field in data}
