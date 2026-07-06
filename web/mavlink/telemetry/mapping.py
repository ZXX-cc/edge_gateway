"""标准 MAVLink 遥测字段映射工具。

这里的代码只服务 web/mavlink 调试链路，不修改主网关运行时模块。
输入可以是真实 pymavlink 消息，也可以是带同名字段的测试对象。
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, Iterable, Optional


TELEMETRY_DEFAULTS: Dict[str, Any] = {
    "latitude": 0.0,
    "longitude": 0.0,
    "airspeed": 0.0,
    "roll": 0.0,
    "pitch": 0.0,
    "yaw": 0.0,
    "groundSpeed": 0.0,
    "altitude": 0.0,
    "altitudeRelative": 0.0,
    "altitudeAGL": 0.0,
    "terrainAltitude": 0.0,
    "climbRate": 0.0,
    "windSpeed": 0.0,
    "windDirection": 0.0,
    "voltage": 0.0,
    "current": 0.0,
    "batteryRemaining": 0,
    "satelliteNumber": 0,
    "gps_fixed": 0,
    "armed": False,
    "flight_mode": "UNKNOWN",
    "mav_type": None,
    "autopilot": None,
    "flight_mode_profile": "unknown",
    "connection_status": "disconnected",
    "data_mode": "real",
    "timestamp": 0.0,
    "flight_time": 0,
}


COPTER_MAV_TYPES = {2, 3, 4, 13, 14, 15, 29, 35}
PLANE_MAV_TYPES = {1, 19, 20, 21, 22, 23, 24, 25}


COPTER_MODE_MAP = {
    0: "STABILIZE",
    1: "ACRO",
    2: "ALT_HOLD",
    3: "AUTO",
    4: "GUIDED",
    5: "LOITER",
    6: "RTL",
    7: "CIRCLE",
    9: "LAND",
    11: "DRIFT",
    13: "SPORT",
    14: "FLIP",
    15: "AUTOTUNE",
    16: "POSHOLD",
    17: "BRAKE",
    18: "THROW",
    19: "AVOID_ADSB",
    20: "GUIDED_NOGPS",
    21: "SMART_RTL",
    22: "FLOWHOLD",
    23: "FOLLOW",
    24: "ZIGZAG",
    25: "SYSTEMID",
    26: "AUTOROTATE",
    27: "AUTO_RTL",
}


PLANE_MODE_MAP = {
    0: "MANUAL",
    1: "CIRCLE",
    2: "STABILIZE",
    3: "TRAINING",
    4: "ACRO",
    5: "FBWA",
    6: "FBWB",
    7: "CRUISE",
    8: "AUTOTUNE",
    10: "AUTO",
    11: "RTL",
    12: "LOITER",
    13: "TAKEOFF",
    14: "AVOID_ADSB",
    15: "GUIDED",
    16: "INITIALISING",
    17: "QSTABILIZE",
    18: "QHOVER",
    19: "QLOITER",
    20: "QLAND",
    21: "QRTL",
    22: "QAUTOTUNE",
    23: "QACRO",
    24: "THERMAL",
    25: "LOITER_ALT_QLAND",
}


DJI_MODE_CODE_MAP = {
    "STABILIZE": 0,
    "ACRO": 1,
    "ALT_HOLD": 2,
    "AUTO": 3,
    "GUIDED": 4,
    "LOITER": 5,
    "RTL": 6,
    "CIRCLE": 7,
    "LAND": 9,
    "DRIFT": 11,
    "SPORT": 13,
    "FLIP": 14,
    "AUTOTUNE": 15,
    "POSHOLD": 16,
    "BRAKE": 17,
    "THROW": 18,
    "AVOID_ADSB": 19,
    "GUIDED_NOGPS": 20,
    "SMART_RTL": 21,
    "FLOWHOLD": 22,
    "FOLLOW": 23,
    "ZIGZAG": 24,
    "SYSTEMID": 25,
    "AUTOROTATE": 26,
    "AUTO_RTL": 27,
}


def new_telemetry_state() -> Dict[str, Any]:
    """返回一份新的遥测状态字典。"""
    return TELEMETRY_DEFAULTS.copy()


def message_type(msg: Any) -> str:
    """兼容 pymavlink 消息和测试消息的类型提取。"""
    if hasattr(msg, "get_type"):
        return msg.get_type()
    return getattr(msg, "msg_type", getattr(msg, "_msg_type", ""))


def flight_mode_profile(mav_type: Optional[int]) -> str:
    """根据 HEARTBEAT.type 选择 ArduPilot 飞行模式表。"""
    if mav_type in COPTER_MAV_TYPES:
        return "copter"
    if mav_type in PLANE_MAV_TYPES:
        return "plane"
    return "unknown"


def parse_flight_mode(custom_mode: int, mav_type: Optional[int] = None) -> str:
    """把 MAVLink custom_mode 转成飞行模式字符串。"""
    profile = flight_mode_profile(mav_type)
    mode_map = COPTER_MODE_MAP if profile == "copter" else PLANE_MODE_MAP
    return mode_map.get(custom_mode, f"UNKNOWN({custom_mode})")


def mode_to_dji_code(mode: str) -> int:
    """把飞行模式字符串转成 OSD mode_code。"""
    return DJI_MODE_CODE_MAP.get(str(mode).upper(), 0)


def apply_mavlink_message(msg: Any, telemetry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把一条标准 MAVLink 消息合入遥测状态并返回状态。"""
    data = telemetry if telemetry is not None else new_telemetry_state()
    msg_name = message_type(msg)
    updated = False

    if msg_name == "ATTITUDE":
        data["roll"] = math.degrees(getattr(msg, "roll"))
        data["pitch"] = math.degrees(getattr(msg, "pitch"))
        data["yaw"] = math.degrees(getattr(msg, "yaw")) % 360
        updated = True
    elif msg_name == "VFR_HUD":
        data["airspeed"] = getattr(msg, "airspeed")
        data["groundSpeed"] = getattr(msg, "groundspeed")
        data["climbRate"] = getattr(msg, "climb")
        updated = True
    elif msg_name == "WIND":
        if hasattr(msg, "speed"):
            data["windSpeed"] = getattr(msg, "speed")
            updated = True
        if hasattr(msg, "direction"):
            data["windDirection"] = getattr(msg, "direction")
            updated = True
    elif msg_name == "GLOBAL_POSITION_INT":
        if hasattr(msg, "lat"):
            data["latitude"] = getattr(msg, "lat") / 1e7
            updated = True
        if hasattr(msg, "lon"):
            data["longitude"] = getattr(msg, "lon") / 1e7
            updated = True
        if hasattr(msg, "alt"):
            data["altitude"] = getattr(msg, "alt") / 1000.0
            updated = True
        if hasattr(msg, "relative_alt"):
            data["altitudeRelative"] = getattr(msg, "relative_alt") / 1000.0
            updated = True
        if hasattr(msg, "hdg") and getattr(msg, "hdg") != 65535:
            data["yaw"] = getattr(msg, "hdg") / 100.0
            updated = True
    elif msg_name == "GPS_RAW_INT":
        if hasattr(msg, "satellites_visible"):
            data["satelliteNumber"] = getattr(msg, "satellites_visible")
            updated = True
        if hasattr(msg, "fix_type"):
            data["gps_fixed"] = 1 if getattr(msg, "fix_type") >= 3 else 0
            updated = True
    elif msg_name == "HEARTBEAT":
        base_mode = getattr(msg, "base_mode", 0)
        custom_mode = getattr(msg, "custom_mode", 0)
        mav_type = getattr(msg, "type", None)
        data["armed"] = bool(base_mode & 0x80)
        data["flight_mode"] = parse_flight_mode(custom_mode, mav_type)
        data["mav_type"] = mav_type
        data["autopilot"] = getattr(msg, "autopilot", None)
        data["flight_mode_profile"] = flight_mode_profile(mav_type)
        updated = True
    elif msg_name == "TERRAIN_REPORT":
        if hasattr(msg, "terrain_height"):
            data["terrainAltitude"] = getattr(msg, "terrain_height")
            updated = True
        if hasattr(msg, "current_height"):
            data["altitudeAGL"] = getattr(msg, "current_height")
            updated = True
    elif msg_name == "SYS_STATUS":
        voltage_battery = getattr(msg, "voltage_battery", 65535)
        current_battery = getattr(msg, "current_battery", -1)
        battery_remaining = getattr(msg, "battery_remaining", -1)
        if voltage_battery != 65535:
            data["voltage"] = round(voltage_battery / 1000.0, 2)
            updated = True
        if current_battery != -1:
            data["current"] = round(current_battery / 100.0, 2)
            updated = True
        if battery_remaining != -1:
            data["batteryRemaining"] = battery_remaining
            updated = True
    elif msg_name == "BATTERY_STATUS":
        voltages = getattr(msg, "voltages", None)
        valid_voltages = _valid_cell_voltages(voltages)
        if valid_voltages:
            data["voltage"] = round(sum(valid_voltages) / 1000.0, 2)
            updated = True
        current_battery = getattr(msg, "current_battery", -1)
        battery_remaining = getattr(msg, "battery_remaining", -1)
        if current_battery != -1:
            data["current"] = round(current_battery / 100.0, 2)
            updated = True
        if battery_remaining != -1:
            data["batteryRemaining"] = battery_remaining
            updated = True

    if updated:
        _fill_altitude_agl(data)
        data["connection_status"] = "connected"
        data["data_mode"] = "real"
        data["timestamp"] = time.time()

    return data


