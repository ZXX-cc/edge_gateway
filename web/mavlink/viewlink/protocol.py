"""ViewLink V3.4.9 raw communication-frame helpers.

The PDF protocol uses a common frame wrapper:
55 AA DC LEN CMD DATA... CHECKSUM, where LEN covers itself through checksum
and CHECKSUM is XOR of LEN and all bytes from CMD through the last data byte.
"""
from __future__ import annotations

from typing import Iterable


HEADER = bytes((0x55, 0xAA, 0xDC))

FRAME_A1 = 0x1A
FRAME_C1 = 0x1C
FRAME_C2 = 0x2C
FRAME_E1 = 0x1E
FRAME_E2 = 0x2E
FRAME_S1 = 0x16


A1_SPEED = 0x01
A1_HOME = 0x04
A1_TRACK = 0x06
A1_FOLLOW_YAW = 0x03
A1_FOLLOW_YAW_DISABLE = 0x0A
A1_ABSOLUTE_ANGLE = 0x0B
A1_POINT_MOVE = 0x0E
A1_ABSOLUTE_ANGLE_720 = 0x1B

C1_IMAGE_NONE = 0x00
C1_IMAGE_EO = 0x01
C1_IMAGE_IR = 0x02

C1_STOP_FOCUS_ZOOM = 0x01
C1_ZOOM_OUT = 0x08
C1_ZOOM_IN = 0x09
C1_FOCUS_OUT = 0x0A
C1_FOCUS_IN = 0x0B
C1_WHITE_HOT = 0x0E
C1_BLACK_HOT = 0x0F
C1_TAKE_PHOTO = 0x13
C1_START_RECORDING = 0x14
C1_STOP_RECORDING = 0x15
C1_PHOTO_MODE = 0x16
C1_RECORD_MODE = 0x17
C1_AUTO_FOCUS = 0x19
C1_MANUAL_FOCUS = 0x1A
C1_IR_DZOOM_IN = 0x1B
C1_IR_DZOOM_OUT = 0x1C
C1_EO_DZOOM_IN = 0x31
C1_EO_DZOOM_OUT = 0x32
C1_EO_DZOOM_STOP = 0x3C
C1_IR_DZOOM_STOP = 0x3C
C1_IR_DZOOM_CONTINUOUS_IN = 0x3D
C1_IR_DZOOM_CONTINUOUS_OUT = 0x3E

C2_EO_DZOOM_ON = 0x06
C2_EO_DZOOM_OFF = 0x07
C2_DEFOG_OFF = 0x16
C2_DEFOG_ON = 0x17
C2_TEMPERATURE_OUTPUT_ONCE = 0x1D
C2_TEMPERATURE_OUTPUT_INTERVAL = 0x1E
C2_TEMPERATURE_OUTPUT_STOP = 0x1F
C2_ZOOM_TO = 0x53
C2_FOCUS_TO = 0x54
C2_IR_DZOOM_TO = 0x56
C2_LASER_CONTROL = 0x74

LASER_NONE = 0x00
LASER_ON = 0x01
LASER_SINGLE = 0x02
LASER_OFF = 0x05

E1_STOP = 0x01
E1_START_TRACK = 0x03

E2_TRACK_POINT = 0x0A
E2_TRACK_RECT_TOP_LEFT = 0x0B
E2_TRACK_RECT_BOTTOM_RIGHT = 0x0C
E2_FOCUS_POINT = 0x0D
E2_EXPOSURE_POINT = 0x0E

S1_LOOK_AT = 0x01


def build_frame(frame_id: int, data: Iterable[int] = (), *, counter: int = 0) -> bytes:
    """Build a ViewLink frame with the V3.4.9 length/counter/checksum rules."""
    body = bytes((_u8(frame_id), *(_u8(value) for value in data)))
    length = len(body) + 2
    if length < 4 or length > 63:
        raise ValueError(f"ViewLink body length out of range: {length}")
    length_byte = ((int(counter) & 0x03) << 6) | length
    checksum = length_byte
    for value in body:
        checksum ^= value
    return HEADER + bytes((length_byte,)) + body + bytes((checksum,))


