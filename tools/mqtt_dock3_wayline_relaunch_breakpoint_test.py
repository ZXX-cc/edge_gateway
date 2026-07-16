#!/usr/bin/env python3
"""Acceptance test for relaunching a Dock3 wayline task from a saved breakpoint.

This covers the business flow where a running wayline is interrupted by
``flighttask_return`` and the next ``flighttask_execute`` for the same
``flight_id`` should take off again and continue from the persisted breakpoint.
"""

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
    assert_breakpoint,
    assert_prepare_file,
    assert_status,
    break_point_from,
    current_waypoint,
    data_output,
    output_ext,
    prepare_payload,
    progress_percent,
)
from mqtt_test_client import MqttRequestClient, result_code  # noqa: E402
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


class RelaunchBreakpointClient(MqttRequestClient):
    def service(
        self,
        method: str,
        data: dict[str, Any] | None,
        timeout: float,
        allowed_results: set[int] | None = None,
        expected_status: str | None = None,
    ) -> tuple[int, dict[str, Any] | None]:
        return self.request(
            method,
            data or {},
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else allowed_results,
            expected_status=expected_status,
        )


def assert_relaunch_index(
    payload: Any,
    paused_index: Any,
    require_resume_index: bool,
) -> int:
    resumed_index = current_waypoint(payload)
    if not require_resume_index:
        return 0
    if isinstance(paused_index, int) and resumed_index is not None and resumed_index < paused_index:
        print(
            f"relaunch current waypoint moved backward: paused={paused_index}, relaunched={resumed_index}",
            file=sys.stderr,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 36
    return 0


def assert_min_breakpoint_index(payload: Any, min_index: int) -> int:
    breakpoint = break_point_from(payload)
    index = breakpoint.get("index")
    if not isinstance(index, int) or index < min_index:
        print(
            f"breakpoint index did not reach minimum: expected>={min_index}, actual={index}",
            file=sys.stderr,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 37
    return 0


def wait_until_waypoint_reaches(
    client: RelaunchBreakpointClient,
    flight_id: str,
    target_index: int,
    args: argparse.Namespace,
    label: str,
) -> tuple[int, dict[str, Any] | None]:
    last_payload = None
    while True:
        rc, payload = client.service(
            "flighttask_progress",
            {"flight_id": flight_id},
            args.timeout,
            allowed_results={0},
            expected_status=None,
        )
        if rc != 0:
            return rc, payload
        last_payload = payload
        waypoint = current_waypoint(payload)
        percent = progress_percent(payload)
        status = data_output(payload).get("status")
        print(
            f"{label} progress observed: "
            f"status={status}, target_waypoint>={target_index}, "
            f"current_waypoint={waypoint}, percent={percent}"
        )
        if isinstance(waypoint, int) and waypoint >= target_index:
            return 0, payload

        if status in {"failed", "canceled", "rejected", "timeout"}:
            print(
                f"{label} ended before reaching target waypoint: "
                f"status={status}, target>={target_index}, current={waypoint}",
                file=sys.stderr,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
            return 39, payload
        if status == "ok":
            print(
                f"{label} completed before reaching target waypoint: "
                f"target>={target_index}, current={waypoint}",
                file=sys.stderr,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
            return 39, payload
        time.sleep(args.progress_poll_interval)


def wait_until_reexecute_advances(
    client: RelaunchBreakpointClient,
    flight_id: str,
    breakpoint_index: Any,
    args: argparse.Namespace,
) -> tuple[int, dict[str, Any] | None]:
    if not isinstance(breakpoint_index, int):
        print(f"breakpoint index is not int: {breakpoint_index}", file=sys.stderr)
        return 38, None
    target_index = breakpoint_index + args.min_reexecute_waypoint_advance
    return wait_until_waypoint_reaches(client, flight_id, target_index, args, "post-reexecute")


def wait_until_paused_with_breakpoint(
    client: RelaunchBreakpointClient,
    flight_id: str,
    args: argparse.Namespace,
) -> tuple[int, dict[str, Any] | None]:
    while True:
        rc, payload = client.service(
            "flighttask_progress",
            {"flight_id": flight_id},
            args.timeout,
            allowed_results={0},
            expected_status=None,
        )
        if rc != 0:
            return rc, payload
        status = data_output(payload).get("status")
        waypoint = current_waypoint(payload)
        percent = progress_percent(payload)
        breakpoint = break_point_from(payload)
        print(
            "return progress observed: "
            f"status={status}, current_waypoint={waypoint}, "
            f"percent={percent}, breakpoint_index={breakpoint.get('index')}"
        )
        if status == "paused":
            rc = assert_breakpoint(payload, args)
            if rc != 0:
                return rc, payload
            rc = assert_min_breakpoint_index(payload, args.min_breakpoint_index)
            if rc != 0:
                return rc, payload
            return 0, payload
        if status in {"failed", "canceled", "rejected", "timeout", "ok"}:
            print(
                f"return did not reach paused state: status={status}",
                file=sys.stderr,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
            return 42, payload
        time.sleep(args.progress_poll_interval)


def run_flow(client: RelaunchBreakpointClient, args: argparse.Namespace) -> int:
    flight_id = args.flight_id or f"real-hd-relaunch-breakpoint-{uuid.uuid4().hex[:8]}"
    wayline_file = args.wayline_file
    print(f"start wayline relaunch breakpoint test: flight_id={flight_id}, file={wayline_file}")

    rc, prepare = client.service(
        "flighttask_prepare",
        prepare_payload(flight_id, wayline_file, args),
        args.timeout,
        allowed_results={0},
        expected_status="sent",
    )
    if rc != 0:
        return rc
    rc = assert_prepare_file(prepare, wayline_file, args)
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
        "initial execute ok: "
        f"percent={progress_percent(execute)}, current_waypoint={current_waypoint(execute)}"
    )

    rc, _ = wait_until_waypoint_reaches(
        client,
        flight_id,
        args.min_breakpoint_index,
        args,
        "initial execute",
    )
    if rc != 0:
        return rc

    rc, returned = client.service(
        "flighttask_return",
        {"flight_id": flight_id, "return_type": args.return_type},
        args.timeout,
        allowed_results={0},
        expected_status="in_progress",
    )
    if rc != 0:
        return rc
    rc = assert_breakpoint(returned, args)
    if rc != 0:
        return rc
    rc = assert_min_breakpoint_index(returned, args.min_breakpoint_index)
    if rc != 0:
        return rc
    print(
        "return requested with breakpoint: "
        f"percent={progress_percent(returned)}, current_waypoint={current_waypoint(returned)}"
    )

    rc, paused_progress = wait_until_paused_with_breakpoint(client, flight_id, args)
    if rc != 0:
        return rc
    paused_index = break_point_from(paused_progress).get("index")

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
            "reprepare accepted for breakpoint re-execute: "
            f"percent={progress_percent(reprepare)}, current_waypoint={current_waypoint(reprepare)}"
        )

    rc, relaunched = client.service(
        "flighttask_execute",
        {"flight_id": flight_id},
        args.timeout,
        allowed_results={0},
        expected_status="in_progress",
    )
    if rc != 0:
        return rc
    relaunched_ext = output_ext(relaunched)
    if relaunched_ext.get("flight_id") not in ("", None, flight_id):
        print(f"relaunched flight_id mismatch: {relaunched_ext.get('flight_id')} != {flight_id}", file=sys.stderr)
        return 35
    rc = assert_relaunch_index(relaunched, paused_index, args.require_resume_index)
    if rc != 0:
        return rc
    print(
        "re-execute accepted from breakpoint: "
        f"paused_index={paused_index}, relaunched_index={current_waypoint(relaunched)}, "
        f"percent={progress_percent(relaunched)}"
    )

    if not args.skip_reexecute_advance:
        rc, progress = wait_until_reexecute_advances(client, flight_id, paused_index, args)
        if rc != 0:
            return rc
        print(
            "re-executed task advanced beyond breakpoint: "
            f"breakpoint={paused_index}, current_waypoint={current_waypoint(progress)}, "
            f"percent={progress_percent(progress)}"
        )

    cleanup_method = "flighttask_stop" if args.use_flighttask_stop else "flighttask_undo"
    cleanup_payload = (
        {"flight_id": flight_id, "reason": 0}
        if cleanup_method == "flighttask_stop"
        else {"flight_ids": [flight_id]}
    )
    rc, cleanup = client.service(
        cleanup_method,
        cleanup_payload,
        args.timeout,
        allowed_results={0, DEVICE_STATE_UNSUPPORTED},
        expected_status=None,
    )
    if rc != 0:
        return rc
    if result_code(cleanup) == 0:
        rc = assert_status(cleanup, "canceled", cleanup_method)
        if rc != 0:
            return rc
    else:
        print(f"cleanup returned non-zero but accepted: result={result_code(cleanup)}")

    missing_events = [method for method in ("flighttask_ready", "flighttask_progress") if not client.has_event(method)]
    if missing_events:
        print(f"missing expected events: {missing_events}", file=sys.stderr)
        return 40

    stale_total = client.stale_total()
    if args.fail_on_stale and stale_total:
        print(
            "strict stale replies observed: "
            f"stats={client.stats}, samples={client.stale_sample_summary()}",
            file=sys.stderr,
        )
        return 41

    print(
        "dock3 wayline re-execute from breakpoint test ok "
        f"(flight_id={flight_id}, file={wayline_file}, paused_index={paused_index}, "
        f"accepted={client.stats['accepted']}, events={client.stats['events']}, "
        f"ignored_mismatched={client.stats['ignored_mismatched']}, "
        f"ignored_legacy={client.stats['ignored_legacy']}, ignored_late={client.stats['ignored_late']}, "
        f"ignored_duplicate={client.stats['ignored_duplicate']}, "
        f"ignored_unknown={client.unrelated_reply_total()}, "
        f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, ignored_total={stale_total})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test DJI Dock3 wayline relaunch from breakpoint over MQTT"
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None, help="MQTT broker host; default comes from config")
    parser.add_argument("--port", type=int, default=None, help="MQTT broker port; default comes from config")
    parser.add_argument("--sn", default=None, help="Gateway SN; default comes from config")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--wayline-file", default=DEFAULT_REAL_WAYLINE_FILE)
    parser.add_argument("--file-fingerprint", default="")
    parser.add_argument("--expect-waypoint-count", type=int, default=10)
    parser.add_argument("--execute-delay-seconds", type=float, default=0.0)
    parser.add_argument("--rth-altitude", type=float, default=100.0)
    parser.add_argument("--out-of-control-action", type=int, default=0)
    parser.add_argument("--exit-wayline-when-rc-lost", type=int, default=0)
    parser.add_argument("--return-type", type=int, choices=[0, 1], default=0)
    parser.add_argument("--progress-poll-interval", type=float, default=1.0)
    parser.add_argument("--min-progress-percent", type=int, default=1)
    parser.add_argument("--min-breakpoint-index", type=int, default=1)
    parser.add_argument("--min-reexecute-waypoint-advance", type=int, default=1)
    parser.add_argument("--expect-breakpoint-index", type=int, default=None)
    parser.add_argument("--require-gateway-parse", action="store_true")
    parser.add_argument("--require-resume-index", action="store_true")
    parser.add_argument("--skip-reprepare", action="store_true")
    parser.add_argument("--skip-reexecute-advance", action="store_true")
    parser.add_argument("--use-flighttask-stop", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.execute_delay_seconds < 0:
        parser.error("--execute-delay-seconds must be non-negative")
    if args.expect_waypoint_count <= 0:
        parser.error("--expect-waypoint-count must be positive")
    if args.progress_poll_interval <= 0:
        parser.error("--progress-poll-interval must be positive")
    if args.min_breakpoint_index < 0:
        parser.error("--min-breakpoint-index must be non-negative")
    if args.min_reexecute_waypoint_advance <= 0:
        parser.error("--min-reexecute-waypoint-advance must be positive")
    if not args.wayline_file:
        parser.error("--wayline-file must be provided")

    client = RelaunchBreakpointClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_wayline_relaunch_breakpoint",
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
