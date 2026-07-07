"""相机 MAVLink 协议映射模块。"""

from .mapping import (
    CAMERA_COMMAND_MAP,
    CAMERA_SETTING_MAP,
    build_camera_command,
    build_camera_setting,
    build_media_metadata,
)


__all__ = [
    "CAMERA_COMMAND_MAP",
    "CAMERA_SETTING_MAP",
    "build_camera_command",
    "build_camera_setting",
    "build_media_metadata",
]
