"""
Dock3 services metadata for one-key takeoff and point flight.

These methods are documented on the Dock3 DRC page, but their MQTT topic is
``thing/product/{gateway_sn}/services``. Keep them out of the DRC module so
topic ownership stays explicit.
"""
import time
from typing import Any, Dict, List, Tuple


TAKEOFF_POINT_MIN_HEIGHT = 5.0
TAKEOFF_POINT_MAX_HEIGHT = 500.0
TAKEOFF_POINT_MAX_SPEED = 17.0

DOCK3_POINT_FLIGHT_SERVICE_METHODS = frozenset({
    "takeoff_to_point",
    "takeoff_to_point_progress",
    "fly_to_point",
    "fly_to_point_stop",
    "fly_to_point_update",
    "fly_to_point_progress",
})


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _in_range(value: Any, minimum: float, maximum: float) -> bool:
    return _is_number(value) and minimum <= float(value) <= maximum


def normalize_takeoff_to_point(data: Any, default_flight_id: str = "") -> Tuple[bool, Dict[str, Any], List[str]]:
    """Validate and normalize Dock3 takeoff_to_point services payload."""
    if not isinstance(data, dict):
        return False, {}, ["takeoff_to_point data must be object"]

    errors: List[str] = []

    def get_first(*names: str):
        for name in names:
            if name in data:
                return data.get(name)
        return None

    latitude = get_first("target_latitude", "latitude")
    longitude = get_first("target_longitude", "longitude")
    height = get_first("target_height", "height", "altitude")
    takeoff_height = get_first("security_takeoff_height", "takeoff_height")
    rth_altitude = data.get("rth_altitude")
    commander_height = data.get("commander_flight_height")
    max_speed = data.get("max_speed", 12)
    flight_id = data.get("flight_id", default_flight_id)

    missing = []
    if latitude is None:
        missing.append("target_latitude")
    if longitude is None:
        missing.append("target_longitude")
    if height is None:
        missing.append("target_height")
    if missing:
        errors.append(f"missing takeoff_to_point keys: {', '.join(missing)}")

    if latitude is not None and not _in_range(latitude, -90.0, 90.0):
        errors.append("target_latitude must be in range -90..90")
    if longitude is not None and not _in_range(longitude, -180.0, 180.0):
        errors.append("target_longitude must be in range -180..180")
    if height is not None and not _in_range(height, TAKEOFF_POINT_MIN_HEIGHT, TAKEOFF_POINT_MAX_HEIGHT):
        errors.append("target_height must be in range 5..500 m")
    if takeoff_height is not None and not _in_range(takeoff_height, TAKEOFF_POINT_MIN_HEIGHT, TAKEOFF_POINT_MAX_HEIGHT):
        errors.append("security_takeoff_height must be in range 5..500 m")
    if rth_altitude is not None and not _in_range(rth_altitude, TAKEOFF_POINT_MIN_HEIGHT, TAKEOFF_POINT_MAX_HEIGHT):
        errors.append("rth_altitude must be in range 5..500 m")
    if commander_height is not None and not _in_range(commander_height, TAKEOFF_POINT_MIN_HEIGHT, TAKEOFF_POINT_MAX_HEIGHT):
        errors.append("commander_flight_height must be in range 5..500 m")
    if not _in_range(max_speed, 0.1, TAKEOFF_POINT_MAX_SPEED):
        errors.append("max_speed must be in range 0.1..17 m/s")
    if flight_id is not None and not isinstance(flight_id, str):
        errors.append("flight_id must be string")

    for action_name in ("rc_lost_action", "commander_mode_lost_action"):
        value = data.get(action_name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            errors.append(f"{action_name} must be non-negative int")

    normalized = {
        "target_latitude": _to_float(latitude),
        "target_longitude": _to_float(longitude),
        "target_height": _to_float(height, TAKEOFF_POINT_MIN_HEIGHT),
        "security_takeoff_height": _to_float(takeoff_height, _to_float(height, TAKEOFF_POINT_MIN_HEIGHT)),
        "rth_altitude": _to_float(rth_altitude, _to_float(height, TAKEOFF_POINT_MIN_HEIGHT)),
        "commander_flight_height": _to_float(commander_height, _to_float(height, TAKEOFF_POINT_MIN_HEIGHT)),
        "max_speed": _to_float(max_speed, 12.0),
        "flight_id": flight_id or default_flight_id,
        "rc_lost_action": _to_int(data.get("rc_lost_action", 0), 0),
        "commander_mode_lost_action": _to_int(data.get("commander_mode_lost_action", 0), 0),
    }
    return not errors, normalized, errors


def normalize_fly_to_point_update(data: Any, default_fly_to_id: str = "") -> Tuple[bool, Dict[str, Any], List[str]]:
    """Validate and normalize Dock3 fly_to_point_update services payload."""
    if not isinstance(data, dict):
        return False, {}, ["fly_to_point_update data must be object"]

    errors: List[str] = []
    max_speed = data.get("max_speed", data.get("speed", 12))
    fly_to_id = data.get("fly_to_id", data.get("flight_id", default_fly_to_id))
    points = data.get("points")
    if points is None:
        single_point = {
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "height": data.get("height", data.get("altitude")),
        }
        points = [single_point]

    if not isinstance(points, list) or not points:
        errors.append("points must be non-empty list")
        points = []

    if not _in_range(max_speed, 0.1, TAKEOFF_POINT_MAX_SPEED):
        errors.append("max_speed must be in range 0.1..17 m/s")
    if fly_to_id is not None and not isinstance(fly_to_id, str):
        errors.append("fly_to_id must be string")

    normalized_points: List[Dict[str, float]] = []
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            errors.append(f"points[{index}] must be object")
            continue
        latitude = point.get("latitude")
        longitude = point.get("longitude")
        height = point.get("height", point.get("altitude"))
        missing = []
        if latitude is None:
            missing.append("latitude")
        if longitude is None:
            missing.append("longitude")
        if height is None:
            missing.append("height")
        if missing:
            errors.append(f"points[{index}] missing keys: {', '.join(missing)}")
        if latitude is not None and not _in_range(latitude, -90.0, 90.0):
            errors.append(f"points[{index}].latitude must be in range -90..90")
        if longitude is not None and not _in_range(longitude, -180.0, 180.0):
            errors.append(f"points[{index}].longitude must be in range -180..180")
        if height is not None and not _in_range(height, TAKEOFF_POINT_MIN_HEIGHT, TAKEOFF_POINT_MAX_HEIGHT):
            errors.append(f"points[{index}].height must be in range 5..500 m")
        normalized_points.append({
            "latitude": _to_float(latitude),
            "longitude": _to_float(longitude),
            "height": _to_float(height, TAKEOFF_POINT_MIN_HEIGHT),
        })

    normalized = {
        "fly_to_id": fly_to_id or default_fly_to_id or str(int(time.time() * 1000)),
        "max_speed": _to_float(max_speed, 12.0),
        "points": normalized_points,
    }
    return not errors, normalized, errors
