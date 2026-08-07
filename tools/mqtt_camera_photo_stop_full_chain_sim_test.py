#!/usr/bin/env python3
"""Real-gateway MQTT regression check for camera_photo_stop.

The script publishes only the official Dock3 services method to the running
gateway process:

    thing/product/{sn}/services
    method: camera_photo_stop

It does not start a simulated gateway and does not attach a simulated photo
thread. Run it while the real gateway/device is in the suspected continuous
photo stop-timeout state.
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
DOCK_SYSTEM_ERROR = 319999


def payload_index(args: argparse.Namespace) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def validate_real_gateway_reply(reply: dict[str, Any] | None, args: argparse.Namespace) -> int:
    code = result_code(reply)
    print(f"real_gateway_result={code}")

    if code == 0:
        print(
            "failed: real gateway returned success for camera_photo_stop. "
            "If the device was still taking photos or the stop thread was stuck, "
            "this is the cloud-side false-success regression.",
            file=sys.stderr,
        )
        if not args.allow_success:
            return 6
        print("allow_success=true, keeping this run observational only")
        return 0

    if code == DOCK_SYSTEM_ERROR:
        print(
            "passed: real gateway returned 319999 instead of success. "
            "The cloud will not misjudge camera_photo_stop as stopped for this run."
        )
        return 0

    print(
        "inconclusive: real gateway did not return success, so there was no false-success reply, "
        f"but result={code} is not the expected 2-second stop-timeout result {DOCK_SYSTEM_ERROR}."
    )
    if args.accept_any_failure:
        return 0
    print("Use --accept-any-failure if any non-zero result is enough for this check.", file=sys.stderr)
    return 5


def run_real_gateway_check(args: argparse.Namespace) -> int:
    print(f"using real gateway SN: {args.sn}")
    print(f"mqtt broker: {args.host}:{args.port}, qos={args.qos}")
    print("mode: real gateway only, no simulated gateway")

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_photo_stop_real",
        username=args.username,
        password=args.password,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 3

    try:
        rc, reply = client.request(
            OFFICIAL_METHOD,
            {"payload_index": payload_index(args)},
            args.timeout,
            channel="services",
            allowed_results=None,
        )
        if rc != 0:
            return rc

        print("accepted real services_reply:")
        print(json.dumps(reply, ensure_ascii=False, indent=2))
        return validate_real_gateway_reply(reply, args)
    finally:
        client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Send camera_photo_stop to the running real gateway and fail if the "
            "official services_reply still returns result=0 during the stuck-photo condition."
        )
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
    parser.add_argument("--payload-index", default="", help="Dock3 payload index, e.g. 52-0-0.")
    parser.add_argument(
        "--allow-success",
        action="store_true",
        help="Do not fail when the real gateway returns result=0; print the observation only.",
    )
    parser.add_argument(
        "--accept-any-failure",
        action="store_true",
        help="Treat any non-zero result as pass instead of requiring result=319999.",
    )
    args = parser.parse_args()

    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.username, args.password = resolve_mqtt_credentials(args)
    return run_real_gateway_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
