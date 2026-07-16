#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 wayline / flighttask protocol."""

import argparse
import json
import math
import sys
import time
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, output_status, result_code
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED, DOCK_SYSTEM_ERROR, INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, resolve_mqtt_settings


DEFAULT_DRONE_SN = "DRONESIM001"
DEFAULT_SITL_LATITUDE = -35.363262
DEFAULT_SITL_LONGITUDE = 149.165237


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


def write_real_sitl_waypoints(
    path: Path,
    latitude: float = DEFAULT_SITL_LATITUDE,
    longitude: float = DEFAULT_SITL_LONGITUDE,
    bearing_deg: float | None = None,
):
    """Build an executable ArduPilot mission for --real-sitl --execute-real.

    A sequence of MAV_CMD_NAV_WAYPOINT items alone can enter AUTO but remain
    on the ground in Copter SITL.  The real-execute probe therefore starts
    with MAV_CMD_NAV_TAKEOFF (22), followed by two short nearby waypoints.
    Its origin is normally taken from the current drone OSD so a custom SITL
    launch location cannot turn a short acceptance route into a distant
    flight.  Keep this separate from the KMZ fixture so KMZ parsing/upload
    remains covered by the safe --real-sitl upload-only path.
    """
    if bearing_deg is None:
        first_latitude, first_longitude = _offset_location(latitude, longitude, north_meters=8, east_meters=8)
        second_latitude, second_longitude = _offset_location(latitude, longitude, north_meters=16, east_meters=16)
    else:
        first_latitude, first_longitude = _offset_location_by_bearing(latitude, longitude, bearing_deg, distance_meters=25)
        second_latitude, second_longitude = _offset_location_by_bearing(latitude, longitude, bearing_deg, distance_meters=50)
    path.write_text(
        "QGC WPL 110\n"
        f"0\t1\t3\t22\t0\t0\t0\t0\t{latitude:.7f}\t{longitude:.7f}\t15\t1\n"
        f"1\t0\t3\t16\t0\t0\t0\t0\t{first_latitude:.7f}\t{first_longitude:.7f}\t20\t1\n"
        f"2\t0\t3\t16\t0\t0\t0\t0\t{second_latitude:.7f}\t{second_longitude:.7f}\t25\t1\n",
        encoding="utf-8",
    )


def _offset_location(latitude: float, longitude: float, *, north_meters: float, east_meters: float) -> tuple[float, float]:
    """Return a nearby WGS84 coordinate without requiring a geo dependency."""
    latitude = float(latitude)
    longitude = float(longitude)
    meters_per_degree_lon = 111320.0 * max(0.1, abs(math.cos(math.radians(latitude))))
    return (
        latitude + float(north_meters) / 111132.0,
        longitude + float(east_meters) / meters_per_degree_lon,
    )


def _offset_location_by_bearing(
    latitude: float,
    longitude: float,
    bearing_deg: float,
    *,
    distance_meters: float,
) -> tuple[float, float]:
    bearing_rad = math.radians(float(bearing_deg) % 360.0)
    north_meters = math.cos(bearing_rad) * float(distance_meters)
    east_meters = math.sin(bearing_rad) * float(distance_meters)
    return _offset_location(latitude, longitude, north_meters=north_meters, east_meters=east_meters)


def _field_value(payload: Any, key: str) -> Any:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    value = data.get(key) if isinstance(data, dict) else None
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def _to_coordinate(value: Any, *, lower: float, upper: float) -> float | None:
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(coordinate) or not lower <= coordinate <= upper:
        return None
    return coordinate


def _candidate_drone_sns(primary: str) -> list[str]:
    candidates = [primary, DEFAULT_DRONE_SN, "DRONE-SIM-001"]
    return list(dict.fromkeys(sn for sn in candidates if sn))


def real_sitl_osd_topics(drone_sn: str) -> list[str]:
    return [f"thing/product/{sn}/osd" for sn in _candidate_drone_sns(drone_sn)]


def _latest_observed(client: MqttRequestClient, topics: Iterable[str]) -> tuple[str | None, Any | None]:
    for topic in topics:
        messages = client.observed_by_topic.get(topic, [])
        if messages:
            return topic, messages[-1]
    return None, None


