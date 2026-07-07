"""
DJI Dock 3 DRC metadata and validation helpers.

The DRC official page keeps the control channel under:

    thing/product/{gateway_sn}/drc/down

This module keeps field validation and compatibility mapping in one place.
The current gateway still keeps legacy simulated fields during migration, but
official Dock3 fields should be preferred by new tests.
"""
import math
import time
from typing import Any, Dict, List, Tuple


STICK_MIN = 364
STICK_CENTER = 1024
STICK_MAX = 1684
STICK_RANGE = 660

STICK_MAX_SPEED = 2.0
STICK_MAX_VERTICAL_SPEED = 1.0
STICK_MAX_YAW_RATE = 1.0

DRONE_XY_SPEED_MIN = -17.0
DRONE_XY_SPEED_MAX = 17.0
DRONE_DOWN_SPEED_MIN = -4.0
DRONE_UP_SPEED_MAX = 5.0
DRONE_YAW_SPEED_MIN = -90.0
DRONE_YAW_SPEED_MAX = 90.0

TAKEOFF_POINT_MIN_HEIGHT = 5.0
TAKEOFF_POINT_MAX_HEIGHT = 500.0
TAKEOFF_POINT_MAX_SPEED = 17.0


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


def validate_drc_mode_enter(data: Any) -> Tuple[bool, List[str]]:
    """
    Validate Dock3 drc_mode_enter payload.

    Empty data is accepted for the local simulator because the gateway does not
    connect to a real DJI DRC relay in this project phase.  When fields are
    present, they must match the official Dock3 structure.
    """
    if data is None:
        return True, []
    if not isinstance(data, dict):
        return False, ["drc_mode_enter data must be object"]

    errors: List[str] = []
    broker = data.get("mqtt_broker")
    if broker is not None:
        if not isinstance(broker, dict):
            errors.append("mqtt_broker must be object")
        else:
            for name in ("address", "client_id", "username", "password"):
                if name in broker and not isinstance(broker.get(name), str):
                    errors.append(f"mqtt_broker.{name} must be string")
            if "expire_time" in broker and (
                isinstance(broker.get("expire_time"), bool)
                or not isinstance(broker.get("expire_time"), int)
                or broker.get("expire_time") <= 0
            ):
                errors.append("mqtt_broker.expire_time must be positive int seconds")
            if "enable_tls" in broker and not isinstance(broker.get("enable_tls"), bool):
                errors.append("mqtt_broker.enable_tls must be bool")

    for name in ("osd_frequency", "hsi_frequency"):
        if name in data:
            value = data.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 30:
                errors.append(f"{name} must be int in range 1..30")

    return not errors, errors


def _stick_channel(data: Dict[str, Any], name: str, errors: List[str]) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{name} must be int")
        return STICK_CENTER
    if not STICK_MIN <= value <= STICK_MAX:
        errors.append(f"{name} must be in range {STICK_MIN}..{STICK_MAX}")
    return value


def _stick_to_unit(value: int) -> float:
    return max(-1.0, min(1.0, (value - STICK_CENTER) / STICK_RANGE))


def normalize_stick_control(data: Any) -> Tuple[bool, Dict[str, float], List[str]]:
    """
    Normalize official stick_control channels to the gateway's velocity model.

    Official Dock3 fields are roll/pitch/throttle/yaw in RC stick units
    [1024±660].  Legacy vx/vy/vz/yaw_rate aliases are still accepted during
    migration for existing tests.
    """
    if not isinstance(data, dict):
        return False, {}, ["stick_control data must be object"]

    official_fields = {"roll", "pitch", "throttle", "yaw"}
    if official_fields & set(data.keys()):
        errors: List[str] = []
        missing = sorted(official_fields - set(data.keys()))
        if missing:
            errors.append(f"missing stick_control keys: {', '.join(missing)}")

        roll = _stick_channel(data, "roll", errors)
        pitch = _stick_channel(data, "pitch", errors)
        throttle = _stick_channel(data, "throttle", errors)
        yaw = _stick_channel(data, "yaw", errors)
        if "gimbal_pitch" in data:
            _stick_channel(data, "gimbal_pitch", errors)

        normalized = {
            "vx": _stick_to_unit(pitch) * STICK_MAX_SPEED,
            "vy": _stick_to_unit(roll) * STICK_MAX_SPEED,
            "vz": -_stick_to_unit(throttle) * STICK_MAX_VERTICAL_SPEED,
            "yaw_rate": _stick_to_unit(yaw) * STICK_MAX_YAW_RATE,
        }
        return not errors, normalized, errors

    normalized = {
        "vx": _to_float(data.get("vx", data.get("right_y", data.get("pitch", 0.0)))),
        "vy": _to_float(data.get("vy", data.get("right_x", data.get("roll", 0.0)))),
        "vz": _to_float(data.get("vz", data.get("left_y", data.get("throttle", 0.0)))),
        "yaw_rate": _to_float(data.get("yaw_rate", data.get("left_x", data.get("yaw", 0.0)))),
    }
    return True, normalized, []


