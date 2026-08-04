"""Dock3 aircraft mode_code derivation.

The mode code is a business-state enum.  It intentionally stays separate from
the raw MAVLink telemetry mapper so DRC, wayline, and takeoff-flow signals can
be combined without pretending that autopilot flight modes are Dock3 states.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


def aircraft_mode_code(telemetry: Dict[str, Any]) -> Optional[int]:
    """Return a real Dock3 aircraft mode_code when an upstream source provides it."""
    raw = telemetry.get("aircraft_mode_code", telemetry.get("mode_code"))
    if raw is None:
        return None
    try:
        mode_code = int(raw)
    except (TypeError, ValueError):
        return None
    return mode_code if 0 <= mode_code <= 21 else None


def derive_aircraft_mode_code_from_real_state(
    telemetry: Dict[str, Any],
    mission_status: Optional[str] = None,
    wayline_mission_state: Optional[int] = None,
    auto_takeoff_state: Optional[str] = None,
    nest_status: Optional[Dict[str, Any]] = None,
    drc_status: Optional[Dict[str, Any]] = None,
    takeoff_flow_steps: Optional[Iterable[Dict[str, Any]]] = None,
) -> int:
    """Derive Dock3 aircraft mode_code from telemetry plus gateway business state."""
    explicit = aircraft_mode_code(telemetry)
    if explicit is not None:
        return explicit

    connected = telemetry.get("connection_status") in {"connected", "simulated"}
    if not connected:
        return 14

    try:
        wayline_state = int(wayline_mission_state) if wayline_mission_state is not None else None
    except (TypeError, ValueError):
        wayline_state = None
    mission_status_text = str(mission_status or "").lower()
    flight_mode_text = str(telemetry.get("flight_mode") or "").upper()

    if flight_mode_text in {"QRTL", "QLAND"}:
        return 10
    if flight_mode_text in {"RTL", "RLT"}:
        return 9

    drc = drc_status if isinstance(drc_status, dict) else {}
    if _point_flight_active(drc):
        return 17
    if _drc_manual_control_active(drc):
        return 3

    if mission_status_text in {"running", "in_progress"} or wayline_state in {6, 8}:
        return 5

    steps = _takeoff_flow_status(takeoff_flow_steps)
    takeoff_state_text = str(auto_takeoff_state or "").upper()
    if (
        takeoff_state_text == "EXECUTING"
        or (steps.get("flight_arm") and mission_status_text not in {"running", "in_progress"})
    ):
        return 4

    if steps.get("self_check") or steps.get("execute_preflight_ready"):
        return 2

    if steps.get("flighttask_execute_precheck"):
        return 1

    return 0


def derive_aircraft_mode_code_from_gateway_modules(
    telemetry: Dict[str, Any],
    mission: Any = None,
    flight_control: Any = None,
    drc: Any = None,
    nest_module: Any = None,
    logger: Any = None,
) -> int:
    """Collect gateway module state and derive Dock3 aircraft mode_code."""
    mission_status = None
    wayline_state = None
    takeoff_flow_steps = None
    if mission:
        try:
            mission_status = mission._get_task_status_name()
            wayline_state = getattr(mission, "_wayline_mission_state", None)
            takeoff_flow_steps = list(getattr(mission, "_last_takeoff_flow_steps", []) or [])
        except Exception as exc:
            _warn(logger, "Read mission mode_code context failed: %s", exc)

    auto_takeoff_state = None
    if flight_control:
        try:
            auto_takeoff_state = flight_control.get_status().get("auto_takeoff_state")
        except Exception as exc:
            _warn(logger, "Read flight control mode_code context failed: %s", exc)

    drc_status = None
    if drc:
        try:
            drc_status = drc.get_drc_status()
        except Exception as exc:
            _warn(logger, "Read DRC mode_code context failed: %s", exc)

    nest_status = {}
    if nest_module:
        try:
            nest_status = nest_module.get_uav_nest_status()
        except Exception as exc:
            _warn(logger, "Read nest mode_code context failed: %s", exc)

    return derive_aircraft_mode_code_from_real_state(
        telemetry,
        mission_status=mission_status,
        wayline_mission_state=wayline_state,
        auto_takeoff_state=auto_takeoff_state,
        nest_status=nest_status,
        drc_status=drc_status,
        takeoff_flow_steps=takeoff_flow_steps,
    )


def _takeoff_flow_status(steps: Optional[Iterable[Dict[str, Any]]]) -> Dict[str, bool]:
    """Return successful/started execute-stage wayline takeoff flow markers."""
    status: Dict[str, bool] = {}
    if not steps:
        return status
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("step") or "")
        if not name:
            continue
        step_status = str(step.get("status") or "").lower()
        result = step.get("result")
        success = step_status in {"ok", "sent", "in_progress"} or result == 0
        status[name] = status.get(name, False) or success or name == "flighttask_execute_precheck"
    return status


def _point_flight_active(drc_status: Dict[str, Any]) -> bool:
    return bool(drc_status.get("active_fly_to_point") or drc_status.get("active_takeoff_to_point"))


def _drc_manual_control_active(drc_status: Dict[str, Any]) -> bool:
    if not bool(drc_status.get("enabled")):
        return False
    return bool(
        drc_status.get("last_speed_cmd")
        or drc_status.get("last_heading_cmd")
        or drc_status.get("last_altitude_cmd")
    )


def _warn(logger: Any, message: str, exc: Exception) -> None:
    if logger and hasattr(logger, "warning"):
        logger.warning(message, exc)
