#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 properties."""

import argparse
import sys

from mqtt_test_client import MqttRequestClient, property_result
from tool_config import add_config_argument, resolve_mqtt_settings

WRITABLE_CASES = [
    ("silent_mode", 1),
    ("air_transfer_enable", True),
    ("user_experience_improvement", 2),
]

REJECT_CASES = [
    ("battery_store_mode", 2),
    ("alarm_state", 1),
    ("drc_state", 2),
    ("silent_mode", 9),
    ("air_transfer_enable", 1),
    ("unknown_property", 1),
]

READBACK_PROPERTIES = [
    "silent_mode",
    "air_transfer_enable",
    "user_experience_improvement",
    "battery_store_mode",
    "alarm_state",
    "drc_state",
    "cover_state",
    "drone_in_dock",
    "mode_code",
]


def assert_property_result(reply, property_name, expected_results):
    actual = property_result(reply, property_name)
    if actual not in set(expected_results):
        print(
            f"property_set {property_name} unexpected result: "
            f"expected={sorted(set(expected_results))}, actual={actual}",
            file=sys.stderr,
        )
        return False
    return True


def run_property_set_checks(client, args):
    for name, value in WRITABLE_CASES:
        rc, reply = client.request("property_set", {name: value}, args.timeout, channel="property")
        if rc != 0:
            return rc
        if not assert_property_result(reply, name, {0}):
            return 5

    for name, value in REJECT_CASES:
        rc, reply = client.request("property_set", {name: value}, args.timeout, channel="property")
        if rc != 0:
            return rc
        if not assert_property_result(reply, name, {1}):
            return 6

    return 0


def run_property_get_checks(client, args):
    rc, reply = client.request(
        "property_get",
        {"properties": READBACK_PROPERTIES},
        args.timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc

    data = reply.get("data", {})
    expected_values = {
        "silent_mode": 1,
        "air_transfer_enable": True,
        "user_experience_improvement": 2,
    }
    for name, expected in expected_values.items():
        actual = data.get(name)
        if actual != expected:
            print(f"property_get {name} mismatch: expected={expected!r}, actual={actual!r}", file=sys.stderr)
            return 7

    missing = [name for name in READBACK_PROPERTIES if name not in data]
    if missing:
        print(f"property_get missing properties: {missing}", file=sys.stderr)
        return 8

    return 0


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 properties category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true", help="Only count stale duplicate replies instead of printing their JSON payloads.")
    parser.add_argument("--fail-on-stale", action="store_true", help="Fail if duplicate legacy/stale replies are observed after the broker environment is cleaned.")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_properties",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_property_set_checks(client, args)
        if rc != 0:
            return rc

        rc = run_property_get_checks(client, args)
        if rc != 0:
            return rc

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
            "dock3 properties test ok "
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
