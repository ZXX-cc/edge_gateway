"""云台 MAVLink 协议映射模块。"""

from .mapping import (
    GIMBAL_CAPABILITY,
    GIMBAL_COMMAND_MAP,
    MOUNT_MODE_MAP,
    build_gimbal_command,
)


__all__ = [
    "GIMBAL_CAPABILITY",
    "GIMBAL_COMMAND_MAP",
    "MOUNT_MODE_MAP",
    "build_gimbal_command",
]
