#!/usr/bin/env python3
"""Show platform-visible interrupted wayline information, then resume or cancel it."""

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
    break_point_from,
    current_waypoint,
    data_output,
    progress_percent,
)
from mqtt_dock3_wayline_relaunch_breakpoint_test import RelaunchBreakpointClient  # noqa: E402
from mqtt_test_client import result_code  # noqa: E402
from src.database.connection import configure_database, get_mongo_database  # noqa: E402
from src.utils.config import Config, load_config  # noqa: E402
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


def task_status_from(payload: Any) -> str:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    value = data.get("task_status")
    return value if isinstance(value, str) else ""


def is_completion_payload(payload: Any) -> bool:
    status = data_output(payload).get("status")
    task_status = task_status_from(payload)
    percent = progress_percent(payload)
    if status in {"ok", "completed"}:
        return True
    if task_status == "completed":
        return True
    return percent == 100 and status not in {"failed", "canceled", "rejected"}


def is_failed_terminal_payload(payload: Any) -> bool:
    status = data_output(payload).get("status")
    task_status = task_status_from(payload)
    return status in {"failed", "canceled", "rejected"} or task_status in {
        "failed",
        "canceled",
        "rejected",
    }


def print_mongodb_summary(args: argparse.Namespace, flight_id: str) -> None:
    try:
        load_config(args.config)
        configure_database(Config.get_database_config())
        db = get_mongo_database()
        doc = db["missions"].find_one({"name": flight_id}, sort=[("created_at", -1)])
        if not doc:
            print(f"mongodb interrupted task summary: mission not found flight_id={flight_id}")
            return
        execution_conditions = doc.get("execution_conditions") or {}
        summary = {
            "flight_id": doc.get("name"),
            "status": doc.get("status"),
            "breakpoint": execution_conditions.get("breakpoint"),
            "breakpoint_updated_at": execution_conditions.get("breakpoint_updated_at"),
            "waypoints": db["waypoints"].count_documents({"mission_id": doc.get("id")}),
        }
        print("mongodb interrupted task summary:")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"mongodb summary unavailable: {exc}", file=sys.stderr)


def query_platform_visible_task(
    client: RelaunchBreakpointClient,
    flight_id: str,
    args: argparse.Namespace,
) -> tuple[int, dict[str, Any] | None]:
    rc, progress = client.service(
        "flighttask_progress",
        {"flight_id": flight_id},
        args.timeout,
        allowed_results={0, DEVICE_STATE_UNSUPPORTED},
        expected_status=None,
    )
    if rc != 0:
        return rc, progress
    print("platform visible interrupted task:")
    print(json.dumps(progress, ensure_ascii=False, indent=2))
    return 0, progress


def wait_until_completion(
    client: RelaunchBreakpointClient,
    flight_id: str,
    args: argparse.Namespace,
) -> tuple[int, dict[str, Any] | None]:
    deadline = time.time() + args.completion_timeout
    last_payload = None
    while time.time() < deadline:
        rc, payload = query_platform_visible_task(client, flight_id, args)
        last_payload = payload
        if rc != 0:
            return rc, payload
        if payload and is_completion_payload(payload):
            return 0, payload
        if payload and is_failed_terminal_payload(payload):
            print("mission reached failed terminal state before completion", file=sys.stderr)
            return 4, payload
        time.sleep(args.progress_poll_interval)
    print(f"timeout waiting mission completion: flight_id={flight_id}", file=sys.stderr)
    return 5, last_payload


def run_flow(client: RelaunchBreakpointClient, args: argparse.Namespace) -> int:
    rc, progress = query_platform_visible_task(client, args.flight_id, args)
    if rc != 0:
        return rc
    print_mongodb_summary(args, args.flight_id)

    if args.action == "show":
        return 0

    status = data_output(progress).get("status") if progress else None
    breakpoint = break_point_from(progress)
    if status != "paused" or not breakpoint:
        print(
            "requested action requires platform-visible paused task with break_point: "
            f"status={status}, break_point={breakpoint}",
            file=sys.stderr,
        )
        return 3

    if args.action == "cancel":
        rc, cancel = client.service(
            "flighttask_undo",
            {"flight_ids": [args.flight_id]},
            args.timeout,
            allowed_results={0, DEVICE_STATE_UNSUPPORTED},
            expected_status=None,
        )
        if rc != 0:
            return rc
        print(f"platform cancel result: result={result_code(cancel)}")
        print_mongodb_summary(args, args.flight_id)
        return 0

    rc, resume = client.service(
        "flighttask_recovery",
        {"flight_id": args.flight_id},
        args.timeout,
        allowed_results={0},
        expected_status="in_progress",
    )
    if rc != 0:
        return rc
    print(
        "platform resume accepted: "
        f"flight_id={args.flight_id}, current_waypoint={current_waypoint(resume)}, "
        f"percent={progress_percent(resume)}"
    )

    if args.wait_completion:
        rc, completed = wait_until_completion(client, args.flight_id, args)
        if rc != 0:
            return rc
        print(
            "platform resumed mission completed: "
            f"flight_id={args.flight_id}, current_waypoint={current_waypoint(completed)}, "
            f"percent={progress_percent(completed)}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Platform-only interrupted wayline decision script")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", required=True)
    parser.add_argument("--action", choices=["show", "resume", "cancel"], default="show")
    parser.add_argument("--wait-completion", action="store_true")
    parser.add_argument("--completion-timeout", type=float, default=600.0)
    parser.add_argument("--progress-poll-interval", type=float, default=2.0)
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
        client_id_prefix="edge_gateway_platform_wayline_decision",
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
            "platform wayline decision script ok "
            f"(action={args.action}, accepted={client.stats['accepted']}, "
            f"events={client.stats['events']}, ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
