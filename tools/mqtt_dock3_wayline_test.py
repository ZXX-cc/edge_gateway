#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 wayline / flighttask protocol."""

import argparse
import sys
import time
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED, INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, resolve_mqtt_settings


def now_ms():
    return int(time.time() * 1000)


def write_mock_kmz(path: Path):
    kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <LineString>
        <coordinates>
          149.165237,-35.363262,50
          149.165537,-35.363562,60
          149.165837,-35.363862,70
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("wpmz/template.kml", kml)


def file_url(path: str) -> str:
    parsed = Path(path).expanduser().resolve()
    return parsed.as_uri() if parsed.exists() else path


def official_prepare_payload(flight_id, wayline_file, *, simulate=True):
    simulate_mission = {"is_enable": 1 if simulate else 0}
    if simulate:
        simulate_mission.update({
            "latitude": -35.363262,
            "longitude": 149.165237,
        })
    return {
        "flight_id": flight_id,
        "task_type": 0,
        "execute_time": now_ms() + 30_000,
        "file": {
            "url": file_url(wayline_file),
            "fingerprint": "simulated-fingerprint",
        },
        "ready_conditions": {},
        "executable_conditions": {
            "storage_capacity": 0,
        },
        "rth_altitude": 80,
        "out_of_control_action": 0,
        "exit_wayline_when_rc_lost": 0,
        "simulate_mission": simulate_mission,
    }


class Dock3WaylineClient(MqttRequestClient):
    def request(self, method, data, timeout, allowed_results=None, expected_status=None):
        return super().request(
            method,
            data,
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else set(allowed_results),
            expected_status=expected_status,
        )

    def ignored_total(self):
        return self.stale_total()


def wayline_file_summary(payload):
    try:
        return payload["data"]["output"]["ext"]["file"]
    except (KeyError, TypeError):
        return {}


def assert_wayline_file_parsed(payload, min_waypoints):
    summary = wayline_file_summary(payload)
    if not summary.get("parsed"):
        print(f"wayline file not parsed: {summary}", file=sys.stderr)
        return 10
    waypoint_count = summary.get("waypoint_count")
    if not isinstance(waypoint_count, int) or waypoint_count < min_waypoints:
        print(
            f"wayline waypoint_count too small: expected>={min_waypoints}, actual={waypoint_count}",
            file=sys.stderr,
        )
        return 11
    print(
        "wayline file parsed: "
        f"format={summary.get('format')}, waypoint_count={waypoint_count}, files={summary.get('files')}"
    )
    return 0


def run_wayline_checks(client, args):
    flight_id = args.flight_id or f"dock3-wayline-{int(time.time())}"
    rc, prepare_payload = client.request(
        "flighttask_prepare",
        official_prepare_payload(flight_id, args.wayline_file),
        args.timeout,
        [0],
        "sent",
    )
    if rc != 0:
        return rc
    rc = assert_wayline_file_parsed(prepare_payload, args.expect_waypoint_count)
    if rc != 0:
        return rc

    checks = [
        ("flighttask_execute", {"flight_id": flight_id}, [0], "in_progress"),
        ("flighttask_progress", {"flight_id": flight_id}, [0], "in_progress"),
        ("flighttask_pause", {}, [0], "paused"),
        ("flighttask_progress", {"flight_id": flight_id}, [0], "paused"),
        ("flighttask_resume", {}, [0], "in_progress"),
        ("flighttask_return", {"flight_id": flight_id, "return_type": 0}, [0], "in_progress"),
        ("return_home_cancel", {}, [0], "paused"),
        ("flighttask_undo", {"flight_ids": [flight_id]}, [0], "canceled"),
        (
            "flighttask_prepare",
            {"flight_id": "", "task_type": 99, "execute_time": 1},
            [INVALID_COMMAND_FORMAT],
            None,
        ),
        ("flighttask_pause", {}, [DEVICE_STATE_UNSUPPORTED], "rejected"),
    ]
    for method, data, allowed, status in checks:
        rc, payload = client.request(method, data, args.timeout, allowed, status)
        if rc != 0:
            return rc
        if method == "flighttask_progress" and payload:
            rc = assert_wayline_file_parsed(payload, args.expect_waypoint_count)
            if rc != 0:
                return rc
        time.sleep(args.step_delay)

    if not client.has_event("flighttask_ready"):
        print("expected flighttask_ready event was not observed", file=sys.stderr)
        return 5
    if not client.has_event("flighttask_progress"):
        print("expected flighttask_progress event was not observed", file=sys.stderr)
        return 6
    if not client.has_event("return_home_info"):
        print("expected return_home_info event was not observed", file=sys.stderr)
        return 7
    return 0


