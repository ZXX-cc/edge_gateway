#!/usr/bin/env python3
"""Publish one DJI Cloud API-style MQTT message and wait for an exact reply."""

import argparse
import json
import sys

from mqtt_test_client import CHANNELS, MqttRequestClient, property_result, result_code
from tool_config import add_config_argument, resolve_mqtt_settings


def parse_payload(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"payload must be JSON: {exc}") from exc


def parse_property_expectation(raw: str) -> tuple[str, int]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("property expectation must be NAME=RESULT")
    name, value = raw.split("=", 1)
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError("property name must not be empty")
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("property result must be integer") from exc
    return name, result


def main():
    parser = argparse.ArgumentParser(description="MQTT smoke test for edge_gateway")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--channel", choices=["auto", *sorted(CHANNELS)], default="auto")
    parser.add_argument("--method", default="dock_status_query")
    parser.add_argument("--payload", type=parse_payload, default={})
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--expect-result", type=int, action="append", default=[])
    parser.add_argument(
        "--expect-property-result",
        type=parse_property_expectation,
        action="append",
        default=[],
        help="Require property/set_reply data.NAME.state.result. Repeat as NAME=RESULT.",
    )
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    allowed_results = set(args.expect_result) if args.expect_result else None
    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_smoke",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc, reply = client.request(
            args.method,
            args.payload,
            args.timeout,
            channel=args.channel,
            allowed_results=allowed_results,
        )
        if rc != 0:
            return rc

        for name, expected in args.expect_property_result:
            actual = property_result(reply, name)
            if actual != expected:
                print(f"property {name} unexpected result: expected={expected}, actual={actual}", file=sys.stderr)
                return 5

        if args.expect_result:
            print(f"result expectation ok: result={result_code(reply)}")
        if args.expect_property_result:
            print("property result expectations ok")

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 10
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
