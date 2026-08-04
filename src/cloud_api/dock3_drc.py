"""DJI Dock 3 DRC metadata and validation helpers for drc/down/up topics."""
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

EPOCH_SECONDS_THRESHOLD = 1_000_000_000
EPOCH_MILLISECONDS_THRESHOLD = 1_000_000_000_000

OFFICIAL_DOCK3_DRC_METHODS = frozenset({
    "obstacle_avoidance_notify",
    "drc_status_notify",
    "joystick_invalid_notify",
    "stick_control",
    "drone_control",
    "drone_emergency_stop",
    "heart_beat",
    "hsi_info_push",
    "delay_info_push",
    "osd_info_push",
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


def _is_expired_epoch_time(value: Any, now_seconds: float | None = None) -> bool:
    """判断秒/毫秒绝对过期时间是否已经过期；小数值按相对 TTL 处理。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    now_seconds = time.time() if now_seconds is None else now_seconds
    expire = int(value)
    if expire >= EPOCH_MILLISECONDS_THRESHOLD:
        return expire <= int(now_seconds * 1000)
    if expire >= EPOCH_SECONDS_THRESHOLD:
        return expire <= int(now_seconds)
    return False


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
            elif _is_expired_epoch_time(broker.get("expire_time")):
                errors.append("mqtt_broker.expire_time has expired")
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
