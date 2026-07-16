#!/usr/bin/env python3
"""After gateway/SITL restart, re-execute the saved task from MongoDB breakpoint.

The local state file only identifies the task and expected breakpoint created by
``mqtt_dock3_wayline_make_breakpoint.py``.  The gateway must recover the active
paused task and breakpoint from its own runtime persistence, normally MongoDB.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_dock3_real_wayline_breakpoint_test import (  # noqa: E402
    assert_prepare_file,
    break_point_from,
    current_waypoint,
    data_output,
    output_ext,
    prepare_payload,
    progress_percent,
)
from mqtt_dock3_wayline_make_breakpoint import DEFAULT_STATE_FILE  # noqa: E402
from mqtt_dock3_wayline_relaunch_breakpoint_test import (  # noqa: E402
    RelaunchBreakpointClient,
    wait_until_waypoint_reaches,
)
from mqtt_test_client import result_code  # noqa: E402
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


def resume_seek_index(breakpoint_index: int) -> int:
    return max(0, int(breakpoint_index) - 1)


def read_state(path: str) -> dict[str, Any]:
    state_path = Path(path).expanduser()
    if not state_path.exists():
        raise SystemExit(f"state file not found: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise SystemExit(f"state file is not an object: {state_path}")
    return state


def wait_until_gateway_recovers_breakpoint(
    client: RelaunchBreakpointClient,
    flight_id: str,
    breakpoint_index: int,
    args: argparse.Namespace,
) -> tuple[int, dict[str, Any] | None]:
    """Wait for restarted gateway to expose the paused task restored from DB."""
    deadline = time.time() + args.recovery_wait_seconds
    last_payload = None
    while time.time() < deadline:
        rc, payload = client.service(
            "flighttask_progress",
            {"flight_id": flight_id},
            args.timeout,
            allowed_results={0, DEVICE_STATE_UNSUPPORTED},
            expected_status=None,
        )
        last_payload = payload
        if rc == 0 and payload:
            status = data_output(payload).get("status")
            bp = break_point_from(payload)
            current = current_waypoint(payload)
            print(
                "recovery progress observed: "
                f"status={status}, current_waypoint={current}, "
                f"breakpoint_index={bp.get('index')}"
            )
            if status == "paused" and bp.get("index") == breakpoint_index:
                return 0, payload
        else:
            print(
                f"waiting for recovered breakpoint: rc={rc}, flight_id={flight_id}",
                file=sys.stderr,
            )
        time.sleep(args.progress_poll_interval)

    print(
        "gateway did not expose recovered paused breakpoint before timeout: "
        f"flight_id={flight_id}, expected_breakpoint={breakpoint_index}",
        file=sys.stderr,
    )
    if last_payload:
        print(json.dumps(last_payload, ensure_ascii=False, indent=2), file=sys.stderr)
    return 43, last_payload


def assert_execute_from_saved_breakpoint(payload: Any, breakpoint_index: int) -> int:
    """Validate new business rule: resume seeks back before continuing."""
    resumed_index = current_waypoint(payload)
    seek_index = resume_seek_index(breakpoint_index)
    if resumed_index is None:
        print("execute reply does not include current waypoint", file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 44
    if resumed_index < seek_index or resumed_index > breakpoint_index:
        print(
            "execute did not start within expected breakpoint resume range: "
            f"seek={seek_index}, breakpoint={breakpoint_index}, actual={resumed_index}",
            file=sys.stderr,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 45
    return 0


def assert_reprepare_from_saved_breakpoint(
    payload: Any,
    wayline_file: str,
    breakpoint_index: int,
    args: argparse.Namespace,
) -> int:
    """Validate reprepare after restart.

    A recovered paused mission does not need to parse/upload the file again.
    The gateway may reply with ``resume_from_breakpoint=true`` and only return
    the persisted breakpoint context.  First-time prepare still uses the strict
    file-summary assertion.
    """
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if data.get("resume_from_breakpoint") is True:
        bp = break_point_from(payload)
        current = current_waypoint(payload)
        if bp.get("index") != breakpoint_index or current != breakpoint_index:
            print(
                "reprepare breakpoint context mismatch: "
                f"expected={breakpoint_index}, current={current}, breakpoint={bp}",
                file=sys.stderr,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
            return 46
        print(
            "reprepare accepted as saved breakpoint resume: "
            f"breakpoint={breakpoint_index}, current_waypoint={current}, "
            f"percent={progress_percent(payload)}"
        )
        return 0
    return assert_prepare_file(payload, wayline_file, args)


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
        f"flight_id={flight_id}, breakpoint={breakpoint_index}, "
        f"resume_seek={resume_seek_index(breakpoint_index)}, file={wayline_file}"
    )

    if not args.skip_recovery_wait:
        rc, _ = wait_until_gateway_recovers_breakpoint(client, flight_id, breakpoint_index, args)
        if rc != 0:
            return rc

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
        rc = assert_reprepare_from_saved_breakpoint(
            reprepare,
            wayline_file,
            breakpoint_index,
            args,
        )
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
    rc = assert_execute_from_saved_breakpoint(execute, breakpoint_index)
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
    parser.add_argument("--recovery-wait-seconds", type=float, default=20.0)
    parser.add_argument("--min-waypoint-advance", type=int, default=1)
    parser.add_argument("--require-gateway-parse", action="store_true")
    parser.add_argument("--skip-recovery-wait", action="store_true")
    parser.add_argument("--skip-reprepare", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--use-flighttask-stop", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.progress_poll_interval <= 0:
        parser.error("--progress-poll-interval must be positive")
    if args.recovery_wait_seconds <= 0:
        parser.error("--recovery-wait-seconds must be positive")
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
        rc = run_flow(client, args)
        if rc != 0:
            return rc
        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(
                "strict stale replies observed: "
                f"stats={client.stats}, samples={client.stale_sample_summary()}",
                file=sys.stderr,
            )
            return 41
        print(
            "dock3 saved breakpoint resume test ok "
            f"(ignored_total={stale_total}, accepted={client.stats['accepted']}, "
            f"events={client.stats['events']})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
