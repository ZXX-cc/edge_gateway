"""自定义机巢 MAVLink 状态到 DJI Dock3 properties 的映射。

来源：
- 本地《MAVLink协议字段映射文档》中的 DRONE_NEST_RESPONSE 字段单位。
- DJI Dock3 properties 官方字段：
  cover_state、putter_state、drone_charge_state、emergency_stop_state、
  working_voltage、working_current、humidity、temperature、
  environment_temperature、wind_speed、rainfall、supplement_light_state、
  air_conditioner、alarm_state、drone_in_dock。
"""
from __future__ import annotations

from typing import Any, Dict

from .protocol import (
    BIT_ALLOW_TAKEOFF,
    BIT_CHARGING,
    BIT_CALIBRATE,
    BIT_DRONE_HEARTBEAT,
    BIT_EMERGENCY_LANDING,
    BIT_ESTOP,
    BIT_INIT,
    BIT_LANDING_ABORT_PROGRESS,
    BIT_LANDING_IN_PROGRESS,
    BIT_NEST_HEARTBEAT,
    BIT_PLC_HEARTBEAT,
    BIT_PLC_READY,
    BIT_TAKEOFF_ABORT_PROGRESS,
    BIT_TAKEOFF_IN_PROGRESS,
)


def response_to_nest_state(msg: Any) -> Dict[str, Any]:
    """把 DRONE_NEST_RESPONSE 消息对象转换成规范化机巢状态字典。"""
    boolstate1 = getattr(msg, "boolstate1", 0)
    boolstate2 = getattr(msg, "boolstate2", 0)

    return {
        "data_valid": True,
        "ac_temp": _scaled(getattr(msg, "ac_temp", 0), 10.0),
        "nest_temperature": _scaled(getattr(msg, "nest_temperature", 0), 10.0),
        "env_temperature": _scaled(getattr(msg, "env_temperature", 0), 10.0),
        "wind_direction": _scaled(getattr(msg, "wind_direction", 0), 100.0),
        "wind_speed": _scaled(getattr(msg, "wind_speed", 0), 10.0),
        "atmospheric_pressure": _scaled(getattr(msg, "atmospheric_pressure", 0), 10.0),
        "rainfall": _scaled(getattr(msg, "rainfall", 0), 10.0),
        "charge_volt": _scaled(getattr(msg, "charge_volt", 0), 100.0),
        "charge_current": _scaled(getattr(msg, "charge_current", 0), 100.0),
        "error_code": getattr(msg, "error_code", 0),
        "boolstate1": boolstate1,
        "boolstate2": boolstate2,
        "nest_heartbeat": _bit(boolstate1, BIT_NEST_HEARTBEAT),
        "estop_state": _bit(boolstate1, BIT_ESTOP),
        "charging_state": _bit(boolstate1, BIT_CHARGING),
        "drone_heartbeat": _bit(boolstate1, BIT_DRONE_HEARTBEAT),
        "plc_heartbeat": _bit(boolstate1, BIT_PLC_HEARTBEAT),
        "calibrate_state": _bit(boolstate1, BIT_CALIBRATE),
        "ready_state": _bit(boolstate1, BIT_PLC_READY),
        "init_state": _bit(boolstate1, BIT_INIT),
        "takeoff_in_progress": _bit(boolstate2, BIT_TAKEOFF_IN_PROGRESS),
        "landing_in_progress": _bit(boolstate2, BIT_LANDING_IN_PROGRESS),
        "takeoff_abort_progress": _bit(boolstate2, BIT_TAKEOFF_ABORT_PROGRESS),
        "landing_abort_progress": _bit(boolstate2, BIT_LANDING_ABORT_PROGRESS),
        "emergency_landing": _bit(boolstate2, BIT_EMERGENCY_LANDING),
        "takeoff_allowed": _bit(boolstate2, BIT_ALLOW_TAKEOFF),
        "clamp_state": getattr(msg, "clamp_state", 0),
        "gate_state": getattr(msg, "gate_state", 0),
        "landing_light_state": getattr(msg, "landing_light_state", 0),
    }


def build_dock3_properties_from_nest(nest_state: Dict[str, Any]) -> Dict[str, Any]:
    """把规范化机巢状态转换成 DJI Dock3 properties 字段。"""
    if not nest_state.get("data_valid", True):
        return {}

    properties: Dict[str, Any] = {}

    if "gate_state" in nest_state:
        properties["cover_state"] = map_cover_state(nest_state["gate_state"])
    if "clamp_state" in nest_state:
        properties["putter_state"] = map_putter_state(nest_state["clamp_state"])
    if "charging_state" in nest_state:
        charge_state = 1 if nest_state["charging_state"] else 0
        properties["charging_state"] = charge_state
        properties["drone_charge_state"] = {"state": charge_state}
        if "batteryRemaining" in nest_state:
            properties["drone_charge_state"]["capacity_percent"] = nest_state["batteryRemaining"]
    if "rainfall" in nest_state:
        properties["rainfall"] = map_rainfall(nest_state["rainfall"])
    if "env_temperature" in nest_state:
        properties["environment_temperature"] = nest_state["env_temperature"]
    if "nest_temperature" in nest_state:
        properties["temperature"] = nest_state["nest_temperature"]
        properties["nest_temperature"] = nest_state["nest_temperature"]
    if "humidity" in nest_state:
        properties["humidity"] = nest_state["humidity"]
    if "wind_speed" in nest_state:
        properties["wind_speed"] = nest_state["wind_speed"]
    if "charge_volt" in nest_state:
        properties["working_voltage"] = int(nest_state["charge_volt"] * 1000)
    if "charge_current" in nest_state:
        properties["working_current"] = nest_state["charge_current"] * 1000
    if "electric_supply_voltage" in nest_state:
        properties["electric_supply_voltage"] = nest_state["electric_supply_voltage"]
    if "estop_state" in nest_state:
        properties["emergency_stop_state"] = 1 if nest_state["estop_state"] else 0
    if "landing_light_state" in nest_state:
        properties["supplement_light_state"] = 1 if nest_state["landing_light_state"] == 1 else 0
    if "ac_temp" in nest_state:
        properties["air_conditioner"] = {
            "air_conditioner_state": 1 if nest_state["ac_temp"] > 0 else 0,
            "switch_time": 0,
        }
    if "error_code" in nest_state:
        properties["alarm_state"] = 1 if nest_state["error_code"] else 0
    if "drone_heartbeat" in nest_state:
        properties["drone_in_dock"] = 1 if nest_state["drone_heartbeat"] else 0

    return properties


def map_rainfall(raw_rainfall: float) -> int:
    """降雨量 mm/m^2 到 Dock3 rainfall 等级。"""
    if raw_rainfall <= 0:
        return 0
    if raw_rainfall < 2.5:
        return 1
    if raw_rainfall < 8:
        return 2
    return 3


def map_cover_state(gate_state: int) -> int:
    """机巢舱门状态到 Dock3 cover_state。"""
    if gate_state == 1:
        return 1
    if gate_state in (0, 2):
        return 0
    if gate_state in (3, 4, 5):
        return 2
    return 3


def map_putter_state(clamp_state: int) -> int:
    """机巢夹紧/推杆状态到 Dock3 putter_state。"""
    if clamp_state == 1:
        return 1
    if clamp_state in (0, 2):
        return 0
    if clamp_state in (3, 4, 5):
        return 2
    return 3


def _bit(value: int, bit: int) -> bool:
    return bool(value & (1 << bit))


def _scaled(value: float, scale: float) -> float:
    return value / scale if scale else value
