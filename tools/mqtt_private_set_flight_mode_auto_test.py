#!/usr/bin/env python3
"""Private ops test: switch the aircraft flight mode to AUTO through MQTT.

This tool intentionally uses the gateway's private ``set_flight_mode`` service
entrypoint.  It does not open a MAVLink/TCP connection to the aircraft; the
running gateway must own that single flight-controller link and will call
``FlightControl.set_mode("AUTO")`` internally.
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

from mqtt_test_client import MqttRequestClient, result_code  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


PRIVATE_METHOD = "set_flight_mode"
TARGET_MODE = "AUTO"
TARGET_MODE_CODE = 4


def build_auto_payload(timeout: float) -> dict[str, Any]:
    return {
        "flight_mode": TARGET_MODE,
        "timeout": float(timeout),
    }


def _reply_data(reply: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {}
    data = reply.get("data")
    return data if isinstance(data, dict) else {}


def validate_auto_reply(reply: dict[str, Any] | None) -> tuple[bool, str]:
    actual_result = result_code(reply)
    if actual_result != 0:
        return False, f"expected result=0, actual={actual_result}"

    data = _reply_data(reply)
    mode = str(data.get("mode") or data.get("target_mode_name") or "").upper()
    if mode != TARGET_MODE:
        return False, f"expected data.mode={TARGET_MODE}, actual={mode or None!r}"

    try:
        mode_code = int(data.get("mode_code"))
    except (TypeError, ValueError):
        return False, f"expected data.mode_code={TARGET_MODE_CODE}, actual={data.get('mode_code')!r}"
    if mode_code != TARGET_MODE_CODE:
        return False, f"expected data.mode_code={TARGET_MODE_CODE}, actual={mode_code}"

    return True, "AUTO mode switch accepted and confirmed by gateway"


def run_auto_mode_test(client: MqttRequestClient, args: argparse.Namespace) -> int:
    if not args.skip_gateway_state:
        rc, _ = client.request(
            "gateway_state_query",
            {"compact": True},
            args.timeout,
            channel="services",
            allowed_results={0},
            verbose=args.verbose,
        )
        if rc != 0:
            return rc

    payload = build_auto_payload(args.mode_timeout)
    rc, reply = client.request(
        PRIVATE_METHOD,
        payload,
        args.timeout,
        channel="services",
        allowed_results={0},
        verbose=args.verbose,
    )
    if rc != 0:
        return rc

    ok, detail = validate_auto_reply(reply)
    if getattr(args, "print_summary", True):
        print(f"[{'PASS' if ok else 'FAIL'}] private set_flight_mode AUTO")
        print(json.dumps({"detail": detail, "payload": payload, "reply": reply}, ensure_ascii=False, indent=2))
    return 0 if ok else 5


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Private MQTT ops test for switching the aircraft flight mode to AUTO. "
            "This script only connects to MQTT; it never connects to the aircraft TCP/MAVLink link."
        )
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=15.0, help="MQTT connect/reply timeout in seconds.")
    parser.add_argument(
        "--mode-timeout",
        type=float,
        default=10.0,
        help="Timeout passed to gateway FlightControl.set_mode('AUTO').",
    )
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--skip-gateway-state", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_private_auto_mode",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_auto_mode_test(client, args)
        if rc != 0:
            return rc
        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 6
        print(f"private AUTO mode test ok (accepted={client.stats['accepted']}, ignored_total={stale_total})")
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
