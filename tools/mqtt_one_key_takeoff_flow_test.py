#!/usr/bin/env python3
"""E2E smoke test for the one-key takeoff closed-loop service."""

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient
from tool_config import add_config_argument, resolve_mqtt_settings


EXPECTED_FULL_STEPS = [
    "precheck",
    "charge_close",
    "cover_open",
    "putter_open",
    "nest_takeoff_request",
    "flight_takeoff",
    "flighttask_execute",
    "return_home",
    "landing",
    "putter_close",
    "cover_close",
    "charge_open",
    "completed",
]

EXPECTED_START_STEPS = EXPECTED_FULL_STEPS[:7]


def output(payload):
    try:
        return payload["data"]["output"]
    except (KeyError, TypeError):
        return {}


def step_names(payload):
    steps = output(payload).get("steps", [])
    return [step.get("step") for step in steps if isinstance(step, dict)]


def run_takeoff_flow_check(client, args):
    flight_id = args.flight_id or f"one-key-takeoff-{int(time.time())}"
    request = {
        "flight_id": flight_id,
        "takeoff_height": args.takeoff_height,
    }
    if args.auto_complete:
        request["auto_complete"] = True
    if args.simulate:
        request["simulate_mission"] = {"is_enable": 1}

    expected_status = "ok" if args.auto_complete or args.simulate else "in_progress"
    expected_steps = EXPECTED_FULL_STEPS if expected_status == "ok" else EXPECTED_START_STEPS

    progress_before = client.event_count("one_key_takeoff_progress")
    flighttask_before = client.event_count("flighttask_progress")
    rc, payload = client.request(
        "one_key_takeoff_flow",
        request,
        args.timeout,
        channel="services",
        allowed_results={0},
        expected_status=expected_status,
    )
    if rc != 0:
        return rc

    actual_steps = step_names(payload)
    if actual_steps != expected_steps:
        print(
            "one_key_takeoff_flow steps mismatch: "
            f"expected={expected_steps}, actual={actual_steps}",
            file=sys.stderr,
        )
        return 10

    rc, _ = client.wait_event(
        "one_key_takeoff_progress",
        progress_before,
        args.event_timeout,
        expected_status=None,
    )
    if rc != 0:
        return rc
    rc, _ = client.wait_event(
        "flighttask_progress",
        flighttask_before,
        args.event_timeout,
        expected_status=expected_status,
    )
    if rc != 0:
        return rc

    return 0


def main():
    parser = argparse.ArgumentParser(description="Test one-key takeoff closed-loop service")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--event-timeout", type=float, default=3.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--takeoff-height", type=float, default=30.0)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--auto-complete", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_one_key_takeoff",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    try:
        rc = run_takeoff_flow_check(client, args)
        if rc != 0:
            return rc
        ignored_total = client.stale_total()
        if args.fail_on_stale and ignored_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 9
        print(
            "one-key takeoff flow test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_total={ignored_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
