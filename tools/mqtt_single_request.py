#!/usr/bin/env python3
"""Send one MQTT Cloud API request and print the reply.

This tiny wrapper exists for Windows/manual acceptance testing.  The shared
``mqtt_test_client.py`` module is a library, not a CLI entrypoint.
"""

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one MQTT request and print the reply")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--method", required=True)
    parser.add_argument("--data", default="{}")
    parser.add_argument("--channel", choices=["auto", "services", "property", "drc"], default="auto")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as exc:
        print(f"invalid --data JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("--data must be a JSON object", file=sys.stderr)
        return 2

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_single_request",
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    try:
        rc, reply = client.request(
            args.method,
            data,
            args.timeout,
            channel=args.channel,
            allowed_results=None,
            verbose=True,
        )
        if reply is not None:
            print("reply json compact:")
            print(json.dumps(reply, ensure_ascii=False))
        return rc
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
