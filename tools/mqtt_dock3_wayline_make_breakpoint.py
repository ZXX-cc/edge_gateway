#!/usr/bin/env python3
"""Create a persisted wayline breakpoint, then stop for manual SITL restart."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_dock3_real_wayline_breakpoint_test import (  # noqa: E402
    DEFAULT_REAL_WAYLINE_FILE,
    assert_prepare_file,
    break_point_from,
    current_waypoint,
    prepare_payload,
    progress_percent,
)
from mqtt_dock3_wayline_relaunch_breakpoint_test import (  # noqa: E402
    RelaunchBreakpointClient,
    wait_until_paused_with_breakpoint,
    wait_until_waypoint_reaches,
)
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


DEFAULT_STATE_FILE = "/tmp/edge_gateway_wayline_breakpoint_state.json"


def write_state(path: str, state: dict[str, Any]) -> None:
    state_path = Path(path).expanduser()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"breakpoint state saved -> {state_path}")


def run_flow(client: RelaunchBreakpointClient, args: argparse.Namespace) -> int:
    flight_id = args.flight_id or f"real-hd-breakpoint-state-{uuid.uuid4().hex[:8]}"
    print(
        "create breakpoint task: "
        f"flight_id={flight_id}, target_waypoint={args.target_waypoint_index}, file={args.wayline_file}"
    )

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
        "execute accepted: "
        f"percent={progress_percent(execute)}, current_waypoint={current_waypoint(execute)}"
    )

    rc, reached = wait_until_waypoint_reaches(
        client,
        flight_id,
        args.target_waypoint_index,
        args,
        "initial task",
    )
    if rc != 0:
        return rc
    print(
        "target waypoint reached, requesting return: "
        f"current_waypoint={current_waypoint(reached)}, percent={progress_percent(reached)}"
    )

    rc, returned = client.service(
        "flighttask_return",
        {"flight_id": flight_id, "return_type": args.return_type},
        args.timeout,
        allowed_results={0},
        expected_status="in_progress",
    )
    if rc != 0:
        return rc
    print(
        "return accepted: "
        f"percent={progress_percent(returned)}, current_waypoint={current_waypoint(returned)}"
    )

    args.min_breakpoint_index = args.target_waypoint_index
    rc, paused = wait_until_paused_with_breakpoint(client, flight_id, args)
    if rc != 0:
        return rc
    breakpoint = break_point_from(paused)
    state = {
        "flight_id": flight_id,
        "wayline_file": str(Path(args.wayline_file).expanduser()),
        "breakpoint_index": breakpoint.get("index"),
        "break_point": breakpoint,
        "created_at": int(time.time() * 1000),
    }
    write_state(args.state_file, state)
    print("SITL can be restarted now. Do not run cleanup before the resume script.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute a wayline to a target waypoint and save a breakpoint")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--wayline-file", default=DEFAULT_REAL_WAYLINE_FILE)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--file-fingerprint", default="")
    parser.add_argument("--expect-waypoint-count", type=int, default=10)
    parser.add_argument("--execute-delay-seconds", type=float, default=0.0)
    parser.add_argument("--rth-altitude", type=float, default=100.0)
    parser.add_argument("--out-of-control-action", type=int, default=0)
    parser.add_argument("--exit-wayline-when-rc-lost", type=int, default=0)
    parser.add_argument("--return-type", type=int, choices=[0, 1], default=0)
    parser.add_argument("--target-waypoint-index", type=int, default=3)
    parser.add_argument("--progress-poll-interval", type=float, default=1.0)
    parser.add_argument("--require-gateway-parse", action="store_true")
    parser.add_argument("--expect-breakpoint-index", type=int, default=None)
    parser.add_argument("--quiet-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.min_breakpoint_index = args.target_waypoint_index

    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.target_waypoint_index < 1:
        parser.error("--target-waypoint-index must be >= 1")
    if args.progress_poll_interval <= 0:
        parser.error("--progress-poll-interval must be positive")

    client = RelaunchBreakpointClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_make_wayline_breakpoint",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1
    try:
        return run_flow(client, args)
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
