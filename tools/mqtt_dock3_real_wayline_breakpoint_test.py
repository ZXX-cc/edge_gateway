#!/usr/bin/env python3
"""Acceptance test for real Dock3 wayline breakpoint resume.

Default real wayline file:

    /home/fenghuolun/zwz/HD.kml

The script drives the independent links:

platform MQTT -> gateway
gateway <-> flight controller/SITL
gateway <-> nest simulator

Flow:

1. flighttask_prepare with simulate_mission disabled
2. flighttask_execute
3. wait/query in-progress state
4. flighttask_pause and assert break_point is reported
5. save the break_point as external/cloud-side resume evidence
6. cancel the interrupted task to release gateway runtime state
7. flighttask_prepare again with the saved break_point
8. flighttask_execute and assert it continues from the breakpoint context
9. cleanup by flighttask_undo or flighttask_stop
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

from mqtt_test_client import MqttRequestClient, output_status, result_code  # noqa: E402
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


DEFAULT_REAL_WAYLINE_FILE = "/home/fenghuolun/zwz/HD.kml"


def now_ms() -> int:
    return int(time.time() * 1000)


def local_file_reference(path: str) -> str:
    local_path = Path(path).expanduser()
    try:
        resolved = local_path.resolve()
    except OSError:
        resolved = local_path
    return str(resolved)


def prepare_payload(
    flight_id: str,
    wayline_file: str,
    args: argparse.Namespace,
    break_point: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "flight_id": flight_id,
        "task_type": 0,
        "execute_time": now_ms() + int(args.execute_delay_seconds * 1000),
        "file": {
            "url": local_file_reference(wayline_file),
            "fingerprint": args.file_fingerprint or f"real-{Path(wayline_file).name}-{flight_id}",
        },
        "ready_conditions": {},
        "executable_conditions": {
            "storage_capacity": 0,
        },
        "rth_altitude": args.rth_altitude,
        "out_of_control_action": args.out_of_control_action,
        "exit_wayline_when_rc_lost": args.exit_wayline_when_rc_lost,
        "simulate_mission": {"is_enable": 0},
    }
    if break_point:
        payload["break_point"] = break_point
    return payload


def data_output(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    output = payload.get("data", {}).get("output", {})
    return output if isinstance(output, dict) else {}


def output_ext(payload: Any) -> dict[str, Any]:
    ext = data_output(payload).get("ext", {})
    return ext if isinstance(ext, dict) else {}


def progress_percent(payload: Any) -> int | None:
    progress = data_output(payload).get("progress", {})
    if not isinstance(progress, dict):
        return None
    value = progress.get("percent")
    return value if isinstance(value, int) else None


def current_waypoint(payload: Any) -> int | None:
    ext = output_ext(payload)
    for key in ("current_waypoint", "current_waypoint_index"):
        value = ext.get(key)
        if isinstance(value, int):
            return value
    return None


def break_point_from(payload: Any) -> dict[str, Any]:
    break_point = output_ext(payload).get("break_point", {})
    return break_point if isinstance(break_point, dict) else {}


def file_summary_from(payload: Any) -> dict[str, Any]:
    file_summary = output_ext(payload).get("file", {})
    return file_summary if isinstance(file_summary, dict) else {}


def assert_status(payload: Any, expected: str, method: str) -> int:
    actual = output_status(payload)
    if actual != expected:
        print(f"{method} output.status mismatch: expected={expected}, actual={actual}", file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 20
    return 0


def assert_prepare_file(payload: Any, wayline_file: str, args: argparse.Namespace) -> int:
    summary = file_summary_from(payload)
    if not summary:
        print("flighttask_prepare did not return output.ext.file", file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 21

    source = str(summary.get("source", ""))
    expected_name = Path(wayline_file).name
    if expected_name and expected_name not in source:
        print(f"prepare file source mismatch: expected name={expected_name}, source={source}", file=sys.stderr)
        return 22

    if args.require_gateway_parse:
        if not summary.get("parsed"):
            print(f"gateway did not parse real wayline file: {summary}", file=sys.stderr)
            return 23
        waypoint_count = summary.get("waypoint_count")
        if not isinstance(waypoint_count, int) or waypoint_count < args.expect_waypoint_count:
            print(
                f"waypoint_count too small: expected>={args.expect_waypoint_count}, actual={waypoint_count}",
                file=sys.stderr,
            )
            return 24
    elif summary.get("parsed"):
        print(
            "real wayline parsed by gateway: "
            f"format={summary.get('format')}, waypoint_count={summary.get('waypoint_count')}, "
            f"files={summary.get('files')}"
        )
    else:
        print(
            "real wayline delegated or not parsed by gateway: "
            f"delegated_to={summary.get('delegated_to')}, source={summary.get('source')}"
        )
    return 0


def assert_breakpoint(payload: Any, args: argparse.Namespace) -> int:
    bp = break_point_from(payload)
    if not bp:
        print("paused progress did not include output.ext.break_point", file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 30

    index = bp.get("index")
    state = bp.get("state")
    progress = bp.get("progress")
    if index is not None and (not isinstance(index, int) or index < 0):
        print(f"invalid break_point.index: {index}", file=sys.stderr)
        return 31
    if state is not None and state not in (0, 1):
        print(f"invalid break_point.state: {state}", file=sys.stderr)
        return 32
    if progress is not None and (not isinstance(progress, (int, float)) or not 0 <= float(progress) <= 1):
        print(f"invalid break_point.progress: {progress}", file=sys.stderr)
        return 33
    if args.expect_breakpoint_index is not None and index != args.expect_breakpoint_index:
        print(
            f"breakpoint index mismatch: expected={args.expect_breakpoint_index}, actual={index}",
            file=sys.stderr,
        )
        return 34

    print(f"breakpoint ok: index={index}, state={state}, progress={progress}, reason={bp.get('reason')}")
    return 0


def normalize_saved_breakpoint(break_point: dict[str, Any]) -> dict[str, Any]:
    saved = dict(break_point)
    saved.setdefault("state", 1)
    saved.setdefault("progress", 0)
    if "index" not in saved:
        saved["index"] = 0
    return saved


def save_breakpoint_evidence(
    flight_id: str,
    wayline_file: str,
    break_point: dict[str, Any],
    args: argparse.Namespace,
) -> Path:
    output = args.breakpoint_output
    if not output:
        output = f"logs/wayline_breakpoint/{flight_id}_breakpoint.json"
    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "flight_id": flight_id,
        "wayline_file": wayline_file,
        "break_point": break_point,
        "saved_at": now_ms(),
        "source": "flighttask_pause/flighttask_progress",
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"breakpoint saved for resume: {path}")
    return path


def assert_resume_uses_breakpoint(payload: Any, expected_breakpoint: dict[str, Any]) -> int:
    ext = output_ext(payload)
    resumed_bp = ext.get("break_point")
    if not isinstance(resumed_bp, dict) or not resumed_bp:
        print("resume reply/progress did not carry output.ext.break_point", file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 37

    expected_index = expected_breakpoint.get("index")
    actual_index = resumed_bp.get("index")
    if isinstance(expected_index, int) and actual_index != expected_index:
        print(
            f"resume breakpoint index mismatch: expected={expected_index}, actual={actual_index}",
            file=sys.stderr,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 38
    print(
        "resume breakpoint context ok: "
        f"index={actual_index}, progress={resumed_bp.get('progress')}, reason={resumed_bp.get('reason')}"
    )
    return 0


def wait_until_progress_observed(
    client: MqttRequestClient,
    flight_id: str,
    args: argparse.Namespace,
) -> tuple[int, dict[str, Any] | None]:
    last_payload = None
    deadline = time.time() + max(0.0, args.progress_wait)
    while time.time() < deadline:
        rc, payload = client.request(
            "flighttask_progress",
            {"flight_id": flight_id},
            args.timeout,
            channel="services",
            allowed_results={0},
            expected_status="in_progress",
        )
        if rc != 0:
            return rc, payload
        last_payload = payload
        percent = progress_percent(payload)
        waypoint = current_waypoint(payload)
        print(f"progress observed: percent={percent}, current_waypoint={waypoint}")
        if percent is not None and percent >= args.min_progress_percent:
            return 0, payload
        time.sleep(args.progress_poll_interval)

    if last_payload is not None and args.min_progress_percent <= 0:
        return 0, last_payload
    print(f"progress did not reach {args.min_progress_percent}% within {args.progress_wait}s", file=sys.stderr)
    if last_payload:
        print(json.dumps(last_payload, ensure_ascii=False, indent=2), file=sys.stderr)
    return 25, last_payload


class RealWaylineBreakpointClient(MqttRequestClient):
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


def run_flow(client: RealWaylineBreakpointClient, args: argparse.Namespace) -> int:
    flight_id = args.flight_id or f"real-hd-breakpoint-{uuid.uuid4().hex[:8]}"
    wayline_file = args.wayline_file
    print(f"start real wayline breakpoint test: flight_id={flight_id}, file={wayline_file}")

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
        "execute ok: "
        f"percent={progress_percent(execute)}, current_waypoint={current_waypoint(execute)}"
    )

    rc, _ = wait_until_progress_observed(client, flight_id, args)
    if rc != 0:
        return rc

    rc, pause = client.service(
        "flighttask_pause",
        {},
        args.timeout,
        allowed_results={0},
        expected_status="paused",
    )
    if rc != 0:
        return rc
    rc = assert_breakpoint(pause, args)
    if rc != 0:
        return rc

    rc, paused_progress = client.service(
        "flighttask_progress",
        {"flight_id": flight_id},
        args.timeout,
        allowed_results={0},
        expected_status="paused",
    )
    if rc != 0:
        return rc
    rc = assert_breakpoint(paused_progress, args)
    if rc != 0:
        return rc
    saved_breakpoint = normalize_saved_breakpoint(break_point_from(paused_progress))
    paused_index = saved_breakpoint.get("index")
    save_breakpoint_evidence(flight_id, wayline_file, saved_breakpoint, args)

    release_method = "flighttask_stop" if args.use_flighttask_stop else "flighttask_undo"
    release_payload = (
        {"flight_id": flight_id, "reason": 0}
        if release_method == "flighttask_stop"
        else {"flight_ids": [flight_id]}
    )
    rc, release = client.service(
        release_method,
        release_payload,
        args.timeout,
        allowed_results={0},
        expected_status="canceled",
    )
    if rc != 0:
        return rc
    print(
        "interrupted task released after breakpoint save: "
        f"method={release_method}, status={output_status(release)}"
    )

    resume_flight_id = args.resume_flight_id or flight_id
    rc, resume_prepare = client.service(
        "flighttask_prepare",
        prepare_payload(resume_flight_id, wayline_file, args, break_point=saved_breakpoint),
        args.timeout,
        allowed_results={0},
        expected_status="sent",
    )
    if rc != 0:
        return rc
    rc = assert_prepare_file(resume_prepare, wayline_file, args)
    if rc != 0:
        return rc
    rc = assert_resume_uses_breakpoint(resume_prepare, saved_breakpoint)
    if rc != 0:
        return rc

    rc, resumed = client.service(
        "flighttask_execute",
        {"flight_id": resume_flight_id},
        args.timeout,
        allowed_results={0},
        expected_status="in_progress",
    )
    if rc != 0:
        return rc
    resumed_index = current_waypoint(resumed)
    if args.require_resume_index and isinstance(paused_index, int) and resumed_index is not None:
        if resumed_index < paused_index:
            print(
                f"resume current waypoint moved backward: paused={paused_index}, resumed={resumed_index}",
                file=sys.stderr,
            )
            return 36
    rc = assert_resume_uses_breakpoint(resumed, saved_breakpoint)
    if rc != 0:
        return rc
    print(
        "breakpoint resume execute ok: "
        f"resume_flight_id={resume_flight_id}, breakpoint_index={paused_index}, "
        f"current_waypoint={resumed_index}, percent={progress_percent(resumed)}"
    )

    rc, progress = client.service(
        "flighttask_progress",
        {"flight_id": resume_flight_id},
        args.timeout,
        allowed_results={0},
        expected_status="in_progress",
    )
    if rc != 0:
        return rc
    rc = assert_resume_uses_breakpoint(progress, saved_breakpoint)
    if rc != 0:
        return rc
    print(
        "post-resume progress ok: "
        f"percent={progress_percent(progress)}, current_waypoint={current_waypoint(progress)}"
    )

    cleanup_method = "flighttask_stop" if args.use_flighttask_stop else "flighttask_undo"
    cleanup_payload = (
        {"flight_id": resume_flight_id, "reason": 0}
        if cleanup_method == "flighttask_stop"
        else {"flight_ids": [resume_flight_id]}
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
        "dock3 real wayline breakpoint test ok "
        f"(flight_id={flight_id}, resume_flight_id={resume_flight_id}, "
        f"breakpoint_index={paused_index}, file={wayline_file}, accepted={client.stats['accepted']}, "
        f"events={client.stats['events']}, ignored_mismatched={client.stats['ignored_mismatched']}, "
        f"ignored_legacy={client.stats['ignored_legacy']}, ignored_late={client.stats['ignored_late']}, "
        f"ignored_duplicate={client.stats['ignored_duplicate']}, "
        f"ignored_unknown={client.unrelated_reply_total()}, "
        f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, ignored_total={stale_total})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test real DJI Dock3 wayline breakpoint resume over MQTT")
    add_config_argument(parser)
    parser.add_argument("--host", default=None, help="MQTT broker host; default comes from config")
    parser.add_argument("--port", type=int, default=None, help="MQTT broker port; default comes from config")
    parser.add_argument("--sn", default=None, help="Gateway SN; default comes from config")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--resume-flight-id", default="", help="flight_id used for the second prepare with break_point; defaults to --flight-id")
    parser.add_argument("--wayline-file", default=DEFAULT_REAL_WAYLINE_FILE)
    parser.add_argument("--file-fingerprint", default="")
    parser.add_argument("--expect-waypoint-count", type=int, default=10)
    parser.add_argument("--execute-delay-seconds", type=float, default=30.0)
    parser.add_argument("--rth-altitude", type=float, default=100.0)
    parser.add_argument("--out-of-control-action", type=int, default=0)
    parser.add_argument("--exit-wayline-when-rc-lost", type=int, default=0)
    parser.add_argument("--progress-wait", type=float, default=15.0)
    parser.add_argument("--progress-poll-interval", type=float, default=1.0)
    parser.add_argument("--min-progress-percent", type=int, default=0)
    parser.add_argument("--expect-breakpoint-index", type=int, default=None)
    parser.add_argument(
        "--breakpoint-output",
        default="",
        help="path to save the captured breakpoint JSON evidence; default logs/wayline_breakpoint/<flight_id>_breakpoint.json",
    )
    parser.add_argument("--require-gateway-parse", action="store_true")
    parser.add_argument("--require-resume-index", action="store_true")
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
    if args.progress_wait < 0:
        parser.error("--progress-wait must be non-negative")
    if args.progress_poll_interval <= 0:
        parser.error("--progress-poll-interval must be positive")
    if not args.wayline_file:
        parser.error("--wayline-file must be provided")

    client = RealWaylineBreakpointClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_real_wayline_breakpoint",
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