def wait_for_drone_osd(client: MqttRequestClient, topics: Iterable[str], timeout: float) -> tuple[str | None, Any | None]:
    """Return an existing or newly observed drone OSD sample."""
    topics = list(topics)
    topic, payload = _latest_observed(client, topics)
    if payload is not None:
        return topic, payload

    counts = {topic: client.observed_count(topic) for topic in topics}
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        topic, payload = _latest_observed(client, topics)
        if topic is not None and client.observed_count(topic) > counts[topic]:
            return topic, payload
        remaining = deadline - time.monotonic()
        client.observed_signal.wait(max(0.05, min(0.5, remaining)))
        client.observed_signal.clear()
    return _latest_observed(client, topics)


def resolve_real_sitl_origin(client: MqttRequestClient, args) -> tuple[float, float, float | None, str]:
    """Prefer an explicitly supplied or current OSD origin for the probe route."""
    explicit_bearing = args.sitl_route_bearing
    if args.sitl_origin_lat is not None and args.sitl_origin_lon is not None:
        return float(args.sitl_origin_lat), float(args.sitl_origin_lon), explicit_bearing, "cli"

    topic, payload = wait_for_drone_osd(client, args._real_sitl_osd_topics, args.osd_wait)
    latitude = _to_coordinate(_field_value(payload, "latitude"), lower=-90.0, upper=90.0)
    longitude = _to_coordinate(_field_value(payload, "longitude"), lower=-180.0, upper=180.0)
    bearing = explicit_bearing
    if bearing is None:
        bearing = _to_coordinate(_field_value(payload, "attitude_head"), lower=0.0, upper=360.0)
    if bearing is None:
        bearing = _to_coordinate(_field_value(payload, "course"), lower=0.0, upper=360.0)
    if latitude is not None and longitude is not None and (latitude != 0.0 or longitude != 0.0):
        return latitude, longitude, bearing, f"osd:{topic}"

    return DEFAULT_SITL_LATITUDE, DEFAULT_SITL_LONGITUDE, explicit_bearing, "fallback"


def real_sitl_osd_snapshot(client: MqttRequestClient, args) -> dict[str, Any]:
    """Build a compact telemetry sample for real-SITL timeout diagnostics."""
    topic, payload = _latest_observed(client, args._real_sitl_osd_topics)
    if payload is None:
        return {"available": False, "topics": args._real_sitl_osd_topics}
    return {
        "available": True,
        "topic": topic,
        "latitude": _field_value(payload, "latitude"),
        "longitude": _field_value(payload, "longitude"),
        "height": _field_value(payload, "height"),
        "altitude": _field_value(payload, "altitude"),
        "horizontal_speed": _field_value(payload, "horizontal_speed"),
        "vertical_speed": _field_value(payload, "vertical_speed"),
        "mode_code": _field_value(payload, "mode_code"),
        "gear": _field_value(payload, "gear"),
        "battery": _field_value(payload, "battery"),
    }


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


def is_delegated_to_nest(payload) -> bool:
    summary = wayline_file_summary(payload)
    return summary.get("delegated_to") == "nest"


