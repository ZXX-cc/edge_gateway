#!/usr/bin/env python3
"""Test Dock3 cloud API payload-control camera_recording_start.

This script intentionally uses only the existing Dock3 cloud API services
method routed by the gateway:

    camera_recording_start

It can optionally call the existing camera_recording_stop method for cleanup,
but it never uses direct ViewLink SDK helpers or locally invented methods.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from src.payload.dock3_payload import payload_index_from_config
from tool_config import add_config_argument, load_config, resolve_mqtt_settings


def _payload_index(args: argparse.Namespace) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def build_recording_start_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "payload_index": _payload_index(args),
        "camera_index": args.camera_index,
        "resolution": args.resolution,
        "framerate": args.framerate,
    }
    if args.camera_type:
        payload["camera_type"] = args.camera_type
    if args.encoding:
        payload["encoding"] = args.encoding
    if args.video_id:
        payload["video_id"] = args.video_id
    if args.file_name:
        payload["file_name"] = args.file_name
    return payload


def build_recording_stop_payload(args: argparse.Namespace, start_payload: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "payload_index": start_payload["payload_index"],
        "camera_index": args.camera_index,
    }
    for key in ("camera_type", "video_id", "file_name"):
        if start_payload.get(key):
            payload[key] = start_payload[key]
    return payload


def _reply_data(reply: Any) -> dict[str, Any]:
    data = reply.get("data") if isinstance(reply, dict) else {}
    return data if isinstance(data, dict) else {}


def validate_recording_start_reply(reply: dict[str, Any] | None, args: argparse.Namespace) -> int:
    code = result_code(reply)
    if code != 0:
        print(
            "camera_recording_start result is not 0: "
            + json.dumps(reply, ensure_ascii=False),
            file=sys.stderr,
        )
        return 4

    data = _reply_data(reply)
    actual_camera_index = data.get("camera_index")
    if actual_camera_index is not None and actual_camera_index != args.camera_index:
        print(
            f"camera_index mismatch: expected={args.camera_index}, actual={actual_camera_index}",
            file=sys.stderr,
        )
        return 5

    if args.expect_recording_flag:
        recording = data.get("recording")
        if recording is not None and recording is not True:
            print(
                "camera_recording_start reply has unexpected recording flag: "
                + json.dumps(reply, ensure_ascii=False),
                file=sys.stderr,
            )
            return 6

    return 0


def run_recording_start_check(client: MqttRequestClient, args: argparse.Namespace) -> int:
    payload = build_recording_start_payload(args)
    rc, reply = client.request(
        "camera_recording_start",
        payload,
        args.timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc

    rc = validate_recording_start_reply(reply, args)
    if rc != 0:
        return rc

    print("camera_recording_start reply:")
    print(json.dumps(reply, ensure_ascii=False, indent=2))

    if args.stop_after_start:
        stop_payload = build_recording_stop_payload(args, payload)
        rc, stop_reply = client.request(
            "camera_recording_stop",
            stop_payload,
            args.timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc
        print("camera_recording_stop cleanup reply:")
        print(json.dumps(stop_reply, ensure_ascii=False, indent=2))

    print(
        "dock3 payload recording-start test ok "
        f"(payload_index={payload['payload_index']}, camera_index={payload['camera_index']}, "
        f"camera_type={payload.get('camera_type', '')}, resolution={payload['resolution']}, "
        f"framerate={payload['framerate']})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test payload-control start-recording through Dock3 cloud API services."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--payload-index", default="", help="Dock3 payload index, e.g. 52-0-0.")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-type", default="zoom")
    parser.add_argument("--resolution", default="1080p")
    parser.add_argument("--framerate", type=int, default=30)
    parser.add_argument("--encoding", default="h264")
    parser.add_argument("--video-id", default="recording-start-test")
    parser.add_argument("--file-name", default="", help="Optional file_name passed through to camera_recording_start.")
    parser.add_argument(
        "--expect-recording-flag",
        action="store_true",
        help="If reply data contains recording, require it to be true.",
    )
    parser.add_argument(
        "--stop-after-start",
        action="store_true",
        help="After start succeeds, clean up with existing camera_recording_stop.",
    )
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.framerate <= 0:
        parser.error("--framerate must be positive")

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_payload_recording_start",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_recording_start_check(client, args)
        if rc != 0:
            return rc

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 10
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
