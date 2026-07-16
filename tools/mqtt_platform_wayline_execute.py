#!/usr/bin/env python3
"""Simulate platform commands to prepare and execute a real Dock3 wayline."""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_dock3_real_wayline_breakpoint_test import (  # noqa: E402
    DEFAULT_REAL_WAYLINE_FILE,
    assert_prepare_file,
    current_waypoint,
    prepare_payload,
    progress_percent,
)
from mqtt_dock3_wayline_relaunch_breakpoint_test import RelaunchBreakpointClient  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


def run_flow(client: RelaunchBreakpointClient, args: argparse.Namespace) -> int:
    flight_id = args.flight_id or f"platform-wayline-{uuid.uuid4().hex[:8]}"
    print(f"platform execute wayline: flight_id={flight_id}, file={args.wayline_file}")

    rc, prepare = client.service(
        "flighttask_prepare",
        prepare_payload(flight_id, args.wayline_file, args),
        args.timeout,
        allowed_results={0},
        expected_status="sent",
    )
    if rc != 0:
        return rc
    rc = assert_prepare_file(prepare, args.wayline_file, args)
    if rc != 0:
        return rc

    rc, execute = client.service(
        "flighttask_execute",
        {"flight_id": flight_id},
        args.timeout,
        allowed_results={0},
        expected_status="in_progress",
    )
    if rc != 0:
        return rc

    print(
        "platform wayline execute accepted: "
        f"flight_id={flight_id}, current_waypoint={current_waypoint(execute)}, "
        f"percent={progress_percent(execute)}"
    )
    print(f"next interrupt command should use: --flight-id {flight_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Platform-only wayline prepare/execute script")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--wayline-file", default=DEFAULT_REAL_WAYLINE_FILE)
    parser.add_argument("--file-fingerprint", default="")
    parser.add_argument("--expect-waypoint-count", type=int, default=10)
    parser.add_argument("--execute-delay-seconds", type=float, default=0.0)
    parser.add_argument("--rth-altitude", type=float, default=100.0)
    parser.add_argument("--out-of-control-action", type=int, default=0)
    parser.add_argument("--exit-wayline-when-rc-lost", type=int, default=0)
    parser.add_argument("--require-gateway-parse", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = RelaunchBreakpointClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_platform_wayline_execute",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1
    try:
        rc = run_flow(client, args)
        if rc != 0:
            return rc
        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"strict stale replies observed: stats={client.stats}", file=sys.stderr)
            return 41
        print(
            "platform wayline execute script ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