def build_osd_data(telemetry: Dict[str, Any]) -> Dict[str, Any]:
    """把统一遥测状态转换成 OSD 定频上报结构。"""
    voltage = telemetry.get("voltage", 0.0)
    current = telemetry.get("current", 0.0)
    osd = {
        "latitude": telemetry.get("latitude", 0.0),
        "longitude": telemetry.get("longitude", 0.0),
        "height": telemetry.get("altitudeRelative", 0.0),
        "altitude": telemetry.get("altitude", 0.0),
        "horizontal_speed": telemetry.get("groundSpeed", 0.0),
        "vertical_speed": telemetry.get("climbRate", 0.0),
        "attitude_pitch": telemetry.get("pitch", 0.0),
        "attitude_roll": telemetry.get("roll", 0.0),
        "attitude_head": telemetry.get("yaw", 0.0),
        "battery": {
            "capacity_percent": telemetry.get("batteryRemaining", 0),
            "voltage": int(voltage * 1000),
        },
        "position_state": {
            "gps_number": telemetry.get("satelliteNumber", 0),
            "is_fixed": telemetry.get("gps_fixed", 0),
        },
        "wind_speed": telemetry.get("windSpeed", 0.0),
        "mode_code": mode_to_dji_code(telemetry.get("flight_mode", "UNKNOWN")),
        "gear": 1 if telemetry.get("armed", False) else 0,
        "flight_time": telemetry.get("flight_time", 0),
        "timestamp": int(time.time() * 1000),
    }
    if current > 0:
        osd["battery"]["current"] = int(current * 1000)
    return osd


def _valid_cell_voltages(voltages: Optional[Iterable[int]]) -> list[int]:
    if not voltages:
        return []
    return [value for value in voltages if value and value != 65535]


def _fill_altitude_agl(data: Dict[str, Any]) -> None:
    if data.get("altitudeAGL", 0.0) != 0.0:
        return
    altitude = data.get("altitude", 0.0)
    terrain = data.get("terrainAltitude", 0.0)
    if altitude > 0 and terrain > 0:
        data["altitudeAGL"] = altitude - terrain
