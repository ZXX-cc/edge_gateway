#!/usr/bin/env python3
"""Batch smoke test for Dock3 P0 MQTT compatibility."""

import argparse
import sys

from mqtt_test_client import MqttRequestClient, property_result
from tool_config import add_config_argument, resolve_mqtt_settings


def assert_property_reply(payload, property_name, expected_results):
    actual = property_result(payload, property_name)
    if actual not in set(expected_results):
        print(
            f"property_set {property_name} unexpected result: "
            f"expected={sorted(set(expected_results))}, actual={actual}",
            file=sys.stderr,
        )
        return False
    return True


def run_property_checks(client, args):
    checks = [
        ("silent_mode", 1, {0}),
        ("air_transfer_enable", True, {0}),
        ("user_experience_improvement", 2, {0}),
    ]
    for name, value, expected in checks:
        rc, reply = client.request("property_set", {name: value}, args.timeout, channel="property")
        if rc != 0:
            return rc
        if not assert_property_reply(reply, name, expected):
            return 5

    if not args.skip_readonly_property_check:
        readonly_checks = [
            ("battery_store_mode", 2),
            ("alarm_state", 1),
        ]
        for name, value in readonly_checks:
            rc, reply = client.request("property_set", {name: value}, args.timeout, channel="property")
            if rc != 0:
                return rc
            actual = property_result(reply, name)
            if actual in (None, 0):
                print(f"expected readonly property {name} to fail, actual={actual}", file=sys.stderr)
                return 6

    rc, reply = client.request(
        "property_get",
        {"properties": ["silent_mode", "drc_state", "air_transfer_enable", "user_experience_improvement"]},
        args.timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc
    if reply.get("data", {}).get("silent_mode") != 1:
        print(f"expected silent_mode=1, actual={reply.get('data', {}).get('silent_mode')}", file=sys.stderr)
        return 7
    return 0


def run_status_checks(client, args):
    rc, _ = client.request("dock_status_query", {}, args.timeout, channel="services", allowed_results={0})
    if rc != 0:
        return rc

    if args.include_legacy_bind_status:
        rc, _ = client.request("airport_bind_status", {}, args.timeout, channel="services", allowed_results={0})
        if rc != 0:
            print(
                "legacy services airport_bind_status failed. "
                "Dock3 official organization validation uses requests/requests_reply; "
                "rerun without --include-legacy-bind-status or use mqtt_cloud_simulator.py for official validation.",
                file=sys.stderr,
            )
            return rc
    return 0


def run_drc_checks(client, args):
    rc, _ = client.request("drc_mode_exit", {}, args.timeout, channel="services", allowed_results={0, 319999})
    if rc != 0:
        return rc

    rc, _ = client.request("drc_mode_enter", {}, args.timeout, channel="services", allowed_results={0})
    if rc != 0:
        return rc

    rc, state_reply = client.request(
        "property_get",
        {"properties": ["drc_state"]},
        args.timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc
    if state_reply.get("data", {}).get("drc_state") != 2:
        print(f"expected drc_state=2 after enter, actual={state_reply.get('data', {}).get('drc_state')}", file=sys.stderr)
        return 8

    if not args.skip_drc_control:
        control_cases = [
            ("stick_control", {"vx": 0.1, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.0}),
            ("drone_control", {
                "horizontal_speed": 0.1,
                "horizontal_direction": 0,
                "vertical_speed": 0.0,
                "vertical_direction": 0,
                "yaw": 0.0,
            }),
            ("payload_control", {"payload_index": "0", "action": "photo_take", "params": {}}),
            ("drc_speed_push", {"vx": 0.0, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.0}),
            ("drc_gimbal_speed_push", {"pitch_speed": 0.0, "yaw_speed": 0.0, "roll_speed": 0.0}),
        ]
        for method, payload in control_cases:
            rc, _ = client.request(method, payload, args.timeout, channel="drc", allowed_results={0})
            if rc != 0:
                return rc

    rc, _ = client.request("drc_mode_exit", {}, args.timeout, channel="services", allowed_results={0})
    if rc != 0:
        return rc

    rc, state_reply = client.request(
        "property_get",
        {"properties": ["drc_state"]},
        args.timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc
    if state_reply.get("data", {}).get("drc_state") != 0:
        print(f"expected drc_state=0 after exit, actual={state_reply.get('data', {}).get('drc_state')}", file=sys.stderr)
        return 9

    if not args.skip_wrong_topic_check:
        rc, _ = client.request("drc_mode_enter", {}, args.timeout, channel="drc", allowed_results={325001})
        if rc != 0:
            return rc
    return 0


def main():
    parser = argparse.ArgumentParser(description="Batch test Dock3 P0 MQTT APIs")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--skip-status", action="store_true")
    parser.add_argument("--skip-properties", action="store_true")
    parser.add_argument("--skip-drc", action="store_true")
    parser.add_argument("--skip-drc-control", action="store_true")
    parser.add_argument("--skip-wrong-topic-check", action="store_true")
    parser.add_argument("--skip-readonly-property-check", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true", help="Only count stale duplicate replies instead of printing their JSON payloads.")
    parser.add_argument("--fail-on-stale", action="store_true", help="Fail if duplicate legacy/stale replies are observed. Useful after the broker environment is cleaned.")
    parser.add_argument(
        "--include-legacy-bind-status",
        action="store_true",
        help="Also test the legacy services airport_bind_status compatibility path. Dock3 official path is requests/requests_reply.",
    )
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_p0",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        if not args.skip_status:
            rc = run_status_checks(client, args)
            if rc != 0:
                return rc

        if not args.skip_properties:
            rc = run_property_checks(client, args)
            if rc != 0:
                return rc

        if not args.skip_drc:
            rc = run_drc_checks(client, args)
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
            "dock3 p0 batch test ok "
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
