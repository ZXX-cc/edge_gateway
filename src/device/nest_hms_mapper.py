"""Map nest status bits to Dock3 HMS alarms.

This module is intentionally side-effect free.  DeviceManager owns MQTT
events and database persistence; this mapper only converts the latest nest
state snapshot into stable HMS alarm records.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from src.cloud_api.dock3_hms_log import normalize_hms_alarm


NEST_HMS_KEY_PREFIX = "dock3.nest."
DEFAULT_DOCK_DEVICE_TYPE = "3-119-0"


def is_nest_hms_alarm(alarm: Dict[str, Any]) -> bool:
    """Return whether an alarm was generated from nest status bits."""
    return str((alarm or {}).get("hms_key", "")).startswith(NEST_HMS_KEY_PREFIX)


def _bool_value(data: Dict[str, Any], key: str) -> bool:
    return bool(data.get(key))


def _has_false_flag(data: Dict[str, Any], key: str) -> bool:
    return key in data and data.get(key) is False


def build_nest_hms_alarms(nest_data: Dict[str, Any], device_sn: str) -> List[Dict[str, Any]]:
    """Build active HMS alarms from a DRONE_NEST_RESPONSE-derived snapshot."""
    if not isinstance(nest_data, dict) or nest_data.get("data_valid") is False:
        return []

    now_ms = int(time.time() * 1000)
    raw_alarms: List[Dict[str, Any]] = []

    def add(alarm_id: str, level: int, hms_key: str, content: str, **extra: Any) -> None:
        raw_alarms.append({
            "id": alarm_id,
            "alarm_id": alarm_id,
            "device_sn": device_sn,
            "domain": "3",
            "module": "dock",
            "type": "nest_status",
            "device_type": DEFAULT_DOCK_DEVICE_TYPE,
            "level": level,
            "hms_key": hms_key,
            "content": content,
            "message": content,
            "status": "active",
            "create_time": now_ms,
            "update_time": now_ms,
            **extra,
        })

    if _bool_value(nest_data, "estop_state"):
        add(
            "nest-estop-active",
            3,
            "dock3.nest.estop.active",
            "机巢急停已触发",
        )

    if _bool_value(nest_data, "fault_state"):
        error_code = nest_data.get("error_code", 0)
        suffix = f"-{error_code}" if error_code else ""
        add(
            f"nest-fault-active{suffix}",
            2,
            "dock3.nest.fault.active",
            f"机巢故障状态已触发，error_code={error_code}",
            error_code=error_code,
        )

    if _bool_value(nest_data, "system_busy"):
        add(
            "nest-system-busy",
            1,
            "dock3.nest.system_busy.active",
            "机巢系统处于忙碌状态",
        )

    gate_state = nest_data.get("gate_state")
    try:
        gate_state_value = int(gate_state)
    except (TypeError, ValueError):
        gate_state_value = 0
    if gate_state_value == 6:
        add(
            f"nest-cover-abnormal-{gate_state_value}",
            2,
            "dock3.nest.cover.abnormal",
            f"机巢舱盖状态异常，gate_state={gate_state_value}",
            gate_state=gate_state_value,
        )

    if _bool_value(nest_data, "charging_state"):
        try:
            charge_volt = float(nest_data.get("charge_volt", 0))
            charge_current = float(nest_data.get("charge_current", 0))
        except (TypeError, ValueError):
            charge_volt = 0.0
            charge_current = 0.0
        if charge_volt < 10.0 or charge_current <= 0.0:
            add(
                "nest-charging-abnormal",
                2,
                "dock3.nest.charging.abnormal",
                f"机巢充电状态异常，电压={charge_volt:.2f}V，电流={charge_current:.2f}A",
                charge_volt=charge_volt,
                charge_current=charge_current,
            )

    if _bool_value(nest_data, "emergency_landing"):
        add(
            "nest-emergency-landing",
            3,
            "dock3.nest.emergency_landing.active",
            "机巢上报紧急降落流程",
        )

    error_code = nest_data.get("error_code", 0)
    try:
        error_code_value = int(error_code)
    except (TypeError, ValueError):
        error_code_value = 0
    if error_code_value:
        add(
            f"nest-error-code-{error_code_value}",
            2,
            "dock3.nest.error_code.active",
            f"机巢上报错误码 {error_code_value}",
            error_code=error_code_value,
        )

    heartbeat_labels = {
        "nest_heartbeat": "机巢心跳丢失",
        "plc_heartbeat": "PLC 心跳丢失",
        "drone_heartbeat": "机巢侧飞行器心跳丢失",
    }
    suppress_heartbeat_alarms = {
        "nest_heartbeat": bool(nest_data.get("_suppress_nest_heartbeat_alarm")),
        "plc_heartbeat": bool(nest_data.get("_suppress_plc_heartbeat_alarm")),
        "drone_heartbeat": bool(nest_data.get("_suppress_drone_heartbeat_alarm")),
    }
    for key, content in heartbeat_labels.items():
        if suppress_heartbeat_alarms.get(key):
            continue
        if _has_false_flag(nest_data, key):
            add(
                f"nest-{key.replace('_', '-')}-lost",
                2,
                f"dock3.nest.{key}.lost",
                content,
            )

    return [
        normalize_hms_alarm(alarm, device_sn=device_sn, index=index)
        for index, alarm in enumerate(raw_alarms)
    ]


def nest_hms_signature(alarms: List[Dict[str, Any]]) -> tuple:
    """Stable signature used to avoid duplicate HMS events."""
    return tuple(
        sorted(
            (
                str(alarm.get("alarm_id") or alarm.get("id") or ""),
                int(alarm.get("level", 0)),
                str(alarm.get("hms_key", "")),
                str(alarm.get("status", "")),
            )
            for alarm in alarms
        )
    )
