"""标准 MAVLink 遥测字段映射工具。

这里的代码只服务 web/mavlink 调试链路，不修改主网关运行时模块。
输入可以是真实 pymavlink 消息，也可以是带同名字段的测试对象。

字段目标同时兼顾：
- 本地《MAVLink协议字段映射文档》中的 OSD/遥测键。
- DJI Dock3 properties 官方字段，例如 latitude、longitude、height、
  electricity、drone_charge_state、position_state、wind_speed。
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, Iterable, Optional

from src.mavlink.flight_modes import (
    parse_flight_mode,
    flight_mode_profile,
    mode_to_dji_code,
)


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
    "course": 0.0,
    "windSpeed": 0.0,
    "windDirection": 0.0,
    "voltage": 0.0,
    "current": 0.0,
    "batteryRemaining": 0,
    "battery_available": False,
    "satelliteNumber": 0,
    "gps_fixed": 0,
    "gps_eph": 0,
    "gps_epv": 0,
    "gps_signal_level": 0,
    "rtk_flag": 0,
    "rtkNumber": 0,
    "gimbal_pitch": 0.0,
    "gimbal_roll": 0.0,
    "gimbal_yaw": 0.0,
    "obstacle_state": {},
    "estimator_status": {},
    "armed": False,
    "flight_mode": "UNKNOWN",
    "mav_type": None,
    "autopilot": None,
    "flight_mode_profile": "unknown",
    "connection_status": "disconnected",
    "data_mode": "real",
    "timestamp": 0.0,
    "flight_time": 0,
    "_flight_start_time": None,
    "_total_flight_time": 0,
}


DOCK3_POSITION_FIXED_STATE = {
    0: 0,  # 未开始/未定位
    1: 2,  # 本地文档 gps_fixed=1 映射为 Dock3 收敛成功
}


def new_telemetry_state() -> Dict[str, Any]:
    """返回一份新的遥测状态字典。"""
    return TELEMETRY_DEFAULTS.copy()


def message_type(msg: Any) -> str:
    """兼容 pymavlink 消息和测试消息的类型提取。"""
    if hasattr(msg, "get_type"):
        return msg.get_type()
    return getattr(msg, "msg_type", getattr(msg, "_msg_type", ""))


def gps_fixed_to_dock3_state(gps_fixed: int) -> int:
    """把本地 gps_fixed(0/1) 转成 Dock3 position_state.is_fixed 枚举。"""
    return DOCK3_POSITION_FIXED_STATE.get(int(gps_fixed), 0)


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
        if hasattr(msg, "heading"):
            data["course"] = getattr(msg, "heading")
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
        if hasattr(msg, "eph"):
            data["gps_eph"] = getattr(msg, "eph")
            data["gps_signal_level"] = gps_signal_level_from_eph(getattr(msg, "eph"), data.get("gps_fixed", 0))
            updated = True
        elif "satelliteNumber" in data:
            data["gps_signal_level"] = _gps_quality_from_satellites(
                data.get("satelliteNumber", 0),
                data.get("gps_fixed", 0),
            )
            updated = True
        if hasattr(msg, "epv"):
            data["gps_epv"] = getattr(msg, "epv")
            updated = True
    elif msg_name == "GPS2_RAW":
        if hasattr(msg, "satellites_visible"):
            data["rtkNumber"] = getattr(msg, "satellites_visible")
            updated = True
        if hasattr(msg, "fix_type"):
            data["rtk_flag"] = 1 if getattr(msg, "fix_type") >= 5 else 0
            updated = True
    elif msg_name == "GPS_RTK":
        if hasattr(msg, "baseline_coords_type"):
            data["rtk_flag"] = 1 if getattr(msg, "baseline_coords_type") else 0
            updated = True
        if hasattr(msg, "satellites_visible"):
            data["rtkNumber"] = getattr(msg, "satellites_visible")
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
        _update_flight_time(data, data["armed"])
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
            data["batteryRemaining"] = max(0, min(100, int(battery_remaining)))
            data["battery_available"] = True
            updated = True
    elif msg_name == "BATTERY_STATUS":
        voltages = getattr(msg, "voltages", None)
        voltages_ext = getattr(msg, "voltages_ext", None)
        valid_voltages = _valid_cell_voltages(voltages) + _valid_cell_voltages(voltages_ext)
        if valid_voltages:
            data["voltage"] = round(sum(valid_voltages) / 1000.0, 2)
            updated = True
        current_battery = getattr(msg, "current_battery", -1)
        battery_remaining = getattr(msg, "battery_remaining", -1)
        if current_battery != -1:
            data["current"] = round(current_battery / 100.0, 2)
            updated = True
        if battery_remaining != -1:
            data["batteryRemaining"] = max(0, min(100, int(battery_remaining)))
            data["battery_available"] = True
            updated = True
    elif msg_name == "GIMBAL_DEVICE_ATTITUDE_STATUS":
        if hasattr(msg, "q"):
            pitch, roll, yaw = _quaternion_to_euler_degrees(getattr(msg, "q"))
            data["gimbal_pitch"] = pitch
            data["gimbal_roll"] = roll
            data["gimbal_yaw"] = yaw
            updated = True
    elif msg_name == "MOUNT_STATUS":
        if hasattr(msg, "pointing_a"):
            data["gimbal_pitch"] = getattr(msg, "pointing_a") / 100.0
            updated = True
        if hasattr(msg, "pointing_b"):
            data["gimbal_roll"] = getattr(msg, "pointing_b") / 100.0
            updated = True
        if hasattr(msg, "pointing_c"):
            data["gimbal_yaw"] = getattr(msg, "pointing_c") / 100.0
            updated = True
    elif msg_name == "DISTANCE_SENSOR":
        obstacle = dict(data.get("obstacle_state") or {})
        orientation = getattr(msg, "orientation", 0)
        distance_cm = getattr(msg, "current_distance", 0)
        obstacle[str(orientation)] = {
            "distance": distance_cm / 100.0,
            "min_distance": getattr(msg, "min_distance", 0) / 100.0,
            "max_distance": getattr(msg, "max_distance", 0) / 100.0,
        }
        data["obstacle_state"] = obstacle
        updated = True
    elif msg_name == "OBSTACLE_DISTANCE":
        distances = getattr(msg, "distances", [])
        valid = [value / 100.0 for value in distances if value and value != 65535]
        data["obstacle_state"] = {
            "min_distance": min(valid) if valid else None,
            "distances": valid,
            "increment": getattr(msg, "increment", 0),
            "angle_offset": getattr(msg, "angle_offset", 0.0),
        }
        updated = True
    elif msg_name == "EKF_STATUS_REPORT":
        data["estimator_status"] = {
            "flags": getattr(msg, "flags", 0),
            "velocity_variance": getattr(msg, "velocity_variance", 0.0),
            "pos_horiz_variance": getattr(msg, "pos_horiz_variance", 0.0),
            "pos_vert_variance": getattr(msg, "pos_vert_variance", 0.0),
            "compass_variance": getattr(msg, "compass_variance", 0.0),
            "terrain_alt_variance": getattr(msg, "terrain_alt_variance", 0.0),
        }
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
        "course": telemetry.get("course", 0.0),
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
            "quality": telemetry.get("gps_signal_level", 0),
            "rtk_number": telemetry.get("rtkNumber", 0),
            "rtk_flag": telemetry.get("rtk_flag", 0),
        },
        "wind_speed": telemetry.get("windSpeed", 0.0),
        "wind_direction": telemetry.get("windDirection", 0.0),
        "gimbal_pitch": telemetry.get("gimbal_pitch", 0.0),
        "gimbal_roll": telemetry.get("gimbal_roll", 0.0),
        "gimbal_yaw": telemetry.get("gimbal_yaw", 0.0),
        "obstacle_state": telemetry.get("obstacle_state", {}),
        "mode_code": mode_to_dji_code(telemetry.get("flight_mode", "UNKNOWN")),
        "gear": 1 if telemetry.get("armed", False) else 0,
        "flight_time": telemetry.get("flight_time", 0),
        "timestamp": int(time.time() * 1000),
    }
    if current > 0:
        osd["battery"]["current"] = int(current * 1000)
    return osd


def build_dock3_properties_from_telemetry(telemetry: Dict[str, Any]) -> Dict[str, Any]:
    """把标准飞控遥测转换成 DJI Dock3 properties 字段。

    说明：
    - 官方 Dock3 `height` 是机场椭球高度字段；本地映射文档当前要求
      使用 `altitudeRelative` 驱动 `height`，这里按本地文档执行。
    - 官方 Dock3 `position_state.is_fixed` 是 0/1/2/3 枚举，本地遥测只
      有 `gps_fixed` 0/1，因此 1 映射为 2（收敛成功）。
    """
    battery_remaining = telemetry.get("batteryRemaining", 0)
    gps_fixed = telemetry.get("gps_fixed", 0)

    properties = {
        "longitude": telemetry.get("longitude", 0.0),
        "latitude": telemetry.get("latitude", 0.0),
        "height": telemetry.get("altitudeRelative", 0.0),
        "mode_code": mode_to_dji_code(telemetry.get("flight_mode", "UNKNOWN")),
        "electricity": battery_remaining,
        "drone_charge_state": {
            "capacity_percent": battery_remaining,
            "state": 0,
        },
        "position_state": {
            "is_calibration": 1 if gps_fixed else 0,
            "is_fixed": gps_fixed_to_dock3_state(gps_fixed),
            "quality": telemetry.get("gps_signal_level") or _gps_quality_from_satellites(
                telemetry.get("satelliteNumber", 0),
                gps_fixed,
            ),
            "gps_number": telemetry.get("satelliteNumber", 0),
            "rtk_number": telemetry.get("rtkNumber", 0),
        },
        "wind_speed": telemetry.get("windSpeed", 0.0),
    }

    return {key: value for key, value in properties.items() if value is not None}


def _valid_cell_voltages(voltages: Optional[Iterable[int]]) -> list[int]:
    if not voltages:
        return []
    return [value for value in voltages if value and value != 65535]


def _update_flight_time(data: Dict[str, Any], armed: bool) -> None:
    now = time.time()
    start_time = data.get("_flight_start_time")
    total_time = int(data.get("_total_flight_time") or 0)

    if armed and start_time is None:
        data["_flight_start_time"] = now
        data["flight_time"] = total_time
    elif armed and start_time is not None:
        data["flight_time"] = total_time + int(now - float(start_time))
    elif not armed and start_time is not None:
        total_time += int(now - float(start_time))
        data["_total_flight_time"] = total_time
        data["_flight_start_time"] = None
        data["flight_time"] = total_time
    else:
        data["flight_time"] = total_time


def _fill_altitude_agl(data: Dict[str, Any]) -> None:
    if data.get("altitudeAGL", 0.0) != 0.0:
        return
    altitude = data.get("altitude", 0.0)
    terrain = data.get("terrainAltitude", 0.0)
    if altitude > 0 and terrain > 0:
        data["altitudeAGL"] = altitude - terrain


def gps_signal_level_from_eph(eph: int, gps_fixed: int) -> int:
    """把 GPS eph(cm) 粗略映射为 1-5 档信号等级。"""
    if not gps_fixed or not eph:
        return 0
    if eph <= 50:
        return 5
    if eph <= 100:
        return 4
    if eph <= 200:
        return 3
    if eph <= 500:
        return 2
    return 1


def _quaternion_to_euler_degrees(q: Iterable[float]) -> tuple[float, float, float]:
    values = list(q)
    if len(values) < 4:
        return 0.0, 0.0, 0.0

    w, x, y, z = values[:4]
    sinr_cosp = 2 * (w * x + y * z)
    cosr_cosp = 1 - 2 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    sinp = 2 * (w * y - z * x)
    if abs(sinp) >= 1:
        pitch = math.copysign(math.pi / 2, sinp)
    else:
        pitch = math.asin(sinp)

    siny_cosp = 2 * (w * z + x * y)
    cosy_cosp = 1 - 2 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return math.degrees(pitch), math.degrees(roll), math.degrees(yaw) % 360


def _gps_quality_from_satellites(satellites: int, gps_fixed: int) -> int:
    if gps_fixed and satellites >= 20:
        return 5
    if gps_fixed and satellites >= 16:
        return 4
    if gps_fixed and satellites >= 12:
        return 3
    if gps_fixed and satellites >= 8:
        return 2
    return 1
