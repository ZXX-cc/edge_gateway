#!/usr/bin/env python3
"""Batch smoke test for Dock3 DRC services and drc/down control data."""

import argparse
import json
import sys

from mqtt_test_client import MqttRequestClient
from tool_config import add_config_argument, resolve_mqtt_settings

DEFAULT_CONTROL_CASES = [
    ("stick_control", {
        "vx": 0.1,
        "vy": 0.0,
        "vz": 0.0,
        "yaw_rate": 0.0,
    }),
    ("drone_control", {
        "horizontal_speed": 0.1,
        "horizontal_direction": 0,
        "vertical_speed": 0.0,
        "vertical_direction": 0,
        "yaw": 0.0,
    }),
    ("payload_control", {
        "payload_index": "0",
        "action": "photo_take",
        "params": {},
    }),
    ("drc_speed_push", {
        "vx": 0.0,
        "vy": 0.0,
        "vz": 0.0,
        "yaw_rate": 0.0,
    }),
    ("drc_gimbal_speed_push", {
        "pitch_speed": 0.0,
        "yaw_speed": 0.0,
        "roll_speed": 0.0,
    }),
]

def parse_json(raw):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"must be JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return value


def parse_control_case(raw):
    value = parse_json(raw)
    method = value.get("method")
    if not method:
        raise argparse.ArgumentTypeError("control case requires method")
    payload = value.get("data", {})
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError("control case data must be an object")
    return method, payload


def main():
    parser = argparse.ArgumentParser(description="Batch test Dock3 DRC enter/control/exit flow")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--no-reset-first", action="store_true")
    parser.add_argument("--skip-state-check", action="store_true")
    parser.add_argument("--skip-exit", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true", help="Only count stale duplicate replies instead of printing their JSON payloads.")
    parser.add_argument("--fail-on-stale", action="store_true", help="Fail if duplicate legacy/stale replies are observed. Useful after the broker environment is cleaned.")
    parser.add_argument(
        "--control-case",
        action="append",
        type=parse_control_case,
        default=[],
        help='Extra or replacement DRC control case JSON: {"method":"stick_control","data":{...}}',
    )
    parser.add_argument(
        "--only-custom-cases",
        action="store_true",
        help="Run only --control-case cases, not the default control cases.",
    )
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_drc_batch",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        if not args.no_reset_first:
            rc, _ = client.request(
                "drc_mode_exit",
                {},
                args.timeout,
                channel="services",
                allowed_results={0, 319999},
            )
            if rc != 0:
                return rc

        rc, _ = client.request("drc_mode_enter", {}, args.timeout, channel="services")
        if rc != 0:
            return rc

        if not args.skip_state_check:
            rc, state_reply = client.request(
                "property_get",
                {"properties": ["drc_state"]},
                args.timeout,
                channel="services",
            )
            if rc != 0:
                return rc
            if state_reply.get("data", {}).get("drc_state") != 2:
                print(f"expected drc_state=2 after enter, actual={state_reply.get('data', {}).get('drc_state')}", file=sys.stderr)
                return 5

        control_cases = [] if args.only_custom_cases else list(DEFAULT_CONTROL_CASES)
        control_cases.extend(args.control_case)
        for method, payload in control_cases:
            rc, _ = client.request(method, payload, args.timeout, channel="drc")
            if rc != 0:
                return rc

        if not args.skip_exit:
            rc, _ = client.request("drc_mode_exit", {}, args.timeout, channel="services")
            if rc != 0:
                return rc
            if not args.skip_state_check:
                rc, state_reply = client.request(
                    "property_get",
                    {"properties": ["drc_state"]},
                    args.timeout,
                    channel="services",
                )
                if rc != 0:
                    return rc
                if state_reply.get("data", {}).get("drc_state") != 0:
                    print(f"expected drc_state=0 after exit, actual={state_reply.get('data', {}).get('drc_state')}", file=sys.stderr)
                    return 6

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(
                "stale duplicate replies observed "
                f"(ignored_mismatched={client.stats['ignored_mismatched']}, "
                f"ignored_legacy={client.stats['ignored_legacy']}, "
                f"ignored_late={client.stats['ignored_late']}, "
                f"ignored_wrong_topic={client.stats['ignored_wrong_topic']})",
                file=sys.stderr,
            )
            return 10

        print(
            "drc batch test ok "
            f"(accepted={client.stats['accepted']}, "
            f"ignored_mismatched={client.stats['ignored_mismatched']}, "
            f"ignored_legacy={client.stats['ignored_legacy']}, "
            f"ignored_late={client.stats['ignored_late']}, "
            f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, "
            f"ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
