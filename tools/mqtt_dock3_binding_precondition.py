#!/usr/bin/env python3
"""Ensure the running gateway is already bound before full-chain business steps."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code
from tool_config import add_config_argument, resolve_mqtt_settings


def is_truthy_bound(value) -> bool:
    return value is True or value == 1 or value == "1" or str(value).lower() == "true"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check gateway binding as a full-chain precondition")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
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
        subscribe_events=True,
        client_id_prefix="edge_gateway_dock3_binding_precondition",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc, reply = client.request(
            "gateway_state_query",
            {"compact": True},
            args.timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc

        data = reply.get("data", {}) if isinstance(reply, dict) else {}
        state = data.get("state", {}) if isinstance(data, dict) else {}
        device_state = state.get("device") if isinstance(state, dict) else ""
        if not is_truthy_bound(device_state == "bound"):
            print(
                "gateway is not bound by the official organization flow; "
                "start the gateway with airport_bind_status/airport_organization_get/"
                f"airport_organization_bind configured first. state={state}",
                file=sys.stderr,
            )
            return 6

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 10

        print(
            "dock3 binding state precondition ok "
            f"(device={device_state}, result={result_code(reply)}, "
            f"accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
