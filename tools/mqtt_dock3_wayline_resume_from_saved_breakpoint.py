#!/usr/bin/env python3
"""After SITL restart, re-execute the saved task and verify it advances from the breakpoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_dock3_real_wayline_breakpoint_test import (  # noqa: E402
    assert_prepare_file,
    current_waypoint,
    output_ext,
    prepare_payload,
    progress_percent,
)
from mqtt_dock3_wayline_make_breakpoint import DEFAULT_STATE_FILE  # noqa: E402
from mqtt_dock3_wayline_relaunch_breakpoint_test import (  # noqa: E402
    RelaunchBreakpointClient,
    assert_relaunch_index,
    wait_until_waypoint_reaches,
)
from mqtt_test_client import result_code  # noqa: E402
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


def read_state(path: str) -> dict[str, Any]:
    state_path = Path(path).expanduser()
    if not state_path.exists():
        raise SystemExit(f"state file not found: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise SystemExit(f"state file is not an object: {state_path}")
    return state


def run_flow(client: RelaunchBreakpointClient, args: argparse.Namespace) -> int:
    state = read_state(args.state_file)
    flight_id = args.flight_id or str(state.get("flight_id") or "")
    wayline_file = args.wayline_file or str(state.get("wayline_file") or "")
    breakpoint_index = state.get("breakpoint_index")
    if not flight_id:
        print("state file does not contain flight_id", file=sys.stderr)
        return 2
    if not wayline_file:
        print("state file does not contain wayline_file", file=sys.stderr)
        return 2
    if not isinstance(breakpoint_index, int):
        print(f"state file does not contain integer breakpoint_index: {breakpoint_index}", file=sys.stderr)
        return 2

    args.wayline_file = wayline_file
    print(
        "resume saved breakpoint after SITL restart: "
        f"flight_id={flight_id}, breakpoint={breakpoint_index}, file={wayline_file}"
    )

    if not args.skip_reprepare:
        rc, reprepare = client.service(
            "flighttask_prepare",
            prepare_payload(flight_id, wayline_file, args),
            args.timeout,
            allowed_results={0},
            expected_status="sent",
        )
        if rc != 0:
            return rc
        rc = assert_prepare_file(reprepare, wayline_file, args)
        if rc != 0:
            return rc
        print(
            "reprepare accepted after SITL restart: "
            f"percent={progress_percent(reprepare)}, current_waypoint={current_waypoint(reprepare)}"
        )

    rc, execute = client.service(
        "flighttask_execute",
        {"flight_id": flight_id},
        args.timeout,
        allowed_results={0},
        expected_status="in_progress",
    )
    if rc != 0:
        return rc
    ext = output_ext(execute)
    if ext.get("flight_id") not in ("", None, flight_id):
        print(f"execute flight_id mismatch: {ext.get('flight_id')} != {flight_id}", file=sys.stderr)
        return 35
    rc = assert_relaunch_index(execute, breakpoint_index, True)
    if rc != 0:
        return rc
    print(
        "execute accepted from saved breakpoint: "
        f"breakpoint={breakpoint_index}, current_waypoint={current_waypoint(execute)}, "
        f"percent={progress_percent(execute)}"
    )

    target_index = breakpoint_index + args.min_waypoint_advance
    rc, progress = wait_until_waypoint_reaches(
        client,
        flight_id,
        target_index,
        args,
        "saved-breakpoint re-execute",
    )
    if rc != 0:
        return rc
    print(
        "saved breakpoint re-execute advanced: "
        f"breakpoint={breakpoint_index}, current_waypoint={current_waypoint(progress)}, "
        f"percent={progress_percent(progress)}"
    )

    if args.cleanup:
        method = "flighttask_stop" if args.use_flighttask_stop else "flighttask_undo"
        payload = (
            {"flight_id": flight_id, "reason": 0}
            if method == "flighttask_stop"
            else {"flight_ids": [flight_id]}
        )
        rc, cleanup = client.service(
            method,
            payload,
            args.timeout,
            allowed_results={0, DEVICE_STATE_UNSUPPORTED},
            expected_status=None,
        )
        if rc != 0:
            return rc
        print(f"cleanup result: method={method}, result={result_code(cleanup)}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-execute a saved wayline task from its persisted breakpoint")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--wayline-file", default="")
    parser.add_argument("--file-fingerprint", default="")
    parser.add_argument("--expect-waypoint-count", type=int, default=10)
    parser.add_argument("--execute-delay-seconds", type=float, default=0.0)
    parser.add_argument("--rth-altitude", type=float, default=100.0)
    parser.add_argument("--out-of-control-action", type=int, default=0)
    parser.add_argument("--exit-wayline-when-rc-lost", type=int, default=0)
    parser.add_argument("--progress-poll-interval", type=float, default=1.0)
    parser.add_argument("--min-waypoint-advance", type=int, default=1)
    parser.add_argument("--require-gateway-parse", action="store_true")
    parser.add_argument("--skip-reprepare", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--use-flighttask-stop", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.progress_poll_interval <= 0:
        parser.error("--progress-poll-interval must be positive")
    if args.min_waypoint_advance <= 0:
        parser.error("--min-waypoint-advance must be positive")

    client = RelaunchBreakpointClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_resume_saved_wayline_breakpoint",
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