def normalize_drone_control(data: Any) -> Tuple[bool, Dict[str, float], List[str]]:
    """
    Normalize official deprecated drone_control to velocity command fields.

    Official Dock3 fields: seq, x, y, h, w.  Legacy project fields are still
    accepted during migration.
    """
    if not isinstance(data, dict):
        return False, {}, ["drone_control data must be object"]

    official_fields = {"x", "y", "h", "w"}
    if official_fields & set(data.keys()):
        errors: List[str] = []
        missing = sorted({"seq", *official_fields} - set(data.keys()))
        if missing:
            errors.append(f"missing drone_control keys: {', '.join(missing)}")

        seq = data.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int):
            errors.append("seq must be int")
        x = data.get("x", 0.0)
        y = data.get("y", 0.0)
        h = data.get("h", 0.0)
        w = data.get("w", 0.0)
        if not _in_range(x, DRONE_XY_SPEED_MIN, DRONE_XY_SPEED_MAX):
            errors.append("x must be in range -17..17 m/s")
        if not _in_range(y, DRONE_XY_SPEED_MIN, DRONE_XY_SPEED_MAX):
            errors.append("y must be in range -17..17 m/s")
        if not _in_range(h, DRONE_DOWN_SPEED_MIN, DRONE_UP_SPEED_MAX):
            errors.append("h must be in range -4..5 m/s")
        if not _in_range(w, DRONE_YAW_SPEED_MIN, DRONE_YAW_SPEED_MAX):
            errors.append("w must be in range -90..90 degree/s")

        normalized = {
            "vx": _to_float(x),
            "vy": _to_float(y),
            # Gateway velocity uses MAVLink BODY_NED semantics: positive down.
            "vz": -_to_float(h),
            "yaw_rate": math.radians(_to_float(w)),
            "seq": _to_int(seq, -1),
        }
        return not errors, normalized, errors

    h_speed = _to_float(data.get("horizontal_speed", 0.0))
    h_dir = _to_float(data.get("horizontal_direction", 0.0))
    v_speed = _to_float(data.get("vertical_speed", 0.0))
    v_dir = _to_float(data.get("vertical_direction", 0.0))
    yaw = _to_float(data.get("yaw", 0.0))
    rad = math.radians(h_dir)
    return True, {
        "vx": h_speed * math.cos(rad),
        "vy": h_speed * math.sin(rad),
        "vz": v_speed * (1 if v_dir > 0 else -1),
        "yaw_rate": math.radians(yaw),
        "seq": _to_int(data.get("seq", 0)),
    }, []


def validate_gimbal_speed_push(data: Any) -> Tuple[bool, List[str]]:
    if not isinstance(data, dict):
        return False, ["drc_gimbal_speed_push data must be object"]
    errors: List[str] = []
    for name in ("pitch_speed", "yaw_speed", "roll_speed"):
        if name in data and not _is_number(data.get(name)):
            errors.append(f"{name} must be number")
    return not errors, errors


def validate_drc_heartbeat(data: Any) -> Tuple[bool, Dict[str, Any], List[str]]:
    """Validate cloud-origin DRC heartbeat payload and return normalized fields."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return False, {}, ["heart_beat data must be object"]

    errors: List[str] = []
    timestamp = data.get("timestamp", int(time.time() * 1000))
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or timestamp <= 0:
        errors.append("timestamp must be positive number milliseconds")

    seq = data.get("seq", data.get("sequence"))
    if seq is not None and (isinstance(seq, bool) or not isinstance(seq, int) or seq < 0):
        errors.append("seq must be non-negative int")

    normalized = {
        "timestamp": int(timestamp) if _is_number(timestamp) else int(time.time() * 1000),
    }
    if seq is not None and not isinstance(seq, bool):
        normalized["seq"] = int(seq)
    return not errors, normalized, errors


def normalize_takeoff_to_point(data: Any, default_flight_id: str = "") -> Tuple[bool, Dict[str, Any], List[str]]:
    """Validate and normalize Dock3 takeoff_to_point payload."""
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
    """Validate and normalize Dock3 fly_to_point_update style payload."""
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
        "fly_to_id": fly_to_id or default_fly_to_id,
        "max_speed": _to_float(max_speed, 12.0),
        "points": normalized_points,
    }
    return not errors, normalized, errors
