#!/usr/bin/env python3
"""Minimal ViewLink SDK diagnostics.

Runs VLK_Init and VLK_Connect outside the gateway startup path so native SDK
crashes can be isolated by process exit code.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    Union,
    c_char,
    c_char_p,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose ViewLink VLK_Connect")
    parser.add_argument("--sdk-dir", default="D:/ViewLink/bin/win64")
    parser.add_argument("--ip", default="192.168.1.119")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--loader", choices=["windll", "cdll"], default="windll")
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
    parser.add_argument("--test-move", action="store_true")
    parser.add_argument("--move-yaw", type=int, default=1000)
    parser.add_argument("--move-pitch", type=int, default=0)
    parser.add_argument("--move-seconds", type=float, default=1.0)
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

    os.chdir(args.sdk_dir)
    print(f"cwd.sdk={os.getcwd()}", flush=True)

    lib = load_library(args.sdk_dir, args.loader)
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

    callback = None
    if args.callback == "none":
        lib.VLK_Connect.argtypes = [POINTER(VLK_CONN_PARAM), c_void_p, c_void_p]
    else:
        cb_type = CALLBACK_FACTORIES[args.callback](c_int, c_int, c_char_p, c_int, c_void_p)
        lib.VLK_Connect.argtypes = [POINTER(VLK_CONN_PARAM), cb_type, c_void_p]

        if args.callback_body == "gimbal":
            from src.modules.gimbal_control import GimbalControl

            gimbal = GimbalControl(args.sdk_dir, args.ip, args.port, False)
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

    deadline = time.time() + max(0.0, args.poll_seconds)
    while time.time() < deadline:
        connected = lib.VLK_IsConnected()
        print(f"is_connected={connected}", flush=True)
        time.sleep(0.5)

    if not args.skip_motion_status:
        print_motion_status(lib, "motion_status.before")

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

    if not args.skip_motion_status:
        print_motion_status(lib, "motion_status.after")

    print("calling VLK_UnInit", flush=True)
    lib.VLK_UnInit()
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
