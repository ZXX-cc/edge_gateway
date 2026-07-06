"""
DJI Dock 3 properties metadata and validation helpers.

This module is intentionally small and explicit.  It is the single source of
truth for the properties category during the Dock3 migration, so DeviceManager
does not have to carry scattered writable/read-only/value-range rules.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, Tuple


PROPERTY_SET_SUCCESS = 0
PROPERTY_SET_FAILED = 1


@dataclass(frozen=True)
class PropertySpec:
    name: str
    label: str
    value_type: str
    access_mode: str
    push_mode: int
    enum_values: Optional[Set[Any]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    source: str = "dock3"

    @property
    def writable(self) -> bool:
        return self.access_mode == "rw"


DOCK3_PROPERTY_SPECS: Dict[str, PropertySpec] = {
    # Dock3 official rw properties confirmed from the DJI Dock3 properties page.
    "air_transfer_enable": PropertySpec(
        name="air_transfer_enable",
        label="空中回传",
        value_type="bool",
        access_mode="rw",
        push_mode=1,
    ),
    "silent_mode": PropertySpec(
        name="silent_mode",
        label="机场静音模式",
        value_type="enum_int",
        access_mode="rw",
        push_mode=1,
        enum_values={0, 1},
    ),
    "user_experience_improvement": PropertySpec(
        name="user_experience_improvement",
        label="用户体验改善计划",
        value_type="enum_int",
        access_mode="rw",
        push_mode=1,
        enum_values={0, 1, 2},
    ),

    # Key Dock3 read-only properties currently used by the gateway.
    "longitude": PropertySpec("longitude", "经度", "double", "r", 0, min_value=-180, max_value=180),
    "latitude": PropertySpec("latitude", "纬度", "double", "r", 0, min_value=-90, max_value=90),
    "height": PropertySpec("height", "椭球高度", "double", "r", 0),
    "firmware_version": PropertySpec("firmware_version", "固件版本", "text", "r", 1),
    "firmware_upgrade_status": PropertySpec("firmware_upgrade_status", "固件升级状态", "enum_int", "r", 1, enum_values={0, 1}),
    "mode_code": PropertySpec("mode_code", "机场状态", "enum_int", "r", 0, enum_values={0, 1, 2, 3, 4, 5}),
    "flighttask_step_code": PropertySpec("flighttask_step_code", "机场任务状态", "enum_int", "r", 0),
    "sub_device": PropertySpec("sub_device", "子设备状态", "struct", "r", 0),
    "cover_state": PropertySpec("cover_state", "舱盖状态", "enum_int", "r", 0, enum_values={0, 1, 2, 3}),
    "supplement_light_state": PropertySpec("supplement_light_state", "补光灯状态", "enum_int", "r", 0, enum_values={0, 1}),
    "network_state": PropertySpec("network_state", "网络状态", "struct", "r", 0),
    "drone_in_dock": PropertySpec("drone_in_dock", "飞行器是否在舱", "enum_int", "r", 0, enum_values={0, 1}),
    "battery_store_mode": PropertySpec("battery_store_mode", "电池运行模式", "enum_int", "r", 0, enum_values={1, 2}),
    "alarm_state": PropertySpec("alarm_state", "机场声光报警状态", "enum_int", "r", 0, enum_values={0, 1}),
    "backup_battery": PropertySpec("backup_battery", "机场备用电池信息", "struct", "r", 0),
    "drone_charge_state": PropertySpec("drone_charge_state", "飞行器充电状态", "struct", "r", 0),
    "position_state": PropertySpec("position_state", "搜星状态", "struct", "r", 0),
    "wpmz_version": PropertySpec("wpmz_version", "航线解析库版本", "text", "r", 1),
    "drc_state": PropertySpec("drc_state", "DRC链路状态", "enum_int", "r", 0, enum_values={0, 1, 2}),
    "drone_authority_info": PropertySpec("drone_authority_info", "飞行器控制权状态", "struct", "r", 0),
}


# Properties still present in the simulator/current code, but not accepted as
# direct Dock3 property/set fields in this migration pass.  They remain readable
# when present in DeviceManager._properties and should be controlled by services
# or another Dock3 group later.
LEGACY_READABLE_PROPERTIES: Dict[str, PropertySpec] = {
    "air_conditioner_mode": PropertySpec("air_conditioner_mode", "空调模式", "enum_int", "r", 0, source="legacy_service_controlled"),
    "remaining_power_for_return_home": PropertySpec("remaining_power_for_return_home", "返航预留电量", "int", "r", 0, source="legacy_service_controlled"),
    "battery_maintenance_mode": PropertySpec("battery_maintenance_mode", "电池保养模式", "enum_int", "r", 0, source="legacy_service_controlled"),
    "putter_state": PropertySpec("putter_state", "推杆状态", "enum_int", "r", 0, enum_values={0, 1, 2, 3}, source="legacy_service_controlled"),
    "charging_state": PropertySpec("charging_state", "充电状态", "enum_int", "r", 0, enum_values={0, 1}, source="legacy_service_controlled"),
}


ALL_PROPERTY_SPECS: Dict[str, PropertySpec] = {
    **DOCK3_PROPERTY_SPECS,
    **LEGACY_READABLE_PROPERTIES,
}


def writable_property_names() -> Set[str]:
    return {name for name, spec in ALL_PROPERTY_SPECS.items() if spec.writable}


def get_property_spec(name: str) -> Optional[PropertySpec]:
    return ALL_PROPERTY_SPECS.get(name)


def normalize_property_value(spec: PropertySpec, value: Any) -> Tuple[bool, Any, str]:
    """Validate and normalize a property value for property/set."""
    if spec.value_type == "bool":
        if isinstance(value, bool):
            return True, value, ""
        return False, value, f"{spec.name} expects bool"

    if spec.value_type == "enum_int":
        if isinstance(value, bool) or not isinstance(value, int):
            return False, value, f"{spec.name} expects int enum"
        if spec.enum_values is not None and value not in spec.enum_values:
            return False, value, f"{spec.name} out of enum range"
        return True, value, ""

    if spec.value_type in {"int", "float", "double"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, value, f"{spec.name} expects number"
        if spec.min_value is not None and value < spec.min_value:
            return False, value, f"{spec.name} below min"
        if spec.max_value is not None and value > spec.max_value:
            return False, value, f"{spec.name} above max"
        return True, value, ""

    return True, value, ""


def validate_property_set(name: str, value: Any) -> Tuple[int, Any, str]:
    """
    Return ``(state_result, normalized_value, reason)`` for Dock3 property/set.

    Dock3 property/set_reply uses per-property ``state.result``.  The first
    migration pass keeps that result as ``0`` for success and ``1`` for failed
    validation/read-only/unknown property.
    """
    spec = get_property_spec(name)
    if not spec:
        return PROPERTY_SET_FAILED, value, f"unknown property: {name}"
    if not spec.writable:
        return PROPERTY_SET_FAILED, value, f"read-only property: {name}"

    valid, normalized_value, reason = normalize_property_value(spec, value)
    if not valid:
        return PROPERTY_SET_FAILED, value, reason
    return PROPERTY_SET_SUCCESS, normalized_value, ""
