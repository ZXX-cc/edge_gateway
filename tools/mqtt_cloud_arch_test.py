#!/usr/bin/env python3
"""Personnel-1 cloud protocol architecture validation.

This script focuses on MQTT/API boundary correctness rather than device-side
hardware behavior.  It uses exact tid/method/topic reply matching so stale
broker messages cannot satisfy the test.
"""

import argparse
import sys

from mqtt_test_client import MqttRequestClient, property_result
from tool_config import add_config_argument, resolve_mqtt_settings


def require_data(reply: dict, key: str, expected):
    actual = reply.get("data", {}).get(key)
    if actual != expected:
        print(f"expected data.{key}={expected!r}, actual={actual!r}", file=sys.stderr)
        return False
    return True


def require_property_result(reply: dict, name: str, expected: int) -> bool:
    actual = property_result(reply, name)
    if actual != expected:
        print(f"expected property {name} state.result={expected}, actual={actual}", file=sys.stderr)
        return False
    return True


def run_arch_checks(client: MqttRequestClient, args) -> int:
    rc, _ = client.request("dock_status_query", {}, args.timeout, allowed_results={0})
    if rc != 0:
        return rc

    rc, reply = client.request("property_set", {"silent_mode": 1}, args.timeout, channel="property")
    if rc != 0:
        return rc
    if not require_property_result(reply, "silent_mode", 0):
        return 5

    rc, reply = client.request("property_set", {"battery_store_mode": 2}, args.timeout, channel="property")
    if rc != 0:
        return rc
    if not require_property_result(reply, "battery_store_mode", 1):
        return 6

    rc, reply = client.request(
        "property_get",
        {"properties": ["silent_mode", "battery_store_mode", "drc_state", "mode_code"]},
        args.timeout,
        allowed_results={0},
    )
    if rc != 0:
        return rc
    if not require_data(reply, "silent_mode", 1):
        return 7

    if not args.skip_flight_mode:
        for payload, expected_mode, expected_code in (
            ({"mode": "GUIDED"}, "GUIDED", 4),
            ({"mode_code": 9}, "LAND", 9),
        ):
            rc, reply = client.request("set_flight_mode", payload, args.timeout, allowed_results={0})
            if rc != 0:
                return rc
            if not require_data(reply, "mode", expected_mode):
                return 8
            if not require_data(reply, "mode_code", expected_code):
                return 9

    rc, _ = client.request("unknown_arch_probe", {}, args.timeout, allowed_results={314000})
    if rc != 0:
        return rc

    if not args.skip_drc:
        rc, _ = client.request("drc_mode_exit", {}, args.timeout, allowed_results={0, 319999})
        if rc != 0:
            return rc

        rc, _ = client.request("drc_mode_enter", {}, args.timeout, allowed_results={0})
        if rc != 0:
            return rc

        rc, reply = client.request("property_get", {"properties": ["drc_state"]}, args.timeout, allowed_results={0})
        if rc != 0:
            return rc
        if not require_data(reply, "drc_state", 2):
            return 10

        rc, _ = client.request("drc_mode_exit", {}, args.timeout, allowed_results={0})
        if rc != 0:
            return rc

        rc, reply = client.request("property_get", {"properties": ["drc_state"]}, args.timeout, allowed_results={0})
        if rc != 0:
            return rc
        if not require_data(reply, "drc_state", 0):
            return 11

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate personnel-1 MQTT/API architecture boundaries")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--skip-flight-mode", action="store_true", help="Skip set_flight_mode checks when SITL is not attached.")
    parser.add_argument("--skip-drc", action="store_true", help="Skip DRC enter/exit state checks.")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_cloud_arch",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_arch_checks(client, args)
        if rc != 0:
            return rc

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 12

        print(
            "mqtt cloud architecture test ok "
            f"(accepted={client.stats['accepted']}, "
            f"ignored_total={stale_total}, stats={client.stats})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
