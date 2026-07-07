"""
机巢 MAVLink 状态到上云 API 属性字段的映射。

该模块只做轻量、无副作用的数据转换。Dock3 官方字段最终确认后，
优先在这里调整枚举、单位和字段名。
"""
from typing import Any, Dict

from web.mavlink.nest.property_mapping import (
    build_dock3_properties_from_nest,
    map_cover_state,
    map_putter_state,
    map_rainfall as _map_rainfall,
)


def map_rainfall(raw_rainfall: float) -> int:
    """将机巢降雨量映射为上云侧降雨等级。"""
    return _map_rainfall(raw_rainfall)


def map_gate_state(gate_state: int) -> int:
    """将机巢舱门状态转换为上云侧舱盖状态。"""
    return map_cover_state(gate_state)


def map_clamp_state(clamp_state: int) -> int:
    """将机巢夹紧机构状态转换为上云侧推杆状态。"""
    return map_putter_state(clamp_state)


def map_nest_to_cloud_properties(nest_data: Dict[str, Any]) -> Dict[str, Any]:
    """将 DroneNestModule 输出转换为当前上云属性/状态字段。"""
    return build_dock3_properties_from_nest(nest_data)


def filter_dock_status_fields(updates: Dict[str, Any]) -> Dict[str, Any]:
    """提取 DockManager._dock_status 支持的字段。"""
    allowed = {
        "cover_state",
        "putter_state",
        "charging_state",
        "drone_in_dock",
        "environment_temperature",
        "nest_temperature",
        "wind_speed",
        "rainfall",
        "humidity",
        "supplement_light_state",
        "alarm_state",
    }
    return {key: value for key, value in updates.items() if key in allowed}
