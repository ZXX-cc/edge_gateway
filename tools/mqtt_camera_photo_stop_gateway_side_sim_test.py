#!/usr/bin/env python3
"""Gateway-side simulated photo-session regression test for camera_photo_stop.

This tool talks to a real running gateway over MQTT. The gateway must be
started with EDGE_GATEWAY_ENABLE_TEST_HOOKS=1 so it registers the debug methods
used to create and clear a simulated photo session inside the gateway process.
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


DEBUG_START_METHOD = "debug_photo_session_start"
DEBUG_CLEAR_METHOD = "debug_photo_session_clear"
OFFICIAL_STOP_METHOD = "camera_photo_stop"
DOCK_SYSTEM_ERROR = 319999


def payload_index(args: argparse.Namespace) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def _print_reply(label: str, reply: dict[str, Any] | None) -> None:
    print(f"{label}:")
    print(json.dumps(reply, ensure_ascii=False, indent=2))


def run_gateway_side_sim_check(args: argparse.Namespace) -> int:
    print(f"using real gateway SN: {args.sn}")
    print(f"mqtt broker: {args.host}:{args.port}, qos={args.qos}")
    print("mode: real gateway MQTT + gateway-side simulated stuck photo thread")
    print("gateway prerequisite: EDGE_GATEWAY_ENABLE_TEST_HOOKS=1")

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_photo_stop_gateway_side_sim",
        username=args.username,
        password=args.password,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 3

    started = False
    try:
        rc, setup_reply = client.request(
            DEBUG_START_METHOD,
            {
                "stuck": True,
                "interval": args.sim_interval,
                "payload_index": payload_index(args),
            },
            args.timeout,
            channel="services",
            allowed_results=None,
        )
        _print_reply("debug setup reply", setup_reply)
        if rc != 0:
            return rc
        if result_code(setup_reply) != 0:
            print(
                "failed: gateway did not accept debug_photo_session_start. "
                "Start the gateway with EDGE_GATEWAY_ENABLE_TEST_HOOKS=1.",
                file=sys.stderr,
            )
            return 7
        started = True

        rc, stop_reply = client.request(
            OFFICIAL_STOP_METHOD,
            {"payload_index": payload_index(args)},
            args.timeout,
            channel="services",
            allowed_results=None,
        )
        _print_reply("official camera_photo_stop reply", stop_reply)
        if rc != 0:
            return rc

        code = result_code(stop_reply)
        print(f"real_gateway_result={code}")
        if code == DOCK_SYSTEM_ERROR:
            print("passed: stuck gateway-side photo session returned 319999, not success.")
            return 0
        if code == 0:
            print(
                "failed: camera_photo_stop returned success while the gateway-side "
                "simulated photo thread was still stuck.",
                file=sys.stderr,
            )
            return 6

        print(
            "inconclusive: camera_photo_stop did not return success, but result "
            f"{code} was not the expected {DOCK_SYSTEM_ERROR}."
        )
        if args.accept_any_failure:
            return 0
        print("Use --accept-any-failure if any non-zero result is enough.", file=sys.stderr)
        return 5
    finally:
        if started:
            rc, clear_reply = client.request(
                DEBUG_CLEAR_METHOD,
                {"payload_index": payload_index(args)},
                args.timeout,
                channel="services",
                allowed_results=None,
            )
            _print_reply("debug cleanup reply", clear_reply)
            if rc != 0:
                print(f"warning: cleanup request rc={rc}", file=sys.stderr)
        client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a stuck simulated photo session inside the running gateway, "
            "then verify official camera_photo_stop does not return success."
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
    parser.add_argument("--sim-interval", type=float, default=0.2)
    parser.add_argument(
        "--accept-any-failure",
        action="store_true",
        help="Treat any non-zero stop result as pass instead of requiring 319999.",
    )
    args = parser.parse_args()

    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.username, args.password = resolve_mqtt_credentials(args)
    return run_gateway_side_sim_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