def frame_hex(frame: bytes) -> str:
    return frame.hex(" ").upper()


def build_a1(command: int, param1: int = 0, param2: int = 0, param3: int = 0, param4: int = 0) -> bytes:
    return build_frame(
        FRAME_A1,
        (
            _u8(command),
            *_i16be(param1),
            *_i16be(param2),
            *_i16be(param3),
            *_i16be(param4),
        ),
    )


def build_a1_speed(yaw_speed: float = 0.0, pitch_speed: float = 0.0, roll_speed: float = 0.0) -> bytes:
    return build_a1(
        A1_SPEED,
        _speed_to_protocol(yaw_speed),
        _speed_to_protocol(pitch_speed),
        _speed_to_protocol(roll_speed),
        0,
    )


def build_a1_absolute(yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0, speed: float = 0.0) -> bytes:
    return build_a1(
        A1_ABSOLUTE_ANGLE,
        _angle360_to_protocol(yaw),
        _angle360_to_protocol(pitch),
        _angle360_to_protocol(roll),
        _speed_to_protocol(speed, signed=False),
    )


def build_a1_absolute_720(yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0, speed: float = 0.0) -> bytes:
    return build_a1(
        A1_ABSOLUTE_ANGLE_720,
        _angle720_to_protocol(yaw),
        _angle720_to_protocol(pitch),
        _angle720_to_protocol(roll),
        _speed_to_protocol(speed, signed=False),
    )


def build_a1_point_move(x: float = 0.0, y: float = 0.0, yaw_speed: float = 0.0, pitch_speed: float = 0.0) -> bytes:
    return build_a1(
        A1_POINT_MOVE,
        _speed_to_protocol(yaw_speed, signed=False),
        _clamp_int(round(x), -960, 960),
        _speed_to_protocol(pitch_speed, signed=False),
        _clamp_int(round(y), -540, 540),
    )


def build_c1(
    *,
    image_type: int = 0,
    operation: int = 0,
    operation_param: int = 0,
    laser: int = 0,
) -> bytes:
    value = (
        (_clamp_int(image_type, 0, 7) & 0x07)
        | ((_clamp_int(operation_param, 0, 7) & 0x07) << 3)
        | ((_clamp_int(operation, 0, 0x7F) & 0x7F) << 6)
        | ((_clamp_int(laser, 0, 7) & 0x07) << 13)
    )
    return build_frame(FRAME_C1, _u16be(value))


def build_c2(command: int, value: int = 0) -> bytes:
    return build_frame(FRAME_C2, (_u8(command), *_u16be(value)))


def build_e1(command: int, *, sensor: int = 1, param1: int = 0, param2: int = 0) -> bytes:
    first = (_clamp_int(sensor, 0, 7) & 0x07) | ((_clamp_int(param1, 0, 31) & 0x1F) << 3)
    return build_frame(FRAME_E1, (first, _u8(command), _u8(param2)))


def build_e2(command: int, param1: int = 0, param2: int = 0) -> bytes:
    return build_frame(FRAME_E2, (_u8(command), *_i16be(param1), *_i16be(param2)))


def build_screen_split_frame(mode: str | int = "visible_ir") -> bytes:
    image_type = _screen_split_image_type(mode)
    return build_c1(image_type=image_type)


def build_temperature_output_once_frame() -> bytes:
    return build_c2(C2_TEMPERATURE_OUTPUT_ONCE)


def build_temperature_output_interval_frame(interval_frames: int) -> bytes:
    return build_c2(C2_TEMPERATURE_OUTPUT_INTERVAL, _clamp_int(interval_frames, 1, 65535))


