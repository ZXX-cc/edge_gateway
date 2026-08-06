#!/usr/bin/env python3
"""Safely zoom and photograph through the running gateway's ViewLink adapter.

The ViewLink SDK stores photographs on the gimbal SD card and does not expose a
file-download API.  When ``--video-url`` is provided, this tool also asks a
local FFmpeg process to save one preview frame from that stream.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
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


def _request_viewlink(client, method: str, data: dict[str, Any], timeout: float):
    rc, reply = client.request(
        method,
        data,
        timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc, reply
    result = _viewlink_result(reply)
    if not result.get("success", False):
        print(f"{method} did not report SDK success: {result}", file=sys.stderr)
        return 20, reply
    return 0, reply


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
            return 0, control
    print("payload_status_query did not return gimbal control configuration", file=sys.stderr)
    return 22, {}


def _check_bound(client, timeout: float) -> int:
    rc, reply = client.request(
        "gateway_state_query",
        {"compact": True},
        timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc
    data = _data(reply)
    state = data.get("state", {}) if isinstance(data, dict) else {}
    if state.get("device") != "bound":
        print(
            "gateway is unbound; complete the official organization requests flow before ViewLink actions",
            file=sys.stderr,
        )
        return 23
    return 0


def _capture_preview(
    video_url: str,
    output_path: Path,
    ffmpeg_bin: str,
    timeout: float,
) -> int:
    executable = shutil.which(ffmpeg_bin)
    if not executable and Path(ffmpeg_bin).is_file():
        executable = ffmpeg_bin
    if not executable:
        print(f"ffmpeg executable not found: {ffmpeg_bin}", file=sys.stderr)
        return 30

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [executable, "-hide_banner", "-loglevel", "error", "-y"]
    if video_url.lower().startswith("rtsp://"):
        command.extend(["-rtsp_transport", "tcp"])
    command.extend(["-i", video_url, "-frames:v", "1", "-q:v", "2", str(output_path)])
    try:
        completed = subprocess.run(command, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"preview capture timed out after {timeout:.1f}s", file=sys.stderr)
        return 31
    if completed.returncode != 0 or not output_path.is_file():
        print(f"preview capture failed: ffmpeg exit={completed.returncode}", file=sys.stderr)
        return 32
    print(f"preview_frame={output_path.resolve()}")
    return 0


def run_zoom_photo(
    client,
    args,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
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
        print(
            "incomplete gimbal control configuration; sdk_dir, host and port are required",
            file=sys.stderr,
        )
        return 24

    rc, _ = _request_viewlink(
        client,
        "viewlink_initialize",
        {"sdk_dir": sdk_dir},
        args.timeout,
    )
    if rc != 0:
        return rc
    rc, _ = _request_viewlink(
        client,
        "viewlink_connect",
        {"ip": gimbal_ip, "port": gimbal_port, "conn_type": "tcp"},
        args.timeout,
    )
    if rc != 0:
        return rc
    rc, connected_reply = _request_viewlink(
        client,
        "viewlink_is_connected",
        {},
        args.timeout,
    )
    if rc != 0:
        return rc
    if _viewlink_result(connected_reply).get("connected") is not True:
        print("ViewLink reports connected=false", file=sys.stderr)
        return 25

    rc, _ = _request_viewlink(
        client,
        "viewlink_set_record_mode",
        {"mode": "PHOTO"},
        args.timeout,
    )
    if rc != 0:
        return rc
    sleep_fn(args.settle_seconds)

    zoom_method = f"viewlink_zoom_{args.zoom_direction}"
    zoom_requested = False
    zoom_rc = 0
    stop_rc = 0
    try:
        zoom_requested = True
        zoom_rc, _ = _request_viewlink(
            client,
            zoom_method,
            {"speed": args.zoom_speed},
            args.timeout,
        )
        if zoom_rc == 0:
            sleep_fn(args.zoom_duration)
    finally:
        if zoom_requested:
            stop_rc, _ = _request_viewlink(
                client,
                "viewlink_stop_zoom",
                {},
                args.timeout,
            )
    if zoom_rc != 0:
        return zoom_rc
    if stop_rc != 0:
        return stop_rc

    sleep_fn(args.settle_seconds)
    rc, photo_reply = _request_viewlink(
        client,
        "viewlink_take_photo",
        {},
        args.timeout,
    )
    if rc != 0:
        return rc
    sleep_fn(args.photo_wait)

    if args.video_url:
        rc = _capture_preview(
            args.video_url,
            Path(args.snapshot_output),
            args.ffmpeg,
            args.preview_timeout,
        )
        if rc != 0:
            return rc

    if args.restore_zoom:
        restore_method = "viewlink_zoom_out" if args.zoom_direction == "in" else "viewlink_zoom_in"
        restore_rc = 0
        restore_stop_rc = 0
        try:
            restore_rc, _ = _request_viewlink(
                client,
                restore_method,
                {"speed": args.zoom_speed},
                args.timeout,
            )
            if restore_rc == 0:
                sleep_fn(args.zoom_duration)
        finally:
            restore_stop_rc, _ = _request_viewlink(
                client,
                "viewlink_stop_zoom",
                {},
                args.timeout,
            )
        if restore_rc != 0:
            return restore_rc
        if restore_stop_rc != 0:
            return restore_stop_rc

    message = _viewlink_result(photo_reply).get("message", "photo command sent")
    print(
        "viewlink zoom/photo test ok "
        f"(direction={args.zoom_direction}, speed={args.zoom_speed}, "
        f"duration={args.zoom_duration:.1f}s, sdk_message={message!r})"
    )
    if not args.video_url:
        print(
            "photo content is stored on the gimbal SD card; provide --video-url "
            "to save a local preview frame"
        )
    return 0


def _dry_run_plan(args) -> None:
    plan = [
        ["gateway_state_query", {"compact": True}],
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
        ["viewlink_set_record_mode", {"mode": "PHOTO"}],
        [f"viewlink_zoom_{args.zoom_direction}", {"speed": args.zoom_speed}],
        ["viewlink_stop_zoom", {}],
        ["viewlink_take_photo", {}],
    ]
    if args.restore_zoom:
        opposite = "out" if args.zoom_direction == "in" else "in"
        plan.extend(
            [
                [f"viewlink_zoom_{opposite}", {"speed": args.zoom_speed}],
                ["viewlink_stop_zoom", {}],
            ]
        )
    print(json.dumps({"dry_run": True, "mqtt_methods": plan}, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely zoom and photograph a ViewLink gimbal over MQTT")
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
    parser.add_argument("--zoom-direction", choices=["in", "out"], default="in")
    parser.add_argument("--zoom-speed", type=int, default=2)
    parser.add_argument("--zoom-duration", type=float, default=1.0)
    parser.add_argument("--settle-seconds", type=float, default=0.5)
    parser.add_argument("--photo-wait", type=float, default=1.0)
    parser.add_argument("--restore-zoom", action="store_true")
    parser.add_argument("--video-url", default="")
    parser.add_argument(
        "--snapshot-output",
        default=str(PROJECT_ROOT / "logs" / "viewlink_capture" / "viewlink_zoom_photo.jpg"),
    )
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--preview-timeout", type=float, default=20.0)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.confirm_device_sn and args.confirm_device_sn != args.sn:
        parser.error("--confirm-device-sn must exactly match --sn")
    if not 1 <= args.zoom_speed <= 8:
        parser.error("--zoom-speed must be between 1 and 8")
    if not 0.1 <= args.zoom_duration <= 10.0:
        parser.error("--zoom-duration must be between 0.1 and 10 seconds")
    if args.settle_seconds < 0 or args.photo_wait < 0:
        parser.error("settle/photo wait values must be non-negative")

    if args.dry_run:
        _dry_run_plan(args)
        return 0

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_viewlink_zoom_photo",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2
    try:
        return run_zoom_photo(client, args)
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
