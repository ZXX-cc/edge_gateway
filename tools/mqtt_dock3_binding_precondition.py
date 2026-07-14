#!/usr/bin/env python3
"""Ensure the running gateway is bound before full-chain business steps."""

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
    parser = argparse.ArgumentParser(description="Bind gateway as a full-chain precondition")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--workspace-id", default="full_chain_workspace")
    parser.add_argument("--workspace-name", default="全链路验收工作空间")
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
            "device_binding",
            {
                "workspace_id": args.workspace_id,
                "workspace_name": args.workspace_name,
            },
            args.timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc

        rc, workspace_reply = client.request(
            "workspace_get",
            {},
            args.timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc

        data = workspace_reply.get("data", {}) if isinstance(workspace_reply, dict) else {}
        if data.get("workspace_id") != args.workspace_id:
            print(
                f"workspace_id mismatch: expected={args.workspace_id}, actual={data.get('workspace_id')}",
                file=sys.stderr,
            )
            return 5
        if not is_truthy_bound(data.get("is_bound")):
            print(f"workspace_get reports unbound: {data}", file=sys.stderr)
            return 6

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 10

        print(
            "dock3 binding precondition ok "
            f"(workspace_id={data.get('workspace_id')}, result={result_code(reply)}, "
            f"accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
