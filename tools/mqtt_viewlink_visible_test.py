#!/usr/bin/env python3
"""Run visible, non-recording ViewLink gimbal checks through the gateway."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient
from tool_config import add_config_argument, resolve_mqtt_settings


def _data(reply: Any) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {}
    data = reply.get("data")
    return data if isinstance(data, dict) else {}


def _viewlink_result(reply: Any) -> dict[str, Any]:
    result = _data(reply).get("viewlink_result")
    return result if isinstance(result, dict) else {}


def _request_viewlink(client, method: str, data: dict[str, Any], timeout: float) -> tuple[int, Any]:
    rc, reply = client.request(
        method,
        data,
        timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        print(f"{method} failed at MQTT/gateway layer: rc={rc}", file=sys.stderr)
        return rc, reply
    result = _viewlink_result(reply)
    if not result.get("success", False):
        print(f"{method} did not report SDK success: {result}", file=sys.stderr)
        return 20, reply
    print(f"ok {method}: {result.get('message', '')}")
    return 0, reply


def _check_bound(client, timeout: float) -> int:
    rc, reply = client.request(
        "workspace_bind_status",
        {},
        timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc
    data = _data(reply)
    if data.get("is_bind") not in {True, 1, "1"}:
        print("gateway is unbound; sending local test device_binding")
        rc, _ = client.request(
            "device_binding",
            {
                "workspace_id": "local_test_workspace",
                "workspace_name": "本地测试工作空间",
            },
            timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc
        rc, reply = client.request(
            "workspace_bind_status",
            {},
            timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc
        data = _data(reply)
        if data.get("is_bind") not in {True, 1, "1"}:
            print("gateway is still unbound after device_binding", file=sys.stderr)
            return 23
    print("ok workspace_bind_status: bound")
    return 0


def _runtime_gimbal_control(client, timeout: float) -> tuple[int, dict[str, Any]]:
    rc, reply = client.request(
        "payload_status_query",
        {},
        timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc, {}

    payloads = _data(reply).get("payloads")
    if not isinstance(payloads, list):
        print("payload_status_query did not return payloads[]", file=sys.stderr)
        return 21, {}
    for payload in payloads:
        if not isinstance(payload, dict) or payload.get("id") != "gimbal":
            continue
        channels = payload.get("channels")
        control = channels.get("control") if isinstance(channels, dict) else None
        if isinstance(control, dict):
            print(
                "ok payload_status_query: "
                f"gimbal host={control.get('host')} port={control.get('port')} "
                f"state={payload.get('connection_state')}"
            )
            return 0, control
    print("payload_status_query did not return gimbal control configuration", file=sys.stderr)
    return 22, {}


def _ensure_connected(client, args) -> int:
    rc = _check_bound(client, args.timeout)
    if rc != 0:
        return rc

    rc, runtime_control = _runtime_gimbal_control(client, args.timeout)
    if rc != 0:
        return rc

    sdk_dir = args.sdk_dir or str(runtime_control.get("sdk_dir") or "")
    gimbal_ip = args.gimbal_ip or str(runtime_control.get("host") or "")
    gimbal_port = args.gimbal_port or int(runtime_control.get("port") or 0)
    if not sdk_dir or not gimbal_ip or not gimbal_port:
        print("incomplete gimbal control configuration; sdk_dir, host and port are required", file=sys.stderr)
        return 24

    for method, data in (
        ("viewlink_initialize", {"sdk_dir": sdk_dir}),
        ("viewlink_connect", {"ip": gimbal_ip, "port": gimbal_port, "conn_type": "tcp"}),
        ("viewlink_is_connected", {}),
    ):
        rc, reply = _request_viewlink(client, method, data, args.timeout)
        if rc != 0:
            return rc
        if method == "viewlink_is_connected" and _viewlink_result(reply).get("connected") is not True:
            print("ViewLink reports connected=false", file=sys.stderr)
            return 25
    return 0


def _run_visible_motion(client, args, sleep_fn: Callable[[float], None]) -> int:
    rc = _ensure_connected(client, args)
    if rc != 0:
        return rc

    checks: list[tuple[str, str, dict[str, Any], float, str | None]] = [
        ("home", "viewlink_home", {}, args.settle_seconds, None),
        ("yaw right", "viewlink_move", {"yaw_speed": args.yaw_speed, "pitch_speed": 0}, args.move_duration, "viewlink_stop"),
        ("yaw left", "viewlink_move", {"yaw_speed": -args.yaw_speed, "pitch_speed": 0}, args.move_duration, "viewlink_stop"),
        ("pitch up", "viewlink_move", {"yaw_speed": 0, "pitch_speed": args.pitch_speed}, args.move_duration, "viewlink_stop"),
        ("pitch down", "viewlink_move", {"yaw_speed": 0, "pitch_speed": -args.pitch_speed}, args.move_duration, "viewlink_stop"),
        ("turn to neutral", "viewlink_turn_to", {"yaw": args.turn_yaw, "pitch": args.turn_pitch}, args.settle_seconds, None),
        ("zoom in", "viewlink_zoom_in", {"speed": args.zoom_speed}, args.zoom_duration, "viewlink_stop_zoom"),
        ("zoom out", "viewlink_zoom_out", {"speed": args.zoom_speed}, args.zoom_duration, "viewlink_stop_zoom"),
    ]

    for label, method, data, duration, stop_method in checks:
        print(f"running {label} -> {method}")
        rc, _reply = _request_viewlink(client, method, data, args.timeout)
        try:
            if rc == 0 and duration > 0:
                sleep_fn(duration)
        finally:
            if stop_method:
                stop_rc, _ = _request_viewlink(client, stop_method, {}, args.timeout)
                if rc == 0 and stop_rc != 0:
                    rc = stop_rc
        if rc != 0:
            return rc
        if args.pause_between > 0:
            sleep_fn(args.pause_between)

    print("viewlink visible test ok")
    return 0


def _dry_run_plan(args) -> None:
    plan = [
        ["workspace_bind_status", {}],
        ["payload_status_query", {}],
        ["viewlink_initialize", {"sdk_dir": args.sdk_dir or "<runtime sdk_dir>"}],
        [
            "viewlink_connect",
            {
                "ip": args.gimbal_ip or "<runtime host>",
                "port": args.gimbal_port or "<runtime port>",
                "conn_type": "tcp",
            },
        ],
        ["viewlink_is_connected", {}],
        ["viewlink_home", {}],
        ["viewlink_move", {"yaw_speed": args.yaw_speed, "pitch_speed": 0}],
        ["viewlink_stop", {}],
        ["viewlink_move", {"yaw_speed": -args.yaw_speed, "pitch_speed": 0}],
        ["viewlink_stop", {}],
        ["viewlink_move", {"yaw_speed": 0, "pitch_speed": args.pitch_speed}],
        ["viewlink_stop", {}],
        ["viewlink_move", {"yaw_speed": 0, "pitch_speed": -args.pitch_speed}],
        ["viewlink_stop", {}],
        ["viewlink_turn_to", {"yaw": args.turn_yaw, "pitch": args.turn_pitch}],
        ["viewlink_zoom_in", {"speed": args.zoom_speed}],
        ["viewlink_stop_zoom", {}],
        ["viewlink_zoom_out", {"speed": args.zoom_speed}],
        ["viewlink_stop_zoom", {}],
    ]
    print(json.dumps({"dry_run": True, "mqtt_methods": plan}, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run visible, non-recording ViewLink checks over MQTT")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--confirm-device-sn", default="")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--sdk-dir", default="")
    parser.add_argument("--gimbal-ip", default="")
    parser.add_argument("--gimbal-port", type=int, default=0)
    parser.add_argument("--yaw-speed", type=int, default=500)
    parser.add_argument("--pitch-speed", type=int, default=500)
    parser.add_argument("--move-duration", type=float, default=0.8)
    parser.add_argument("--zoom-speed", type=int, default=2)
    parser.add_argument("--zoom-duration", type=float, default=0.8)
    parser.add_argument("--turn-yaw", type=float, default=0.0)
    parser.add_argument("--turn-pitch", type=float, default=0.0)
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    parser.add_argument("--pause-between", type=float, default=0.4)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.confirm_device_sn and args.confirm_device_sn != args.sn:
        parser.error("--confirm-device-sn must exactly match --sn")
    if not 1 <= args.zoom_speed <= 8:
        parser.error("--zoom-speed must be between 1 and 8")
    if not 1 <= args.yaw_speed <= 2000 or not 1 <= args.pitch_speed <= 2000:
        parser.error("--yaw-speed and --pitch-speed must be between 1 and 2000")
    if not 0.1 <= args.move_duration <= 5.0:
        parser.error("--move-duration must be between 0.1 and 5 seconds")
    if not 0.1 <= args.zoom_duration <= 5.0:
        parser.error("--zoom-duration must be between 0.1 and 5 seconds")
    if args.settle_seconds < 0 or args.pause_between < 0:
        parser.error("settle/pause values must be non-negative")

    if args.dry_run:
        _dry_run_plan(args)
        return 0

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_viewlink_visible",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2
    try:
        return _run_visible_motion(client, args, time.sleep)
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
