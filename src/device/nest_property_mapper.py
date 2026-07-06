"""
机巢 MAVLink 状态到上云 API 属性字段的映射。

该模块只做轻量、无副作用的数据转换。Dock3 官方字段最终确认后，
优先在这里调整枚举、单位和字段名。
"""
from typing import Any, Dict


def map_rainfall(raw_rainfall: float) -> int:
    """将机巢降雨量映射为上云侧降雨等级。"""
    if raw_rainfall <= 0:
        return 0
    if raw_rainfall < 2.5:
        return 1
    if raw_rainfall < 8:
        return 2
    return 3


def map_gate_state(gate_state: int) -> int:
    """将机巢舱门状态转换为上云侧舱盖状态。"""
    if gate_state == 1:
        return 1
    if gate_state in (0, 2):
        return 0
    if gate_state in (3, 4, 5):
        return 2
    return 3


def map_clamp_state(clamp_state: int) -> int:
    """将机巢夹紧机构状态转换为上云侧推杆状态。"""
    if clamp_state == 1:
        return 1
    if clamp_state in (0, 2):
        return 0
    if clamp_state in (3, 4, 5):
        return 2
    return 3


def map_nest_to_cloud_properties(nest_data: Dict[str, Any]) -> Dict[str, Any]:
    """将 DroneNestModule 输出转换为当前上云属性/状态字段。"""
    if not nest_data.get("data_valid", False):
        return {}

    updates: Dict[str, Any] = {}

    if "env_temperature" in nest_data:
        updates["environment_temperature"] = nest_data["env_temperature"]
    if "nest_temperature" in nest_data:
        updates["temperature"] = nest_data["nest_temperature"]
        updates["nest_temperature"] = nest_data["nest_temperature"]
    if "wind_speed" in nest_data:
        updates["wind_speed"] = nest_data["wind_speed"]
    if "rainfall" in nest_data:
        updates["rainfall"] = map_rainfall(nest_data["rainfall"])
    if "humidity" in nest_data:
        updates["humidity"] = nest_data["humidity"]

    if "charge_volt" in nest_data:
        updates["working_voltage"] = int(nest_data["charge_volt"] * 1000)
    if "charge_current" in nest_data:
        updates["working_current"] = nest_data["charge_current"] * 1000
    if "electric_supply_voltage" in nest_data:
        updates["electric_supply_voltage"] = nest_data["electric_supply_voltage"]

    if "estop_state" in nest_data:
        updates["emergency_stop_state"] = 1 if nest_data["estop_state"] else 0
    if "charging_state" in nest_data:
        updates["charging_state"] = 1 if nest_data["charging_state"] else 0

    if "gate_state" in nest_data:
        updates["cover_state"] = map_gate_state(nest_data["gate_state"])
    if "clamp_state" in nest_data:
        updates["putter_state"] = map_clamp_state(nest_data["clamp_state"])

    if "landing_light_state" in nest_data:
        updates["supplement_light_state"] = 1 if nest_data["landing_light_state"] == 1 else 0

    if "ac_temp" in nest_data:
        updates["air_conditioner"] = {
            "air_conditioner_state": 1 if nest_data["ac_temp"] > 0 else 0,
            "switch_time": 0,
        }

    return updates


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
