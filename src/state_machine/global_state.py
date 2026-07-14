"""Global gateway state snapshot helpers.

The global state machine does not own the business modules.  It reads their
latest cached state and converts it into a stable command-admission snapshot.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class GatewayPhase(str, Enum):
    BOOTING = "BOOTING"
    CLOUD_OFFLINE = "CLOUD_OFFLINE"
    DEVICE_UNBOUND = "DEVICE_UNBOUND"
    DEVICE_BOUND_OFFLINE = "DEVICE_BOUND_OFFLINE"
    READY = "READY"
    BUSY_MISSION = "BUSY_MISSION"
    BUSY_DRC = "BUSY_DRC"
    BUSY_OTA = "BUSY_OTA"
    MAINTENANCE = "MAINTENANCE"
    FAULT_BLOCKED = "FAULT_BLOCKED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    RECOVERING = "RECOVERING"


@dataclass(frozen=True)
class GatewayStateSnapshot:
    gateway_phase: str = GatewayPhase.BOOTING.value
    cloud: str = "unknown"
    device: str = "unknown"
    dock: str = "unknown"
    aircraft: str = "unknown"
    mission: str = "unknown"
    drc: str = "unknown"
    ota: str = "unknown"
    safety: str = "normal"
    generated_at: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, compact: bool = False) -> Dict[str, Any]:
        data = {
            "gateway_phase": self.gateway_phase,
            "cloud": self.cloud,
            "device": self.device,
            "dock": self.dock,
            "aircraft": self.aircraft,
            "mission": self.mission,
            "drc": self.drc,
            "ota": self.ota,
            "safety": self.safety,
            "generated_at": int(self.generated_at * 1000),
        }
        if not compact:
            data["details"] = self.details
        return data


def _call(obj: Any, method_name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    method = getattr(obj, method_name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:
        return default


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default) if obj is not None else default


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "connected", "online"}
    return bool(value)


class GatewayStateProvider:
    """Builds the combined snapshot from existing gateway modules."""

    _MISSION_NAMES = {
        "MISSION_IDLE": "idle",
        "MISSION_PREPARING": "preparing",
        "MISSION_RUNNING": "running",
        "MISSION_PAUSED": "paused",
        "MISSION_COMPLETED": "completed",
        "MISSION_FAILED": "failed",
        "MISSION_CANCELED": "canceled",
    }

    _OTA_BUSY_STATES = {"checking", "downloading", "verifying", "upgrading"}

    def __init__(self, **sources: Any):
        self.sources: Dict[str, Any] = {}
        self.set_sources(**sources)

    def set_sources(self, **sources: Any) -> None:
        self.sources.update({key: value for key, value in sources.items() if value is not None})

    def snapshot(self) -> GatewayStateSnapshot:
        cloud, cloud_details = self._read_cloud()
        device, device_details = self._read_device()
        dock, dock_details = self._read_dock()
        aircraft, aircraft_details = self._read_aircraft()
        mission, mission_details = self._read_mission()
        drc, drc_details = self._read_drc()
        ota, ota_details = self._read_ota()
        safety, safety_details = self._read_safety(dock_details, aircraft_details)
        phase = self._calculate_phase(cloud, device, dock, aircraft, mission, drc, ota, safety)
        return GatewayStateSnapshot(
            gateway_phase=phase,
            cloud=cloud,
            device=device,
            dock=dock,
            aircraft=aircraft,
            mission=mission,
            drc=drc,
            ota=ota,
            safety=safety,
            details={
                "cloud": cloud_details,
                "device": device_details,
                "dock": dock_details,
                "aircraft": aircraft_details,
                "mission": mission_details,
                "drc": drc_details,
                "ota": ota_details,
                "safety": safety_details,
            },
        )

    def _read_cloud(self) -> tuple[str, Dict[str, Any]]:
        mqtt = self.sources.get("mqtt")
        if mqtt is None:
            return "unknown", {}
        connected = bool(_get(mqtt, "connected", False))
        return ("online" if connected else "offline"), {
            "connected": connected,
            "gateway_sn": _get(mqtt, "gateway_sn", ""),
        }

    def _read_device(self) -> tuple[str, Dict[str, Any]]:
        device = self.sources.get("device")
        state = _call(device, "get_device_state", {}) or {}
        if state:
            return str(state.get("state") or "unknown"), state
        return "unknown", {}

    def _read_ota(self) -> tuple[str, Dict[str, Any]]:
        device = self.sources.get("device")
        ota_state = str(_get(device, "_ota_state", "unknown") or "unknown")
        return ota_state, {
            "state": ota_state,
            "progress": _get(device, "_ota_progress", None),
            "task_id": _get(device, "_ota_task_id", ""),
        }

    def _read_mission(self) -> tuple[str, Dict[str, Any]]:
        mission = self.sources.get("mission")
        if mission is None:
            return "unknown", {}
        status = _get(mission, "_status", None)
        name = "unknown"
        for attr, label in self._MISSION_NAMES.items():
            if status == _get(mission, attr, object()):
                name = label
                break
        return name, {
            "status_code": status,
            "task_id": _get(mission, "_current_task_id", None),
            "wayline_mission_state": _get(mission, "_wayline_mission_state", None),
            "current_step": _get(mission, "_current_step", None),
            "current_waypoint_index": _get(mission, "_current_waypoint_idx", None),
            "waypoint_count": len(_get(mission, "_current_wayline", []) or []),
        }

    def _read_drc(self) -> tuple[str, Dict[str, Any]]:
        drc = self.sources.get("drc")
        status = _call(drc, "get_drc_status", {}) or {}
        if not status:
            return "unknown", {}
        enabled = bool(status.get("enabled"))
        return ("active" if enabled else "idle"), status

    def _read_dock(self) -> tuple[str, Dict[str, Any]]:
        nest = self.sources.get("nest")
        status = _call(nest, "get_uav_nest_status", {}) or {}
        if status:
            data_valid = bool(status.get("data_valid"))
            estop = bool(status.get("estop_state"))
            fault = bool(status.get("fault_state")) or int(status.get("error_code") or 0) != 0
            busy = bool(status.get("system_busy"))
            heartbeat_ok = bool(status.get("nest_heartbeat")) and bool(status.get("plc_heartbeat"))
            takeoff_allowed = bool(status.get("takeoff_allowed"))
            if estop:
                return "emergency_stop", status
            if fault:
                return "fault", status
            if busy:
                return "busy", status
            if data_valid and heartbeat_ok and takeoff_allowed:
                return "ready", status
            if data_valid and heartbeat_ok:
                return "online", status
            if data_valid:
                return "offline", status
            return "offline", status

        dock = self.sources.get("dock")
        dock_status = dict(_get(dock, "_dock_status", {}) or {})
        if dock_status:
            if dock_status.get("alarm_state"):
                return "fault", dock_status
            return "online", dock_status
        return "unknown", {}

    def _read_aircraft(self) -> tuple[str, Dict[str, Any]]:
        telemetry = self._read_telemetry_snapshot()
        if not telemetry:
            return "unknown", {}
        connection_status = str(telemetry.get("connection_status") or "disconnected")
        if connection_status in {"connected", "simulated"}:
            state = "online"
        else:
            state = "offline"
        details = {
            "connection_status": connection_status,
            "armed": bool(telemetry.get("armed", False)),
            "flight_mode": telemetry.get("flight_mode", telemetry.get("mode", "UNKNOWN")),
            "battery": telemetry.get("batteryRemaining", telemetry.get("battery", None)),
            "satellite_number": telemetry.get("satelliteNumber", telemetry.get("satellite_number", None)),
            "gps_fixed": telemetry.get("gps_fixed", None),
            "height": telemetry.get("altitudeRelative", telemetry.get("altitude", None)),
        }
        return state, details

    def _read_telemetry_snapshot(self) -> Dict[str, Any]:
        processor = self.sources.get("telemetry")
        data = _call(processor, "get_current_data", None)
        if data:
            return data
        module = self.sources.get("telemetry_module")
        data = _call(module, "get_telemetry_data", None)
        return data or {}

    def _read_safety(self, dock_details: Dict[str, Any], aircraft_details: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        device = self.sources.get("device")
        alarms = list(_get(device, "_hms_alarms", []) or [])
        if dock_details.get("estop_state"):
            return "emergency_stop", {"source": "dock", "estop_state": True}
        if dock_details.get("fault_state") or int(dock_details.get("error_code") or 0) != 0:
            return "blocking_hms_alarm", {
                "source": "dock",
                "fault_state": dock_details.get("fault_state"),
                "error_code": dock_details.get("error_code"),
            }
        battery = aircraft_details.get("battery")
        try:
            battery_value: Optional[float] = float(battery) if battery is not None else None
        except (TypeError, ValueError):
            battery_value = None
        if battery_value is not None and 0 < battery_value < 20:
            return "low_battery", {"battery": battery_value}
        if alarms:
            return "hms_alarm", {"alarm_count": len(alarms)}
        return "normal", {}

    def _calculate_phase(
        self,
        cloud: str,
        device: str,
        dock: str,
        aircraft: str,
        mission: str,
        drc: str,
        ota: str,
        safety: str,
    ) -> str:
        if cloud == "offline":
            return GatewayPhase.CLOUD_OFFLINE.value
        if device == "unbound":
            return GatewayPhase.DEVICE_UNBOUND.value
        if safety == "emergency_stop" or dock == "emergency_stop":
            return GatewayPhase.EMERGENCY_STOP.value
        if safety in {"blocking_hms_alarm", "low_battery"} or dock == "fault":
            return GatewayPhase.FAULT_BLOCKED.value
        if ota in self._OTA_BUSY_STATES:
            return GatewayPhase.BUSY_OTA.value
        if drc == "active":
            return GatewayPhase.BUSY_DRC.value
        if mission in {"preparing", "running", "paused"}:
            return GatewayPhase.BUSY_MISSION.value
        if device in {"bound", "offline"} and (dock == "offline" or aircraft == "offline"):
            return GatewayPhase.DEVICE_BOUND_OFFLINE.value
        if device in {"bound", "online"}:
            return GatewayPhase.READY.value
        return GatewayPhase.BOOTING.value