def build_temperature_output_stop_frame() -> bytes:
    return build_c2(C2_TEMPERATURE_OUTPUT_STOP)


def build_eo_dzoom_stop_frame() -> bytes:
    return build_c1(image_type=C1_IMAGE_EO, operation=C1_EO_DZOOM_STOP)


def build_ir_dzoom_stop_frame() -> bytes:
    return build_c1(image_type=C1_IMAGE_IR, operation=C1_IR_DZOOM_STOP)


def build_ir_dzoom_in_frame(speed: int = 5) -> bytes:
    return build_c1(
        image_type=C1_IMAGE_IR,
        operation=C1_IR_DZOOM_CONTINUOUS_IN,
        operation_param=speed,
    )


def build_ir_dzoom_out_frame(speed: int = 5) -> bytes:
    return build_c1(
        image_type=C1_IMAGE_IR,
        operation=C1_IR_DZOOM_CONTINUOUS_OUT,
        operation_param=speed,
    )


def build_ir_metering_point_frames(x: int, y: int) -> list[bytes]:
    return [
        build_e2(E2_TRACK_POINT, _clamp_int(x, -960, 960), _clamp_int(y, -540, 540)),
        build_temperature_output_once_frame(),
    ]


def build_focus_value_frame(value: float | int) -> bytes:
    """Build C2 visible-light absolute focus frame.

    The V3.4.9 PDF names command 0x54 as setting focus to the command
    parameter. Treat the cloud focus_distance field as that absolute protocol
    value so operators can sweep the full lens range during real visual tests.
    """
    return build_c2(C2_FOCUS_TO, _clamp_int(round(float(value)), 0, 65535))


def build_focus_point_frame(x: int, y: int) -> bytes:
    """Build E2 visible-light point-focus frame."""
    return build_e2(E2_FOCUS_POINT, _clamp_int(x, -960, 960), _clamp_int(y, -540, 540))


def build_ir_metering_area_frames(left: int, top: int, right: int, bottom: int) -> list[bytes]:
    left = _clamp_int(left, -960, 960)
    right = _clamp_int(right, -960, 960)
    top = _clamp_int(top, -540, 540)
    bottom = _clamp_int(bottom, -540, 540)
    return [
        build_e2(E2_TRACK_RECT_TOP_LEFT, min(left, right), min(top, bottom)),
        build_e2(E2_TRACK_RECT_BOTTOM_RIGHT, max(left, right), max(top, bottom)),
        build_temperature_output_once_frame(),
    ]


def build_s1_look_at(latitude: float, longitude: float, altitude: float = 0.0) -> bytes:
    return build_frame(
        FRAME_S1,
        (
            S1_LOOK_AT,
            0,
            *_i32be(round(float(latitude) * 10_000_000)),
            *_i32be(round(float(longitude) * 10_000_000)),
            *_i32be(round(float(altitude) * 1000)),
        ),
    )