def is_real_sitl_upload_failure(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    msg = str(payload.get("msg", ""))
    return result_code(payload) == DOCK_SYSTEM_ERROR and "航线上传到飞控失败" in msg


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


def validate_real_sitl_progress(progress, flight_id, expected_file):
    """Verify that a progress reply still belongs to the prepared SITL mission."""
    progress_status = output_status(progress)
    if progress_status not in {"in_progress", "ok"}:
        break_point = progress.get("data", {}).get("output", {}).get("ext", {}).get("break_point", {})
        if progress_status == "failed" and isinstance(break_point, dict):
            print(
                "real SITL mission failed: "
                f"reason={break_point.get('reason')}, break_reason={break_point.get('break_reason')}",
                file=sys.stderr,
            )
        print(f"unexpected real SITL progress status: {progress_status}, payload={progress}", file=sys.stderr)
        return 18, progress_status, {}

    ext = progress.get("data", {}).get("output", {}).get("ext", {})
    if not isinstance(ext, dict) or ext.get("flight_id") != flight_id:
        print(
            "real SITL progress flight_id changed: "
            f"expected={flight_id}, actual={ext.get('flight_id') if isinstance(ext, dict) else None}",
            file=sys.stderr,
        )
        return 19, progress_status, ext if isinstance(ext, dict) else {}

    delegated_to_nest = expected_file.get("delegated_to") == "nest"
    expected_waypoint_count = expected_file.get("waypoint_count")
    current_file = ext.get("file") if isinstance(ext.get("file"), dict) else {}
    waypoint_count = ext.get("waypoint_count")
    if (
        not delegated_to_nest
        and (waypoint_count != expected_waypoint_count or current_file.get("source") != expected_file.get("source"))
    ):
        print(
            "real SITL progress task file changed: "
            f"expected_file={expected_file}, actual_file={current_file}, "
            f"expected_waypoint_count={expected_waypoint_count}, actual_waypoint_count={waypoint_count}",
            file=sys.stderr,
        )
        return 20, progress_status, ext

    waypoint_index = ext.get("current_waypoint_index")
    if delegated_to_nest and (not isinstance(waypoint_count, int) or waypoint_count <= 0):
        return 0, progress_status, ext
    if not isinstance(waypoint_index, int) or not isinstance(waypoint_count, int) or not (0 <= waypoint_index < waypoint_count):
        print(
            "real SITL progress contains invalid waypoint index: "
            f"index={waypoint_index}, waypoint_count={waypoint_count}, payload={progress}",
            file=sys.stderr,
        )
        return 21, progress_status, ext
    if progress_status == "ok" and waypoint_index != waypoint_count - 1:
        print(
            "completed real SITL mission did not finish at final waypoint: "
            f"index={waypoint_index}, waypoint_count={waypoint_count}",
            file=sys.stderr,
        )
        return 22, progress_status, ext
    return 0, progress_status, ext


def cleanup_real_sitl_mission(client, args, flight_id: str) -> int:
    """Cancel the isolated test mission so it cannot block the next run."""
    if args.leave_real_mission_running:
        print(f"real SITL mission left running by request (flight_id={flight_id})")
        return 0
    rc, _ = client.request(
        "flighttask_undo",
        {"flight_ids": [flight_id]},
        args.timeout,
        [0],
        "canceled",
    )
    if rc == 0:
        print(f"real SITL test mission cleaned up (flight_id={flight_id})")
    return rc


def wait_until_real_sitl_waypoint_reaches(client, args, flight_id: str, expected_file: dict, target_index: int):
    deadline = time.monotonic() + args.return_at_waypoint_timeout
    last_progress = None
    last_status = None
    last_ext = {}
    while time.monotonic() < deadline:
        rc, progress = client.request(
            "flighttask_progress",
            {"flight_id": flight_id},
            args.timeout,
            [0],
            None,
        )
        if rc != 0:
            return rc, progress, last_status, last_ext
        rc, progress_status, ext = validate_real_sitl_progress(progress, flight_id, expected_file)
        if rc != 0:
            return rc, progress, progress_status, ext
        last_progress = progress
        last_status = progress_status
        last_ext = ext
        current_index = ext.get("current_waypoint_index")
        print(
            "real SITL waypoint observation "
            f"(status={progress_status}, waypoint_index={current_index}, target={target_index})"
        )
        if isinstance(current_index, int) and current_index >= target_index:
            return 0, progress, progress_status, ext
        if progress_status == "ok":
            return 0, progress, progress_status, ext
        time.sleep(args.return_at_waypoint_poll_seconds)

    print(
        "timeout waiting target waypoint: "
        f"target={target_index}, last_status={last_status}, "
        f"last_waypoint={last_ext.get('current_waypoint_index')}",
        file=sys.stderr,
    )
    return 25, last_progress, last_status, last_ext


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
        [0, DOCK_SYSTEM_ERROR] if args.allow_real_sitl_upload_failure else [0],
        None if args.allow_real_sitl_upload_failure else "sent",
    )
    if rc != 0:
        return rc
    if is_delegated_to_nest(prepare_payload):
        print(
            "real SITL prepare delegated to nest "
            f"(flight_id={flight_id}, source={wayline_file_summary(prepare_payload).get('source')}); "
            "gateway did not parse/upload the wayline"
        )
    else:
        rc = assert_wayline_file_parsed(prepare_payload, args.expect_waypoint_count)
        if rc != 0:
            return rc
    if is_real_sitl_upload_failure(prepare_payload):
        if not args.allow_real_sitl_upload_failure:
            print(f"real SITL waypoint upload failed: {prepare_payload}", file=sys.stderr)
            return 4
        print(
            "real SITL waypoint upload reached gateway but FC mission handshake failed; "
            "accepted as full-chain environment evidence "
            f"(flight_id={flight_id}, result={DOCK_SYSTEM_ERROR}, msg={prepare_payload.get('msg')!r})"
        )
        return 0
    expected_file = wayline_file_summary(prepare_payload)

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

    # Keep this after successful upload/prepare so an invalid local file or a
    # failed MAVLink mission handshake never leaves the isolated SITL armed.
    if not args.skip_prearm_verify:
        rc, prearm_payload = client.request(
            "set_flight_mode",
            {
                # QLAND is intentionally requested here: the gateway must map
                # it to a mode that permits arming (normally QSTABILIZE).
                "mode": "QLAND",
                "arm": True,
                "timeout": args.arm_timeout,
                "prearm_timeout": args.prearm_timeout,
            },
            args.timeout,
            [0],
            None,
        )
        if rc != 0:
            cleanup_real_sitl_mission(client, args, flight_id)
            return rc
        prearm_data = prearm_payload.get("data", {}) if isinstance(prearm_payload, dict) else {}
        if prearm_data.get("armed") is not True:
            print(f"real SITL pre-arm did not confirm armed=true: {prearm_payload}", file=sys.stderr)
            cleanup_real_sitl_mission(client, args, flight_id)
            return 24
        print(
            "real SITL pre-arm verified "
            f"(requested=QLAND, applied={prearm_data.get('mode')}, armed={prearm_data.get('armed')})"
        )

    rc, _ = client.request(
        "flighttask_execute",
        {"flight_id": flight_id},
        args.timeout,
        [0],
        "in_progress",
    )
    if rc != 0:
        cleanup_real_sitl_mission(client, args, flight_id)
        return rc
    time.sleep(args.execute_observe_seconds)
    rc, progress = client.request(
        "flighttask_progress",
        {"flight_id": flight_id},
        args.timeout,
        [0],
        None,
    )
    if rc != 0:
        cleanup_real_sitl_mission(client, args, flight_id)
        return rc
    rc, progress_status, ext = validate_real_sitl_progress(progress, flight_id, expected_file)
    if rc != 0:
        cleanup_real_sitl_mission(client, args, flight_id)
        return rc

    if args.return_at_waypoint_index is not None:
        rc, progress, progress_status, ext = wait_until_real_sitl_waypoint_reaches(
            client,
            args,
            flight_id,
            expected_file,
            args.return_at_waypoint_index,
        )
        if rc != 0:
            cleanup_real_sitl_mission(client, args, flight_id)
            return rc
        if progress_status == "ok":
            print(
                "target waypoint return skipped because mission already completed "
                f"(flight_id={flight_id}, waypoint_index={ext.get('current_waypoint_index')})",
                file=sys.stderr,
            )
            return 26

        if args.return_mode == "flighttask_return":
            rc, _ = client.request(
                "flighttask_return",
                {"flight_id": flight_id, "return_type": args.return_type},
                args.timeout,
                [0],
                "in_progress",
            )
            return_action = f"flighttask_return return_type={args.return_type}"
        else:
            rc, _ = client.request(
                "set_flight_mode",
                {"flight_mode": args.return_mode, "timeout": args.arm_timeout},
                args.timeout,
                [0],
                None,
            )
            return_action = f"set_flight_mode {args.return_mode}"
        if rc != 0:
            cleanup_real_sitl_mission(client, args, flight_id)
            return rc
        print(
            "target waypoint return accepted "
            f"(flight_id={flight_id}, action={return_action}, "
            f"waypoint_index={ext.get('current_waypoint_index')})"
        )
        if args.land_after_return_seconds >= 0:
            time.sleep(args.land_after_return_seconds)
            rc, _ = client.request(
                "landing",
                {},
                args.timeout,
                [0],
                None,
            )
            if rc != 0:
                return rc
            print(
                "landing requested after return "
                f"(delay_seconds={args.land_after_return_seconds})"
            )
        return 0

    if args.require_completion:
        deadline = time.monotonic() + args.completion_timeout
        while progress_status != "ok" and time.monotonic() < deadline:
            time.sleep(min(args.completion_poll_seconds, max(0.0, deadline - time.monotonic())))
            rc, progress = client.request(
                "flighttask_progress",
                {"flight_id": flight_id},
                args.timeout,
                [0],
                None,
            )
            if rc != 0:
                cleanup_real_sitl_mission(client, args, flight_id)
                return rc
            rc, progress_status, ext = validate_real_sitl_progress(progress, flight_id, expected_file)
            if rc != 0:
                cleanup_real_sitl_mission(client, args, flight_id)
                return rc
            print(
                "real SITL completion observation "
                f"(status={progress_status}, waypoint_index={ext.get('current_waypoint_index')}, "
                f"mission_state={ext.get('wayline_mission_state')})"
            )
        if progress_status != "ok":
            print(
                "real SITL completion timeout: "
                f"timeout={args.completion_timeout}s, status={progress_status}, "
                f"waypoint_index={ext.get('current_waypoint_index')}",
                file=sys.stderr,
            )
            print(
                "real SITL completion telemetry: "
                f"{json.dumps(real_sitl_osd_snapshot(client, args), ensure_ascii=False, sort_keys=True)}",
                file=sys.stderr,
            )
            cleanup_real_sitl_mission(client, args, flight_id)
            return 23

    if progress_status != "ok":
        cleanup_rc = cleanup_real_sitl_mission(client, args, flight_id)
        if cleanup_rc != 0:
            return cleanup_rc

    print(
        "real SITL execute/progress verified "
        f"(flight_id={flight_id}, observe_seconds={args.execute_observe_seconds}, "
        f"status={progress_status}, waypoint_index={ext.get('current_waypoint_index')}, "
        f"mission_state={ext.get('wayline_mission_state')})"
    )
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
        "--allow-real-sitl-upload-failure",
        action="store_true",
        help=(
            "with --real-sitl upload-only mode, accept a parsed wayline plus explicit "
            "FC mission upload failure as environment evidence; standalone real-SITL "
            "acceptance should omit this flag to stay strict"
        ),
    )
    parser.add_argument(
        "--execute-real",
        action="store_true",
        help="with --real-sitl, also request flighttask_execute/AUTO; use only in an isolated SITL",
    )
    parser.add_argument(
        "--execute-observe-seconds",
        type=float,
        default=8.0,
        help="seconds to wait before reading flighttask_progress after --execute-real",
    )
    parser.add_argument(
        "--require-completion",
        action="store_true",
        help="with --execute-real, keep polling until the isolated SITL mission completes",
    )
    parser.add_argument(
        "--completion-timeout",
        type=float,
        default=90.0,
        help="maximum seconds to wait for --require-completion (default: 90)",
    )
    parser.add_argument(
        "--completion-poll-seconds",
        type=float,
        default=5.0,
        help="progress poll interval for --require-completion (default: 5)",
    )
    parser.add_argument("--drone-sn", default=DEFAULT_DRONE_SN, help="Drone OSD SN used for real-SITL diagnostics")
    parser.add_argument("--osd-wait", type=float, default=4.0, help="seconds to wait for initial drone OSD")
    parser.add_argument("--sitl-origin-lat", type=float, default=None, help="override generated real-SITL route origin latitude")
    parser.add_argument("--sitl-origin-lon", type=float, default=None, help="override generated real-SITL route origin longitude")
    parser.add_argument("--sitl-route-bearing", type=float, default=None, help="override generated short SITL route bearing in degrees clockwise from north; default uses current OSD attitude_head/course")
    parser.add_argument("--return-at-waypoint-index", type=int, default=None, help="with --execute-real, request return mode when current_waypoint_index reaches this value")
    parser.add_argument("--return-mode", choices=["QRTL", "RTL", "QLAND", "LAND", "flighttask_return"], default="QRTL", help="action for --return-at-waypoint-index; default directly sets QRTL, use flighttask_return for the original return_type flow")
    parser.add_argument("--return-type", type=int, choices=[0, 1], default=0, help="return_type for --return-at-waypoint-index: 0=return home, 1=land")
    parser.add_argument("--return-at-waypoint-timeout", type=float, default=180.0, help="maximum seconds to wait for --return-at-waypoint-index")
    parser.add_argument("--return-at-waypoint-poll-seconds", type=float, default=2.0, help="progress poll interval for --return-at-waypoint-index")
    parser.add_argument("--land-after-return-seconds", type=float, default=-1.0, help="after --return-at-waypoint-index return is accepted, wait this many seconds then request landing; negative disables")
    parser.add_argument("--skip-prearm-verify", action="store_true", help="skip the QLAND-to-armable-mode verification before real execution")
    parser.add_argument("--arm-timeout", type=float, default=10.0, help="arm confirmation timeout for real-SITL pre-arm verification")
    parser.add_argument("--prearm-timeout", type=float, default=30.0, help="pre-arm readiness wait for real-SITL verification")
    parser.add_argument(
        "--leave-real-mission-running",
        action="store_true",
        help="do not automatically undo an in-progress real-SITL test mission",
    )
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    if args.execute_real and not args.real_sitl:
        parser.error("--execute-real requires --real-sitl")
    if args.require_completion and not args.execute_real:
        parser.error("--require-completion requires --execute-real")
    if args.return_at_waypoint_index is not None and not args.execute_real:
        parser.error("--return-at-waypoint-index requires --execute-real")
    if (args.sitl_origin_lat is None) != (args.sitl_origin_lon is None):
        parser.error("--sitl-origin-lat and --sitl-origin-lon must be provided together")
    if args.execute_observe_seconds < 0:
        parser.error("--execute-observe-seconds must be non-negative")
    if args.completion_timeout <= 0:
        parser.error("--completion-timeout must be positive")
    if args.completion_poll_seconds <= 0:
        parser.error("--completion-poll-seconds must be positive")
    if args.return_at_waypoint_index is not None and args.return_at_waypoint_index < 0:
        parser.error("--return-at-waypoint-index must be non-negative")
    if args.return_at_waypoint_timeout <= 0:
        parser.error("--return-at-waypoint-timeout must be positive")
    if args.return_at_waypoint_poll_seconds <= 0:
        parser.error("--return-at-waypoint-poll-seconds must be positive")
    if args.osd_wait < 0:
        parser.error("--osd-wait must be non-negative")
    if args.arm_timeout <= 0 or args.prearm_timeout < 0:
        parser.error("--arm-timeout must be positive and --prearm-timeout must be non-negative")

    temp_dir = None
    needs_real_sitl_fixture = False
    if not args.wayline_file:
        temp_dir = tempfile.TemporaryDirectory()
        if args.real_sitl and args.execute_real:
            mock_path = Path(temp_dir.name) / "simulated-executable-wayline.waypoints"
            needs_real_sitl_fixture = True
        else:
            mock_path = Path(temp_dir.name) / "simulated-wayline.kmz"
            write_mock_kmz(mock_path)
            print(f"mock wayline kmz -> {mock_path}")
        args.wayline_file = str(mock_path)

    try:
        client = Dock3WaylineClient(
            args.host,
            args.port,
            args.sn,
            args.qos,
            quiet_stale=args.quiet_stale,
            client_id_prefix="edge_gateway_dock3_wayline",
            subscribe_events=True,
            observe_topics=real_sitl_osd_topics(args.drone_sn) if args.real_sitl and args.execute_real else None,
        )
        if not client.connect(args.timeout):
            print("timeout waiting for MQTT connection", file=sys.stderr)
            return 1
        try:
            args._real_sitl_osd_topics = real_sitl_osd_topics(args.drone_sn)
            if needs_real_sitl_fixture:
                latitude, longitude, bearing, origin_source = resolve_real_sitl_origin(client, args)
                write_real_sitl_waypoints(Path(args.wayline_file), latitude, longitude, bearing)
                print(
                    "mock executable SITL waypoints -> "
                    f"{args.wayline_file} (origin={latitude:.7f},{longitude:.7f}, "
                    f"bearing={bearing if bearing is not None else 'default-ne'}, source={origin_source})"
                )
            rc = run_real_sitl_prepare_check(client, args) if args.real_sitl else run_wayline_checks(client, args)
            if rc != 0:
                return rc
            ignored_total = client.ignored_total()
            if args.fail_on_stale and ignored_total:
                print(
                    "strict stale replies observed: "
                    f"stats={client.stats}, samples={client.stale_sample_summary()}",
                    file=sys.stderr,
                )
                return 9
            print(
                f"dock3 {'real SITL ' if args.real_sitl else ''}wayline test ok "
                f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
                f"ignored_mismatched={client.stats['ignored_mismatched']}, "
                f"ignored_legacy={client.stats['ignored_legacy']}, "
                f"ignored_late={client.stats['ignored_late']}, "
                f"ignored_duplicate={client.stats['ignored_duplicate']}, "
                f"ignored_unknown={client.unrelated_reply_total()}, "
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
