#!/usr/bin/env python3
"""Minimal ViewLink SDK diagnostics.

Runs VLK_Init and VLK_Connect outside the gateway startup path so native SDK
crashes can be isolated by process exit code.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import sys
import time
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    Union,
    c_char,
    c_char_p,
    c_double,
    c_int,
    c_short,
    c_void_p,
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


if os.name == "nt":
    CALLBACK_FACTORIES = {
        "cdecl": CFUNCTYPE,
        "stdcall": ctypes.WINFUNCTYPE,
    }
else:
    CALLBACK_FACTORIES = {
        "cdecl": CFUNCTYPE,
        "stdcall": CFUNCTYPE,
    }


class VLK_DEV_IPADDR(Structure):
    _fields_ = [
        ("szIPV4", c_char * 16),
        ("iPort", c_int),
    ]


class VLK_DEV_SERIAL_PORT(Structure):
    _fields_ = [
        ("szSerialPortName", c_char * 16),
        ("iBaudRate", c_int),
    ]


class ConnParamUnion(Union):
    _fields_ = [
        ("IPAddr", VLK_DEV_IPADDR),
        ("SerialPort", VLK_DEV_SERIAL_PORT),
    ]


class VLK_CONN_PARAM(Structure):
    _fields_ = [
        ("emType", c_int),
        ("ConnParam", ConnParamUnion),
    ]


class VLK_DEV_TELEMETRY(Structure):
    _fields_ = [
        ("dYaw", c_double),
        ("dPitch", c_double),
        ("dRoll", c_double),
        ("emSensorType", c_int),
        ("emTrackerStatus", c_int),
        ("dTargetLat", c_double),
        ("dTargetLng", c_double),
        ("dTargetAlt", c_double),
        ("dDroneLat", c_double),
        ("dDroneLng", c_double),
        ("dDroneAlt", c_double),
        ("dFov", c_double),
        ("dVertical", c_double),
    ]


def _library_path(sdk_dir: str) -> str:
    if os.name == "nt":
        return os.path.join(sdk_dir, "ViewLink.dll")
    if sys.platform.startswith("linux"):
        return os.path.join(sdk_dir, "libViewLink.so")
    return os.path.join(sdk_dir, "ViewLink.dll")


def load_library(sdk_dir: str, loader: str):
    dll_path = _library_path(sdk_dir)
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(os.path.abspath(sdk_dir))
    if loader == "windll" and os.name == "nt":
        return ctypes.WinDLL(dll_path)
    return ctypes.CDLL(dll_path)


def bind_optional(lib, name: str, argtypes: list, restype):
    try:
        func = getattr(lib, name)
    except AttributeError:
        print(f"{name}=unavailable", flush=True)
        return None
    func.argtypes = argtypes
    func.restype = restype
    return func


def read_int_status(lib, name: str):
    func = getattr(lib, name, None)
    if func is None:
        return "unavailable"
    try:
        return int(func())
    except Exception as exc:
        return f"error:{exc}"


def print_motion_status(lib, label: str) -> None:
    connected = read_int_status(lib, "VLK_IsConnected")
    motor_on = read_int_status(lib, "VLK_IsMotorOn")
    follow_mode = read_int_status(lib, "VLK_IsFollowMode")
    tracking = read_int_status(lib, "VLK_IsTracking")
    print(
        (
            f"{label}: connected={connected} "
            f"motor_on={motor_on} follow_mode={follow_mode} tracking={tracking}"
        ),
        flush=True,
    )


def print_telemetry(state: dict, label: str) -> None:
    telemetry = state.get("telemetry")
    if not telemetry:
        print(f"{label}: telemetry=none", flush=True)
        return
    print(
        (
            f"{label}: yaw={telemetry['yaw']:.3f} pitch={telemetry['pitch']:.3f} "
            f"roll={telemetry['roll']:.3f} sensor={telemetry['sensor']} "
            f"tracker={telemetry['tracker']}"
        ),
        flush=True,
    )


def latest_telemetry(state: dict) -> dict | None:
    telemetry = state.get("telemetry")
    return dict(telemetry) if telemetry else None


def angle_delta(after: float, before: float) -> float:
    delta = after - before
    while delta > 180.0:
        delta -= 360.0
    while delta < -180.0:
        delta += 360.0
    return delta


def print_telemetry_delta(before: dict | None, after: dict | None, label: str) -> None:
    if not before or not after:
        print(f"{label}: telemetry_delta=unavailable", flush=True)
        return
    print(
        (
            f"{label}: delta_yaw={angle_delta(after['yaw'], before['yaw']):.3f} "
            f"delta_pitch={after['pitch'] - before['pitch']:.3f} "
            f"delta_roll={after['roll'] - before['roll']:.3f}"
        ),
        flush=True,
    )


def run_axis_sweep(lib, state: dict, speed: int, seconds: float) -> None:
    move = getattr(lib, "VLK_Move", None)
    stop = getattr(lib, "VLK_Stop", None)
    if move is None or stop is None:
        print("axis_sweep=unavailable", flush=True)
        return

    speed = max(1, min(2000, int(speed)))
    duration = max(0.2, min(5.0, float(seconds)))
    cases = [
        ("yaw_positive", speed, 0),
        ("yaw_negative", -speed, 0),
        ("pitch_positive", 0, speed),
        ("pitch_negative", 0, -speed),
    ]
    for label, yaw, pitch in cases:
        time.sleep(0.8)
        before = latest_telemetry(state)
        print_telemetry(state, f"{label}.before")
        print(f"calling VLK_Move({yaw}, {pitch}) for {duration}s", flush=True)
        move(yaw, pitch)
        time.sleep(duration)
        print("calling VLK_Stop()", flush=True)
        stop()
        time.sleep(0.8)
        after = latest_telemetry(state)
        print_telemetry(state, f"{label}.after")
        print_telemetry_delta(before, after, label)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose ViewLink VLK_Connect")
    parser.add_argument("--sdk-dir", default="D:/ViewLink/bin/win64")
    parser.add_argument("--ip", default="192.168.1.119")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--loader", choices=["windll", "cdll"], default="windll")
    parser.add_argument(
        "--work-dir",
        default=".",
        help="runtime working directory for SDK logs; default keeps the command cwd",
    )
    parser.add_argument(
        "--reset-log-dir",
        action="store_true",
        help="remove work-dir/VLKLog before VLK_Init; useful because SDK 3.5.3 fails if it already exists",
    )
    parser.add_argument("--callback", choices=["none", "cdecl", "stdcall"], default="cdecl")
    parser.add_argument(
        "--callback-body",
        choices=["minimal", "decode", "state", "gimbal"],
        default="minimal",
    )
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--skip-motion-status", action="store_true")
    parser.add_argument("--switch-motor", choices=["keep", "on", "off"], default="keep")
    parser.add_argument("--follow-mode", choices=["keep", "on", "off"], default="keep")
    parser.add_argument("--stop-track", action="store_true")
    parser.add_argument("--stop-motion", action="store_true")
    parser.add_argument("--test-move", action="store_true")
    parser.add_argument("--move-yaw", type=int, default=1000)
    parser.add_argument("--move-pitch", type=int, default=0)
    parser.add_argument("--move-seconds", type=float, default=1.0)
    parser.add_argument("--turn-to", action="store_true")
    parser.add_argument("--turn-yaw", type=float, default=30.0)
    parser.add_argument("--turn-pitch", type=float, default=-20.0)
    parser.add_argument("--turn-wait", type=float, default=2.0)
    parser.add_argument("--dev-status", action="store_true")
    parser.add_argument("--axis-sweep", action="store_true")
    parser.add_argument("--axis-sweep-speed", type=int, default=1000)
    parser.add_argument("--axis-sweep-seconds", type=float, default=1.0)
    args = parser.parse_args()

    print(f"cwd.before={os.getcwd()}", flush=True)
    print(
        "sizeof: IPADDR=%s SERIAL=%s PARAM=%s"
        % (
            ctypes.sizeof(VLK_DEV_IPADDR),
            ctypes.sizeof(VLK_DEV_SERIAL_PORT),
            ctypes.sizeof(VLK_CONN_PARAM),
        ),
        flush=True,
    )

    sdk_dir = os.path.abspath(args.sdk_dir)
    work_dir = os.path.abspath(args.work_dir)
    print(f"sdk_dir={sdk_dir}", flush=True)
    os.makedirs(work_dir, exist_ok=True)
    os.chdir(work_dir)
    print(f"cwd.runtime={os.getcwd()}", flush=True)
    log_dir = os.path.join(os.getcwd(), "VLKLog")
    if args.reset_log_dir and os.path.isdir(log_dir):
        shutil.rmtree(log_dir)
        print(f"log_dir.removed={log_dir}", flush=True)
    print(f"log_dir.expected={log_dir}", flush=True)

    lib = load_library(sdk_dir, args.loader)
    lib.GetSDKVersion.argtypes = []
    lib.GetSDKVersion.restype = c_char_p
    lib.VLK_Init.argtypes = []
    lib.VLK_Init.restype = c_int
    lib.VLK_IsConnected.argtypes = []
    lib.VLK_IsConnected.restype = c_int
    lib.VLK_UnInit.argtypes = []
    lib.VLK_UnInit.restype = None
    bind_optional(lib, "VLK_IsMotorOn", [], c_int)
    bind_optional(lib, "VLK_SwitchMotor", [c_int], None)
    bind_optional(lib, "VLK_IsFollowMode", [], c_int)
    bind_optional(lib, "VLK_EnableFollowMode", [c_int], None)
    bind_optional(lib, "VLK_IsTracking", [], c_int)
    bind_optional(lib, "VLK_StopTrack", [], None)
    bind_optional(lib, "VLK_DisableTrackMode", [], None)
    bind_optional(lib, "VLK_Move", [c_short, c_short], None)
    bind_optional(lib, "VLK_Stop", [], None)
    bind_optional(lib, "VLK_TurnTo", [c_double, c_double], None)

    callback = None
    if args.callback == "none":
        lib.VLK_Connect.argtypes = [POINTER(VLK_CONN_PARAM), c_void_p, c_void_p]
    else:
        cb_type = CALLBACK_FACTORIES[args.callback](c_int, c_int, c_char_p, c_int, c_void_p)
        lib.VLK_Connect.argtypes = [POINTER(VLK_CONN_PARAM), cb_type, c_void_p]

        if args.callback_body == "gimbal":
            from src.modules.gimbal_control import GimbalControl

            gimbal = GimbalControl(sdk_dir, args.ip, args.port, False)
            gimbal._lib = lib
            callback = gimbal._create_conn_callback()
        else:
            state = {"status": None, "message": "", "connected": False}

            @cb_type
            def callback(status, message, msg_len, user_param):
                if args.callback_body == "minimal":
                    print(f"callback status={status} msg_len={msg_len}", flush=True)
                    return 0

                msg = message.decode("utf-8") if message else ""
                if args.callback_body == "decode":
                    print(f"callback status={status} message={msg!r}", flush=True)
                    return 0

                state["status"] = status
                state["message"] = msg
                if status in (2, 4):
                    state["connected"] = True
                elif status in (3, 5):
                    state["connected"] = False
                print(f"callback state={state}", flush=True)
                return 0

    lib.VLK_Connect.restype = c_int
    dev_status_callback = None
    dev_status_state = {"telemetry": None}
    if args.dev_status:
        dev_cb_type = CALLBACK_FACTORIES["cdecl"](c_int, c_void_p, c_int, c_void_p)
        lib.VLK_RegisterDevStatusCB.argtypes = [dev_cb_type, c_void_p]
        lib.VLK_RegisterDevStatusCB.restype = None

        @dev_cb_type
        def dev_status_callback(status_type, buffer, buf_len, user_param):
            if status_type == 2 and buffer and buf_len >= ctypes.sizeof(VLK_DEV_TELEMETRY):
                telemetry = ctypes.cast(buffer, POINTER(VLK_DEV_TELEMETRY)).contents
                dev_status_state["telemetry"] = {
                    "yaw": float(telemetry.dYaw),
                    "pitch": float(telemetry.dPitch),
                    "roll": float(telemetry.dRoll),
                    "sensor": int(telemetry.emSensorType),
                    "tracker": int(telemetry.emTrackerStatus),
                }
            return 0

    version = lib.GetSDKVersion()
    print(f"version={version!r}", flush=True)
    init_ret = lib.VLK_Init()
    print(f"VLK_Init ret={init_ret}", flush=True)
    if init_ret != 0:
        return 2

    param = VLK_CONN_PARAM()
    ctypes.memset(ctypes.byref(param), 0, ctypes.sizeof(param))
    param.emType = 1
    encoded_ip = args.ip.encode("utf-8")
    if len(encoded_ip) >= 16:
        print(f"ip too long for szIPV4[16]: {args.ip}", flush=True)
        return 3
    param.ConnParam.IPAddr.szIPV4 = encoded_ip
    param.ConnParam.IPAddr.iPort = args.port

    print(
        (
            f"before VLK_Connect loader={args.loader} callback={args.callback} "
            f"body={args.callback_body} ip={args.ip} port={args.port}"
        ),
        flush=True,
    )
    ret = lib.VLK_Connect(ctypes.byref(param), callback, None)
    print(f"after VLK_Connect ret={ret}", flush=True)

    if args.dev_status:
        print("calling VLK_RegisterDevStatusCB()", flush=True)
        lib.VLK_RegisterDevStatusCB(dev_status_callback, None)

    deadline = time.time() + max(0.0, args.poll_seconds)
    while time.time() < deadline:
        connected = lib.VLK_IsConnected()
        print(f"is_connected={connected}", flush=True)
        time.sleep(0.5)

    if not args.skip_motion_status:
        print_motion_status(lib, "motion_status.before")
    if args.dev_status:
        print_telemetry(dev_status_state, "telemetry.before")

    if args.switch_motor != "keep":
        switch_motor = getattr(lib, "VLK_SwitchMotor", None)
        if switch_motor is None:
            print("switch_motor=unavailable", flush=True)
        else:
            value = 1 if args.switch_motor == "on" else 0
            print(f"calling VLK_SwitchMotor({value})", flush=True)
            switch_motor(value)
            time.sleep(0.5)

    if args.follow_mode != "keep":
        enable_follow = getattr(lib, "VLK_EnableFollowMode", None)
        if enable_follow is None:
            print("follow_mode=unavailable", flush=True)
        else:
            value = 1 if args.follow_mode == "on" else 0
            print(f"calling VLK_EnableFollowMode({value})", flush=True)
            enable_follow(value)
            time.sleep(0.5)

    if args.stop_track:
        stop_track = getattr(lib, "VLK_StopTrack", None)
        disable_track = getattr(lib, "VLK_DisableTrackMode", None)
        if stop_track is None and disable_track is None:
            print("stop_track=unavailable", flush=True)
        if stop_track is not None:
            print("calling VLK_StopTrack()", flush=True)
            stop_track()
        if disable_track is not None:
            print("calling VLK_DisableTrackMode()", flush=True)
            disable_track()
        time.sleep(0.5)

    if args.stop_motion:
        stop = getattr(lib, "VLK_Stop", None)
        if stop is None:
            print("stop_motion=unavailable", flush=True)
        else:
            print("calling VLK_Stop()", flush=True)
            stop()
            time.sleep(0.5)

    if args.test_move:
        move = getattr(lib, "VLK_Move", None)
        stop = getattr(lib, "VLK_Stop", None)
        if move is None or stop is None:
            print("test_move=unavailable", flush=True)
        else:
            yaw = max(-16383, min(16383, int(args.move_yaw)))
            pitch = max(-16383, min(16383, int(args.move_pitch)))
            duration = max(0.1, min(5.0, float(args.move_seconds)))
            print(f"calling VLK_Move({yaw}, {pitch}) for {duration}s", flush=True)
            move(yaw, pitch)
            time.sleep(duration)
            print("calling VLK_Stop()", flush=True)
            stop()
            time.sleep(0.5)

    if args.turn_to:
        turn_to = getattr(lib, "VLK_TurnTo", None)
        if turn_to is None:
            print("turn_to=unavailable", flush=True)
        else:
            yaw = max(-180.0, min(180.0, float(args.turn_yaw)))
            pitch = max(-90.0, min(90.0, float(args.turn_pitch)))
            wait = max(0.1, min(10.0, float(args.turn_wait)))
            print(f"calling VLK_TurnTo({yaw}, {pitch}), wait {wait}s", flush=True)
            turn_to(c_double(yaw), c_double(pitch))
            time.sleep(wait)

    if args.axis_sweep:
        if not args.dev_status:
            print("axis_sweep requires --dev-status", flush=True)
        else:
            run_axis_sweep(lib, dev_status_state, args.axis_sweep_speed, args.axis_sweep_seconds)

    if not args.skip_motion_status:
        print_motion_status(lib, "motion_status.after")
    if args.dev_status:
        print_telemetry(dev_status_state, "telemetry.after")

    print("calling VLK_UnInit", flush=True)
    lib.VLK_UnInit()
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
