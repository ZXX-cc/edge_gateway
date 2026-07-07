"""Shared ArduPilot flight mode mapping helpers.

Cloud API handlers, telemetry OSD builders, and Web MAVLink tools must agree on
how ArduPilot custom_mode values map to human-readable modes and Dock3
mode_code values.  Numeric API inputs are treated as the gateway's stable DJI
mode code layer, then converted to the active vehicle profile when sending
MAVLink SET_MODE.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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

DJI_CODE_MODE_MAP = {value: key for key, value in DJI_MODE_CODE_MAP.items()}


@dataclass(frozen=True)
class NormalizedFlightMode:
    mode_name: str
    mode_code: int
    source_key: str
    raw_value: object


def flight_mode_profile(mav_type: Optional[int]) -> str:
    """Return the ArduPilot mode table profile for a MAVLink HEARTBEAT.type."""
    if mav_type in COPTER_MAV_TYPES:
        return "copter"
    if mav_type in PLANE_MAV_TYPES:
        return "plane"
    return "unknown"


def mode_map_for_profile(profile: str) -> dict[int, str]:
    """Return a custom_mode -> mode-name map for the given profile."""
    return COPTER_MODE_MAP if profile == "copter" else PLANE_MODE_MAP


def mode_name_to_custom_mode(mode_name: str, profile: str = "plane") -> Optional[int]:
    """Resolve a mode name into the active profile's MAVLink custom_mode."""
    mode_upper = str(mode_name).upper()
    for mode_id, name in mode_map_for_profile(profile).items():
        if name == mode_upper:
            return mode_id
    return None


def parse_flight_mode(custom_mode: int, mav_type: Optional[int] = None, profile: Optional[str] = None) -> str:
    """Convert MAVLink custom_mode to a flight mode string."""
    selected_profile = profile if profile and profile != "unknown" else flight_mode_profile(mav_type)
    mode_map = mode_map_for_profile(selected_profile)
    return mode_map.get(custom_mode, f"UNKNOWN({custom_mode})")


def mode_to_dji_code(mode: object) -> int:
    """Convert a flight mode string to the gateway's stable Dock3 mode_code."""
    if isinstance(mode, int):
        return mode
    return DJI_MODE_CODE_MAP.get(str(mode).upper(), 0)


def normalize_flight_mode_request(data: dict, default_mode_code: int = 2) -> tuple[bool, Optional[NormalizedFlightMode], str]:
    """Normalize Cloud API set_flight_mode payloads.

    Supported request keys:
    - flight_mode / mode_name: explicit mode name, for example "GUIDED".
    - mode_code / mode: stable gateway/Dock3 mode code, for example 4 -> GUIDED.
    Raw MAVLink custom_mode IDs are intentionally not accepted here.  Cloud
    requests should use the stable mode name or Dock3 mode_code layer; the
    active FlightControl profile converts that name into MAVLink custom_mode.
    """
    payload = data if isinstance(data, dict) else {}

    for key in ("flight_mode", "mode_name"):
        if key not in payload:
            continue
        raw_value = payload.get(key)
        mode_name = str(raw_value).strip().upper()
        if not mode_name:
            return False, None, f"{key} is empty"
        if mode_name not in DJI_MODE_CODE_MAP:
            return False, None, f"unsupported flight mode: {raw_value}"
        return True, NormalizedFlightMode(mode_name, DJI_MODE_CODE_MAP[mode_name], key, raw_value), ""

    for key in ("mode_code", "mode"):
        raw_value = payload.get(key, default_mode_code) if key == "mode" else payload.get(key)
        if raw_value is None:
            continue
        try:
            mode_code = int(raw_value)
        except (TypeError, ValueError):
            mode_name = str(raw_value).strip().upper()
            if mode_name in DJI_MODE_CODE_MAP:
                return True, NormalizedFlightMode(mode_name, DJI_MODE_CODE_MAP[mode_name], key, raw_value), ""
            return False, None, f"{key} must be a mode name or integer mode code"

        mode_name = DJI_CODE_MODE_MAP.get(mode_code)
        if not mode_name:
            return False, None, f"unsupported mode code: {mode_code}"
        return True, NormalizedFlightMode(mode_name, mode_code, key, raw_value), ""

    mode_name = DJI_CODE_MODE_MAP.get(default_mode_code)
    if not mode_name:
        return False, None, f"unsupported default mode code: {default_mode_code}"
    return True, NormalizedFlightMode(mode_name, default_mode_code, "default", default_mode_code), ""
