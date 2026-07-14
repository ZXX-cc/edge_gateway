#!/usr/bin/env python3
"""Acceptance test for the Dock3 wayline management page.

This script drives the gateway through the main Dock3 wayline lifecycle over
MQTT services:

1. prepare a local mock KMZ wayline
2. execute the task
3. query progress
4. pause and recover
5. query airport task progress
6. trigger return and cancel return
7. stop/cancel the task
8. verify invalid requests return Dock3 error codes

It is intentionally safe by default: ``simulate_mission.is_enable`` is set to 1
and no real aircraft or real Dock 3 is required.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, output_status, result_code  # noqa: E402
from src.utils.dji_error_codes import (  # noqa: E402
    DEVICE_STATE_UNSUPPORTED,
    INVALID_COMMAND_FORMAT,
)
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


DEFAULT_WAYPOINTS = [
    (149.165237, -35.363262, 50),
    (149.165537, -35.363562, 60),
    (149.165837, -35.363862, 70),
]


def now_ms() -> int:
    return int(time.time() * 1000)


def write_mock_kmz(path: Path, waypoints: list[tuple[float, float, float]] | None = None) -> None:
    waypoints = waypoints or DEFAULT_WAYPOINTS
    coordinates = "\n".join(f"          {lon},{lat},{alt}" for lon, lat, alt in waypoints)
    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.2">
  <Document>
    <Placemark>
      <wpml:waypointSpeed>6</wpml:waypointSpeed>
      <LineString>
        <coordinates>
{coordinates}
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("wpmz/template.kml", kml)


def local_file_reference(path: str) -> str:
    local_path = Path(path).expanduser().resolve()
    return str(local_path) if local_path.exists() else path


def prepare_payload(flight_id: str, wayline_file: str) -> dict[str, Any]:
    return {
        "flight_id": flight_id,
        "task_type": 0,
        "execute_time": now_ms() + 30_000,
        "file": {
            "url": local_file_reference(wayline_file),
            "fingerprint": f"mock-{flight_id}",
        },
        "ready_conditions": {},
        "executable_conditions": {
            "storage_capacity": 0,
        },
        "rth_altitude": 80,
        "out_of_control_action": 0,
        "exit_wayline_when_rc_lost": 0,
        "simulate_mission": {
            "is_enable": 1,
            "latitude": -35.363262,
            "longitude": 149.165237,
        },
    }


def output_ext(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    output = payload.get("data", {}).get("output", {})
    ext = output.get("ext") if isinstance(output, dict) else {}
    return ext if isinstance(ext, dict) else {}


def assert_status(payload: Any, expected: str, method: str) -> int:
    actual = output_status(payload)
    if actual != expected:
        print(f"{method} output.status mismatch: expected={expected}, actual={actual}", file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 20
    return 0


def assert_file_parsed(payload: Any, min_waypoints: int) -> int:
    file_summary = output_ext(payload).get("file", {})
    if not isinstance(file_summary, dict) or not file_summary.get("parsed"):
        print(f"wayline file was not parsed: {file_summary}", file=sys.stderr)
        return 21
    waypoint_count = file_summary.get("waypoint_count")
    if not isinstance(waypoint_count, int) or waypoint_count < min_waypoints:
        print(
            f"wayline waypoint_count too small: expected>={min_waypoints}, actual={waypoint_count}",
            file=sys.stderr,
        )
        return 22
    print(
        "航线文件解析通过: "
        f"format={file_summary.get('format')}, waypoint_count={waypoint_count}, "
        f"files={file_summary.get('files')}"
    )
    return 0


class WaylineManagementClient(MqttRequestClient):
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


def run_management_flow(client: WaylineManagementClient, args: argparse.Namespace) -> int:
    flight_id = args.flight_id or f"dock3-wayline-mgmt-{uuid.uuid4().hex[:8]}"

    print(f"开始 Dock3 航线管理测试: flight_id={flight_id}")
    rc, payload = client.service(
        "flighttask_prepare",
        prepare_payload(flight_id, args.wayline_file),
        args.timeout,
        expected_status="sent",
    )
    if rc != 0:
        return rc
    rc = assert_file_parsed(payload, args.expect_waypoint_count)
    if rc != 0:
        return rc

    steps: list[tuple[str, dict[str, Any], set[int], str | None]] = [
        ("flighttask_execute", {"flight_id": flight_id}, {0}, "in_progress"),
        ("flighttask_progress", {"flight_id": flight_id}, {0}, "in_progress"),
        ("flighttask_pause", {}, {0}, "paused"),
        ("flighttask_progress", {"flight_id": flight_id}, {0}, "paused"),
        ("flighttask_recovery", {}, {0}, "in_progress"),
        ("flighttask_progress_get", {"sn": args.sn}, {0}, None),
        ("flighttask_return", {"flight_id": flight_id, "return_type": 0}, {0}, "in_progress"),
        ("return_home_cancel", {}, {0}, "paused"),
    ]

    for method, data, allowed, status in steps:
        rc, payload = client.service(method, data, args.timeout, allowed, status)
        if rc != 0:
            return rc
        if method == "flighttask_progress":
            rc = assert_file_parsed(payload, args.expect_waypoint_count)
            if rc != 0:
                return rc
        time.sleep(args.step_delay)

    stop_method = "flighttask_stop" if args.use_flighttask_stop else "flighttask_undo"
    stop_payload = {"flight_id": flight_id, "reason": 0} if stop_method == "flighttask_stop" else {"flight_ids": [flight_id]}
    rc, payload = client.service(stop_method, stop_payload, args.timeout, {0}, "canceled")
    if rc != 0:
        return rc

    invalid_cases: list[tuple[str, dict[str, Any], set[int], str | None]] = [
        (
            "flighttask_prepare",
            {"flight_id": "", "task_type": 99, "execute_time": 1},
            {INVALID_COMMAND_FORMAT},
            None,
        ),
        (
            "flighttask_pause",
            {},
            {DEVICE_STATE_UNSUPPORTED},
            "rejected",
        ),
        (
            "flighttask_progress",
            {"flight_id": "not-current-flight"},
            {DEVICE_STATE_UNSUPPORTED},
            "rejected",
        ),
        (
            "return_specific_home",
            {"home_dock_sn": "REMOTE-DOCK-001"},
            {DEVICE_STATE_UNSUPPORTED},
            "rejected",
        ),
        (
            "in_flight_wayline_deliver",
            {
                "in_flight_wayline_id": "air-wayline-001",
                "file": {
                    "url": "https://example.invalid/mission.kmz",
                    "fingerprint": "mock-fingerprint",
                },
                "out_of_control_action": 0,
                "exit_wayline_when_rc_lost": 0,
                "rth_altitude": 120,
                "rth_mode": 1,
                "wayline_precision_type": 0,
            },
            {DEVICE_STATE_UNSUPPORTED},
            "rejected",
        ),
    ]

    for method, data, allowed, status in invalid_cases:
        rc, payload = client.service(method, data, args.timeout, allowed, status)
        if rc != 0:
            return rc
        print(f"异常分支通过: {method}, result={result_code(payload)}, status={output_status(payload)}")
        time.sleep(args.step_delay)

    required_events = ["flighttask_ready", "flighttask_progress", "return_home_info"]
    missing_events = [method for method in required_events if not client.has_event(method)]
    if missing_events:
        print(f"missing expected events: {missing_events}", file=sys.stderr)
        return 30

    ignored_total = client.stale_total()
    if args.fail_on_stale and ignored_total:
        print(
            "strict stale replies observed: "
            f"stats={client.stats}, samples={client.stale_sample_summary()}",
            file=sys.stderr,
        )
        return 31

    print(
        "dock3 wayline management test ok "
        f"(flight_id={flight_id}, accepted={client.stats['accepted']}, "
        f"events={client.stats['events']}, ignored_mismatched={client.stats['ignored_mismatched']}, "
        f"ignored_legacy={client.stats['ignored_legacy']}, "
        f"ignored_late={client.stats['ignored_late']}, "
        f"ignored_duplicate={client.stats['ignored_duplicate']}, "
        f"ignored_unknown={client.unrelated_reply_total()}, "
        f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, "
        f"ignored_total={ignored_total})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test DJI Dock3 wayline management over MQTT")
    add_config_argument(parser)
    parser.add_argument("--host", default=None, help="MQTT broker host; default comes from config")
    parser.add_argument("--port", type=int, default=None, help="MQTT broker port; default comes from config")
    parser.add_argument("--sn", default=None, help="Gateway SN; default comes from config")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", default="", help="Use a fixed flight_id instead of an auto generated one")
    parser.add_argument("--wayline-file", default="", help="Local KMZ/KML/WPML/.waypoints file; default creates a mock KMZ")
    parser.add_argument("--expect-waypoint-count", type=int, default=3)
    parser.add_argument("--step-delay", type=float, default=0.1)
    parser.add_argument(
        "--use-flighttask-stop",
        action="store_true",
        help="Use official flighttask_stop for cleanup instead of flighttask_undo",
    )
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.expect_waypoint_count <= 0:
        parser.error("--expect-waypoint-count must be positive")
    if args.step_delay < 0:
        parser.error("--step-delay must be non-negative")

    temp_dir = None
    if not args.wayline_file:
        temp_dir = tempfile.TemporaryDirectory()
        mock_path = Path(temp_dir.name) / "dock3-wayline-management.kmz"
        write_mock_kmz(mock_path)
        args.wayline_file = str(mock_path)
        print(f"mock wayline kmz -> {mock_path}")

    try:
        client = WaylineManagementClient(
            args.host,
            args.port,
            args.sn,
            args.qos,
            quiet_stale=args.quiet_stale,
            client_id_prefix="edge_gateway_dock3_wayline_management",
            subscribe_events=True,
        )
        if not client.connect(args.timeout):
            print("timeout waiting for MQTT connection", file=sys.stderr)
            return 1
        try:
            return run_management_flow(client, args)
        finally:
            client.disconnect()
    finally:
        if temp_dir:
            temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
