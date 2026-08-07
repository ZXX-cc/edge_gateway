#!/usr/bin/env python3
"""Test Dock3 cloud API payload-control camera_photo_take.

This is intentionally a narrow test.  It uses the existing Dock3 cloud API
services path:

    thing/product/{sn}/services -> thing/product/{sn}/services_reply

and sends only the official routed method used by the gateway for payload
photo control: camera_photo_take.
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


def build_photo_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "payload_index": _payload_index(args),
        "camera_index": args.camera_index,
    }
    if args.camera_type:
        payload["camera_type"] = args.camera_type
    if args.file_name:
        payload["file_name"] = args.file_name
    return payload


def _reply_data(reply: Any) -> dict[str, Any]:
    data = reply.get("data") if isinstance(reply, dict) else {}
    return data if isinstance(data, dict) else {}


def _media_hint(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("local_cache", "media", "file", "photo", "viewlink_result"):
        node = data.get(key)
        if isinstance(node, dict):
            return node
    return {}


def validate_photo_reply(reply: dict[str, Any] | None, args: argparse.Namespace) -> int:
    code = result_code(reply)
    if code != 0:
        print(f"camera_photo_take result is not 0: {json.dumps(reply, ensure_ascii=False)}", file=sys.stderr)
        return 4

    data = _reply_data(reply)
    expected_camera_index = args.camera_index
    actual_camera_index = data.get("camera_index")
    if actual_camera_index is not None and actual_camera_index != expected_camera_index:
        print(
            f"camera_index mismatch: expected={expected_camera_index}, actual={actual_camera_index}",
            file=sys.stderr,
        )
        return 5

    if args.expect_source and data.get("photo_source") != args.expect_source:
        print(
            f"photo_source mismatch: expected={args.expect_source}, actual={data.get('photo_source')}",
            file=sys.stderr,
        )
        return 6

    if args.require_media_hint:
        media = _media_hint(data)
        if not any(media.get(key) for key in ("file_path", "local_path", "path", "download_url", "url")):
            print(
                "photo reply did not include media path/url hint: "
                + json.dumps(reply, ensure_ascii=False),
                file=sys.stderr,
            )
            return 7

    return 0


def validate_photo_progress_event(client: MqttRequestClient, args: argparse.Namespace, previous_count: int) -> int:
    legacy_count = client.event_count("photo_take_progress")
    rc, event = client.wait_event(
        "camera_photo_take_progress",
        previous_count,
        args.progress_timeout,
        expected_status=None,
    )
    if rc != 0:
        if client.event_count("photo_take_progress") > legacy_count:
            print(
                "observed nonstandard event method photo_take_progress; "
                "Dock3 Cloud API requires camera_photo_take_progress",
                file=sys.stderr,
            )
            return 8
        return rc

    data = event.get("data") if isinstance(event, dict) else {}
    output = data.get("output") if isinstance(data, dict) else {}
    progress = output.get("progress") if isinstance(output, dict) else {}
    if not isinstance(output, dict) or not isinstance(progress, dict):
        print(
            "camera_photo_take_progress event must contain data.output.progress: "
            + json.dumps(event, ensure_ascii=False),
            file=sys.stderr,
        )
        return 9

    status = output.get("status")
    if status not in {"in_progress", "ok", "failed"}:
        print(
            f"camera_photo_take_progress output.status is not official-style: {status}",
            file=sys.stderr,
        )
        return 10

    if not isinstance(progress.get("percent"), int):
        print(
            "camera_photo_take_progress data.output.progress.percent must be an integer: "
            + json.dumps(event, ensure_ascii=False),
            file=sys.stderr,
        )
        return 11

    print("camera_photo_take_progress event:")
    print(json.dumps(event, ensure_ascii=False, indent=2))
    return 0


def run_photo_take_check(client: MqttRequestClient, args: argparse.Namespace) -> int:
    payload = build_photo_payload(args)
    previous_progress = client.event_count("camera_photo_take_progress")
    rc, reply = client.request(
        "camera_photo_take",
        payload,
        args.timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc

    rc = validate_photo_reply(reply, args)
    if rc != 0:
        return rc

    if not args.skip_progress_event:
        rc = validate_photo_progress_event(client, args, previous_progress)
        if rc != 0:
            return rc

    print("camera_photo_take reply:")
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    print(
        "dock3 payload photo-take test ok "
        f"(payload_index={payload['payload_index']}, camera_index={payload['camera_index']}, "
        f"camera_type={payload.get('camera_type', '')})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test payload-control start-photo through Dock3 cloud API services."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--progress-timeout", type=float, default=10.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--payload-index", default="", help="Dock3 payload index, e.g. 52-0-0.")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-type", default="zoom")
    parser.add_argument("--file-name", default="", help="Optional file_name passed through to camera_photo_take.")
    parser.add_argument(
        "--expect-source",
        choices=["viewlink_sdk", "mavlink", "gateway_direct"],
        default="",
        help="Optionally assert the gateway reply data.photo_source value.",
    )
    parser.add_argument(
        "--require-media-hint",
        action="store_true",
        help="Require reply data to include a media/local_cache path or download URL.",
    )
    parser.add_argument(
        "--skip-progress-event",
        action="store_true",
        help="Only validate camera_photo_take reply; do not require official camera_photo_take_progress event.",
    )
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_payload_photo_take",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_photo_take_check(client, args)
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
