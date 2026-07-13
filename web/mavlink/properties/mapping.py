"""Dock3 物模型属性数据来源映射。

对应《MAVLink协议字段映射文档》第四部分：
- 4.2 MAVLink 遥测驱动属性
- 4.3 机巢协议驱动属性
- 4.4 内部维护属性

该模块只做字段聚合，不负责 MQTT 发布、不访问数据库、不启动任务。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from ..nest.property_mapping import build_dock3_properties_from_nest
from ..telemetry.mapping import build_dock3_properties_from_telemetry


PROPERTY_SOURCE_MAP: Dict[str, Dict[str, Any]] = {
    # 4.2 MAVLink 遥测驱动属性
    "longitude": {"source": "MAVLink GLOBAL_POSITION_INT.lon", "pushMode": 0},
    "latitude": {"source": "MAVLink GLOBAL_POSITION_INT.lat", "pushMode": 0},
    "height": {"source": "MAVLink GLOBAL_POSITION_INT.relative_alt", "pushMode": 0},
    "mode_code": {"source": "MAVLink HEARTBEAT.custom_mode", "pushMode": 0},
    "electricity": {"source": "MAVLink SYS_STATUS/BATTERY_STATUS", "pushMode": 0},
    "drone_charge_state.capacity_percent": {
        "source": "MAVLink SYS_STATUS/BATTERY_STATUS.battery_remaining",
        "pushMode": 0,
    },
    "position_state.gps_number": {"source": "MAVLink GPS_RAW_INT.satellites_visible", "pushMode": 0},
    "position_state.is_fixed": {"source": "MAVLink GPS_RAW_INT.fix_type", "pushMode": 0},
    "wind_speed": {"source": "MAVLink WIND.speed / DRONE_NEST_RESPONSE.wind_speed", "pushMode": 0},

    # 4.3 机巢协议驱动属性
    "cover_state": {"source": "DRONE_NEST_RESPONSE.gate_state", "pushMode": 0},
    "putter_state": {"source": "DRONE_NEST_RESPONSE.clamp_state", "pushMode": 0},
    "drone_charge_state.state": {"source": "DRONE_NEST_RESPONSE.boolstate1.bit4", "pushMode": 0},
    "rainfall": {"source": "DRONE_NEST_RESPONSE.rainfall", "pushMode": 0},
    "environment_temperature": {"source": "DRONE_NEST_RESPONSE.env_temperature", "pushMode": 0},
    "temperature": {"source": "DRONE_NEST_RESPONSE.nest_temperature", "pushMode": 0},
    "working_voltage": {"source": "DRONE_NEST_RESPONSE.charge_volt", "pushMode": 0},
    "working_current": {"source": "DRONE_NEST_RESPONSE.charge_current", "pushMode": 0},
    "emergency_stop_state": {"source": "DRONE_NEST_RESPONSE.boolstate1.bit3", "pushMode": 0},
    "supplement_light_state": {"source": "DRONE_NEST_RESPONSE.landing_light_state", "pushMode": 0},
    "air_conditioner.air_conditioner_state": {"source": "DRONE_NEST_RESPONSE.ac_temp", "pushMode": 0},

    # 4.4 内部维护属性
    "firmware_version": {"source": "internal.firmware.version", "pushMode": 1},
    "firmware_upgrade_status": {"source": "internal.ota.status", "pushMode": 1},
    "flighttask_step_code": {"source": "internal.mission.step_code", "pushMode": 0},
    "job_number": {"source": "internal.database.job_number", "pushMode": 0},
    "drc_state": {"source": "internal.drc.state", "pushMode": 0},
    "acc_time": {"source": "internal.runtime.acc_time", "pushMode": 0},
}


INTERNAL_FIELD_ALIASES = {
    "firmware_version": ("firmware_version", "version"),
    "firmware_upgrade_status": ("firmware_upgrade_status", "ota_status", "upgrade_status"),
    "compatible_status": ("compatible_status",),
    "flighttask_step_code": ("flighttask_step_code", "mission_step_code", "step_code"),
    "job_number": ("job_number",),
    "drc_state": ("drc_state",),
    "acc_time": ("acc_time", "runtime_seconds"),
    "network_state": ("network_state",),
    "storage": ("storage",),
    "media_file_detail": ("media_file_detail",),
    "backup_battery": ("backup_battery",),
    "alternate_land_point": ("alternate_land_point",),
    "sub_device": ("sub_device",),
}


def build_properties_from_sources(
    telemetry: Optional[Dict[str, Any]] = None,
    nest_state: Optional[Dict[str, Any]] = None,
    internal_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """按文档第四部分聚合 Dock3 properties。

    合并顺序：
    1. 标准 MAVLink 遥测属性。
    2. 机巢自定义协议属性。
    3. 内部维护属性。

    对同一个嵌套结构会深合并，例如 `drone_charge_state.capacity_percent`
    来自飞控电量，`drone_charge_state.state` 来自机巢充电状态。
    """
    merged: Dict[str, Any] = {}

    if telemetry:
        merge_properties(merged, build_dock3_properties_from_telemetry(telemetry))
    if nest_state:
        merge_properties(merged, build_dock3_properties_from_nest(nest_state))
    if internal_state:
        merge_properties(merged, build_properties_from_internal_state(internal_state))

    return merged


def build_properties_from_osd(osd_data: Dict[str, Any]) -> Dict[str, Any]:
    """把当前 TelemetryProcessor OSD 数据转换成 Dock3 properties。

    主网关运行时 `DeviceManager.update_from_telemetry()` 接收到的是 OSD，
    不是原始 telemetry，因此这里做一层 OSD -> properties 的适配。
    """
    properties: Dict[str, Any] = {}

    # 以下机场位置字段暂时复用 aircraft 遥测，仅保障无真机阶段的 DRC/SITL
    # 联调。真机接入后必须替换为机场固定坐标、RTK 状态和椭球高度。
    if "longitude" in osd_data:
        properties["longitude"] = osd_data["longitude"]
    if "latitude" in osd_data:
        properties["latitude"] = osd_data["latitude"]
    if "height" in osd_data:
        properties["height"] = osd_data["height"]
    if "attitude_head" in osd_data:
        raw_heading = float(osd_data["attitude_head"] or 0.0)
        # Aircraft OSD uses 0..360 in part of the current stack while the Dock
        # 3 property requires -180..180. This is explicitly a simulator-only
        # stand-in until the dock supplies its calibrated fixed heading.
        properties["heading"] = ((raw_heading + 180.0) % 360.0) - 180.0

    coordinate_keys = {"longitude", "latitude", "height"}
    if coordinate_keys.issubset(properties):
        longitude = float(properties["longitude"] or 0.0)
        latitude = float(properties["latitude"] or 0.0)
        height = float(properties["height"] or 0.0)
        heading_available = "heading" in properties
        coordinate_available = abs(longitude) > 1e-7 or abs(latitude) > 1e-7
        if coordinate_available and heading_available:
            properties["home_position_is_valid"] = 1
        elif heading_available:
            properties["home_position_is_valid"] = 2
        elif coordinate_available:
            properties["home_position_is_valid"] = 3
        else:
            properties["home_position_is_valid"] = 0
        properties["self_converge_coordinate"] = {
            "longitude": longitude,
            "latitude": latitude,
            "height": height,
        }
    # aircraft mode_code 是飞行状态枚举，不能覆盖 gateway Dock3
    # mode_code(0..5)。机场作业状态只由 mission/device 状态机维护。
    if "battery" in osd_data:
        capacity_percent = osd_data["battery"].get("capacity_percent", 0)
        properties["electricity"] = capacity_percent
        properties["drone_charge_state"] = {"capacity_percent": capacity_percent}
    if "position_state" in osd_data:
        position = osd_data["position_state"]
        fixed_state = int(position.get("is_fixed", 0) or 0)
        properties["position_state"] = {
            "is_calibration": 1 if fixed_state == 2 else 0,
            "is_fixed": fixed_state if fixed_state in {0, 1, 2, 3} else 0,
            # Dock 3 additionally uses quality=10 for RTK fixed.
            "quality": (
                10 if int(position.get("quality", 0) or 0) == 10
                else max(0, min(5, int(position.get("quality", 0) or 0)))
            ),
            "gps_number": position.get("gps_number", 0),
            "rtk_number": position.get("rtk_number", 0),
        }
    if "wind_speed" in osd_data:
        properties["wind_speed"] = osd_data["wind_speed"]
    if osd_data.get("gear") == 1:
        properties["drone_in_dock"] = 0

    return properties


def build_properties_from_internal_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """把内部维护状态转换成 Dock3 properties 字段。"""
    properties: Dict[str, Any] = {}

    for output_field, aliases in INTERNAL_FIELD_ALIASES.items():
        value = _first_present(state, aliases)
        if value is not None:
            properties[output_field] = copy.deepcopy(value)

    return properties


def merge_properties(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """深合并 properties，返回 base 本身。"""
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge_properties(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _first_present(data: Dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        if key in data:
            return data[key]
    return None