def build_protocol_frame_for_method(method: str, args: dict) -> bytes | None:
    """Return the closest raw protocol frame for an SDK-style ViewLink method."""
    if method == "move":
        return build_a1_speed(args.get("yaw_speed", 0) / 100.0, args.get("pitch_speed", 0) / 100.0)
    if method == "home":
        return build_a1(A1_HOME)
    if method == "turn_to":
        return build_a1_absolute_720(args.get("yaw", 0.0), args.get("pitch", 0.0), args.get("roll", 0.0), args.get("speed", 0.0))
    if method == "zoom_in":
        return build_c1(operation=C1_ZOOM_IN, operation_param=args.get("speed", 5))
    if method == "zoom_out":
        return build_c1(operation=C1_ZOOM_OUT, operation_param=args.get("speed", 5))
    if method == "stop_zoom":
        return build_c1(operation=C1_STOP_FOCUS_ZOOM)
    if method == "zoom_to":
        return build_c2(C2_ZOOM_TO, round(float(args.get("magnification", 1.0)) * 10))
    if method == "focus_to":
        return build_focus_value_frame(args.get("value", args.get("focus_distance", 0)))
    if method == "focus_point":
        return build_focus_point_frame(args.get("x", 0), args.get("y", 0))
    if method == "focus_in":
        return build_c1(operation=C1_FOCUS_IN, operation_param=args.get("speed", 5))
    if method == "focus_out":
        return build_c1(operation=C1_FOCUS_OUT, operation_param=args.get("speed", 5))
    if method == "stop_focus":
        return build_c1(operation=C1_STOP_FOCUS_ZOOM)
    if method == "set_focus_mode":
        return build_c1(operation=C1_MANUAL_FOCUS if int(args.get("mode", 0)) == 1 else C1_AUTO_FOCUS)
    if method == "switch_eo_digital_zoom":
        return build_c2(C2_EO_DZOOM_ON if args.get("on") else C2_EO_DZOOM_OFF)
    if method == "eo_digital_zoom_stop":
        return build_eo_dzoom_stop_frame()
    if method == "ir_digital_zoom_in":
        return build_ir_dzoom_in_frame(args.get("speed", 5))
    if method == "ir_digital_zoom_out":
        return build_ir_dzoom_out_frame(args.get("speed", 5))
    if method == "ir_digital_zoom_stop":
        return build_ir_dzoom_stop_frame()
    if method == "switch_defog":
        return build_c2(C2_DEFOG_ON if args.get("on") else C2_DEFOG_OFF)
    if method == "switch_laser":
        return build_c2(C2_LASER_CONTROL, LASER_ON if args.get("on") else LASER_OFF)
    if method == "laser_single":
        return build_c1(laser=1)
    if method == "take_photo":
        return build_c1(operation=C1_TAKE_PHOTO)
    if method == "start_recording":
        return build_c1(operation=C1_START_RECORDING)
    if method == "stop_recording":
        return build_c1(operation=C1_STOP_RECORDING)
    if method == "set_record_mode":
        return build_c1(operation=C1_RECORD_MODE if int(args.get("mode", 0)) == 2 else C1_PHOTO_MODE)
    if method == "start_track":
        return build_e1(E1_START_TRACK, sensor=args.get("sensor", 1))
    if method == "stop_track":
        return build_e1(E1_STOP, sensor=args.get("sensor", 1))
    return None


def _screen_split_image_type(mode: str | int) -> int:
    if isinstance(mode, int):
        return _clamp_int(mode, 0, 7)
    key = str(mode or "visible_ir").lower()
    return {
        "visible": 1,
        "normal": 1,
        "eo": 1,
        "ir": 2,
        "infrared": 2,
        "thermal": 2,
        "visible_ir": 3,
        "eo_ir": 3,
        "ir_visible": 4,
        "ir_eo": 4,
        "fusion": 5,
        "dual": 3,
        "split": 3,
    }.get(key, 3)


def _speed_to_protocol(value: float, *, signed: bool = True) -> int:
    scaled = round(float(value) * 100)
    return _clamp_int(scaled, -32768 if signed else 0, 32767 if signed else 65535)


def _angle360_to_protocol(value: float) -> int:
    return _clamp_int(round(float(value) * 65536 / 360), -32768, 32767)


def _angle720_to_protocol(value: float) -> int:
    return _clamp_int(round(float(value) * 32768 / 720), -32768, 32767)


def _i16be(value: int) -> bytes:
    return int(value).to_bytes(2, "big", signed=True)


def _u16be(value: int) -> bytes:
    return _clamp_int(value, 0, 0xFFFF).to_bytes(2, "big", signed=False)


def _i32be(value: int) -> bytes:
    return _clamp_int(value, -2147483648, 2147483647).to_bytes(4, "big", signed=True)


def _u8(value: int) -> int:
    return _clamp_int(value, 0, 0xFF)


def _clamp_int(value: float | int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))
