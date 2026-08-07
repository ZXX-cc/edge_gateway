#!/usr/bin/env python3
"""Test Dock3 cloud API payload-control gimbal_reset.

This script intentionally uses only the existing Dock3 cloud API services
method routed by the gateway:

    gimbal_reset

It never calls direct ViewLink SDK helpers or locally invented methods.
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


def build_gimbal_reset_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {"payload_index": _payload_index(args)}


def validate_gimbal_reset_reply(reply: dict[str, Any] | None) -> int:
    code = result_code(reply)
    if code != 0:
        print(
            "gimbal_reset result is not 0: "
            + json.dumps(reply, ensure_ascii=False),
            file=sys.stderr,
        )
        return 4
    return 0


def run_gimbal_reset_check(client: MqttRequestClient, args: argparse.Namespace) -> int:
    payload = build_gimbal_reset_payload(args)
    rc, reply = client.request(
        "gimbal_reset",
        payload,
        args.timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc

    rc = validate_gimbal_reset_reply(reply)
    if rc != 0:
        return rc

    print("gimbal_reset reply:")
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    print(f"dock3 payload gimbal-reset test ok (payload_index={payload['payload_index']})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test payload-control gimbal reset through Dock3 cloud API services."
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
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_payload_gimbal_reset",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_gimbal_reset_check(client, args)
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
