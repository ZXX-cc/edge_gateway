#!/usr/bin/env python3
"""Simulate platform interruption of the active Dock3 wayline task."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_dock3_real_wayline_breakpoint_test import break_point_from, current_waypoint, progress_percent  # noqa: E402
from mqtt_dock3_wayline_relaunch_breakpoint_test import RelaunchBreakpointClient  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


def run_flow(client: RelaunchBreakpointClient, args: argparse.Namespace) -> int:
    if not args.flight_id:
        print("--flight-id is required", file=sys.stderr)
        return 2

    previous_progress_events = client.event_count("flighttask_progress")
    rc, reply = client.service(
        "flighttask_return",
        {"flight_id": args.flight_id, "return_type": args.return_type},
        args.timeout,
        allowed_results={0},
        expected_status=None,
    )
    if rc != 0:
        return rc
    print(
        "platform interrupt accepted: "
        f"flight_id={args.flight_id}, current_waypoint={current_waypoint(reply)}, "
        f"percent={progress_percent(reply)}"
    )

    rc, event = client.wait_event(
        "flighttask_progress",
        previous_progress_events,
        args.event_timeout,
        expected_status="paused",
    )
    if rc != 0:
        return rc
    print("platform visible interrupted task:")
    print(json.dumps(event, ensure_ascii=False, indent=2))
    breakpoint = break_point_from(event)
    if not breakpoint:
        print("paused event did not include break_point", file=sys.stderr)
        return 3
    print(
        "platform interruption evidence ok: "
        f"flight_id={args.flight_id}, current_waypoint={current_waypoint(event)}, "
        f"percent={progress_percent(event)}, break_point={breakpoint}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Platform-only wayline interruption script")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--event-timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", required=True)
    parser.add_argument("--return-type", type=int, choices=[0, 1], default=0)
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
        client_id_prefix="edge_gateway_platform_wayline_interrupt",
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
            "platform wayline interrupt script ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
