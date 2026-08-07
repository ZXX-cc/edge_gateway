#!/usr/bin/env python3
"""Test Dock3 Cloud API payload-control camera_photo_stop.

This script intentionally uses only the official stop-photo services method:

    thing/product/{sn}/services -> thing/product/{sn}/services_reply
    method: camera_photo_stop

Request data is limited to the official payload_index field.
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
from tool_config import (
    add_config_argument,
    load_config,
    resolve_mqtt_credentials,
    resolve_mqtt_settings,
)


OFFICIAL_METHOD = "camera_photo_stop"


def _payload_index(args: argparse.Namespace) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def build_photo_stop_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {"payload_index": _payload_index(args)}


def validate_photo_stop_reply(reply: dict[str, Any] | None) -> int:
    if not isinstance(reply, dict) or reply.get("method") != OFFICIAL_METHOD:
        print(
            f"reply method mismatch: expected={OFFICIAL_METHOD}, "
            f"actual={reply.get('method') if isinstance(reply, dict) else None}",
            file=sys.stderr,
        )
        return 5

    code = result_code(reply)
    if code != 0:
        print(f"{OFFICIAL_METHOD} result is not 0: {json.dumps(reply, ensure_ascii=False)}", file=sys.stderr)
        return 4

    data = reply.get("data")
    data = data if isinstance(data, dict) else {}
    if data.get("running") is True or data.get("stopped") is False:
        print(
            f"{OFFICIAL_METHOD} returned success while photo session is still running: "
            + json.dumps(reply, ensure_ascii=False),
            file=sys.stderr,
        )
        return 6

    return 0


def run_photo_stop_check(client: MqttRequestClient, args: argparse.Namespace) -> int:
    payload = build_photo_stop_payload(args)
    rc, reply = client.request(
        OFFICIAL_METHOD,
        payload,
        args.timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc

    rc = validate_photo_stop_reply(reply)
    if rc != 0:
        return rc

    print(f"{OFFICIAL_METHOD} reply:")
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    print(f"dock3 payload photo-stop test ok (payload_index={payload['payload_index']})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test payload-control stop-photo through Dock3 Cloud API services."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--payload-index", default="", help="Dock3 payload index, e.g. 52-0-0.")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.username, args.password = resolve_mqtt_credentials(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_payload_photo_stop",
        username=args.username,
        password=args.password,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_photo_stop_check(client, args)
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