def run_real_sitl_prepare_check(client, args):
    """Verify the real MAVLink waypoint-upload path without starting a mission.

    ``flighttask_prepare`` waits for the flight controller's MISSION upload
    handshake.  Keeping execute opt-in makes this check safe for a shared SITL
    environment: it uploads and validates the mission but does not switch the
    vehicle to AUTO or request takeoff.
    """
    flight_id = args.flight_id or f"dock3-real-sitl-{int(time.time())}"
    rc, prepare_payload = client.request(
        "flighttask_prepare",
        official_prepare_payload(flight_id, args.wayline_file, simulate=False),
        args.timeout,
        [0],
        "sent",
    )
    if rc != 0:
        return rc
    rc = assert_wayline_file_parsed(prepare_payload, args.expect_waypoint_count)
    if rc != 0:
        return rc

    if not client.has_event("flighttask_ready"):
        print("expected flighttask_ready event was not observed", file=sys.stderr)
        return 17

    if not args.execute_real:
        print(
            "real SITL waypoint upload verified "
            f"(flight_id={flight_id}, waypoints={args.expect_waypoint_count}); "
            "AUTO/execute was not requested"
        )
        return 0

    rc, _ = client.request(
        "flighttask_execute",
        {"flight_id": flight_id},
        args.timeout,
        [0],
        "in_progress",
    )
    if rc != 0:
        return rc
    print(f"real SITL execute accepted (flight_id={flight_id})")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 wayline category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--wayline-file", default="")
    parser.add_argument("--expect-waypoint-count", type=int, default=3)
    parser.add_argument("--step-delay", type=float, default=0.1)
    parser.add_argument(
        "--real-sitl",
        action="store_true",
        help="verify actual MAVLink waypoint upload; does not execute or take off by default",
    )
    parser.add_argument(
        "--execute-real",
        action="store_true",
        help="with --real-sitl, also request flighttask_execute/AUTO; use only in an isolated SITL",
    )
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    if args.execute_real and not args.real_sitl:
        parser.error("--execute-real requires --real-sitl")

    temp_dir = None
    if not args.wayline_file:
        temp_dir = tempfile.TemporaryDirectory()
        mock_path = Path(temp_dir.name) / "simulated-wayline.kmz"
        write_mock_kmz(mock_path)
        args.wayline_file = str(mock_path)
        print(f"mock wayline kmz -> {args.wayline_file}")

    try:
        client = Dock3WaylineClient(
            args.host,
            args.port,
            args.sn,
            args.qos,
            quiet_stale=args.quiet_stale,
            client_id_prefix="edge_gateway_dock3_wayline",
            subscribe_events=True,
        )
        if not client.connect(args.timeout):
            print("timeout waiting for MQTT connection", file=sys.stderr)
            return 1
        try:
            rc = run_real_sitl_prepare_check(client, args) if args.real_sitl else run_wayline_checks(client, args)
            if rc != 0:
                return rc
            ignored_total = client.ignored_total()
            if args.fail_on_stale and ignored_total:
                print(f"stale replies observed: {client.stats}", file=sys.stderr)
                return 9
            print(
                f"dock3 {'real SITL ' if args.real_sitl else ''}wayline test ok "
                f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
                f"ignored_mismatched={client.stats['ignored_mismatched']}, "
                f"ignored_legacy={client.stats['ignored_legacy']}, "
                f"ignored_late={client.stats['ignored_late']}, "
                f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, "
                f"ignored_total={ignored_total})"
            )
            return 0
        finally:
            client.disconnect()
    finally:
        if temp_dir:
            temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
