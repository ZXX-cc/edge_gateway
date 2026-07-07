"""标准 MAVLink 遥测映射模块。"""

from .mapping import (
    apply_mavlink_message,
    build_dock3_properties_from_telemetry,
    build_osd_data,
    new_telemetry_state,
)


__all__ = [
    "apply_mavlink_message",
    "build_dock3_properties_from_telemetry",
    "build_osd_data",
    "new_telemetry_state",
]
