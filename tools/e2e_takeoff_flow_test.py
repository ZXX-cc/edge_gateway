#!/usr/bin/env python3
"""Offline one-key launch flow regression for Dock3 wayline missions."""

from __future__ import annotations

import argparse
import sys
import time
import types
import tempfile
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def install_offline_stubs() -> None:
    """Keep this offline tool independent from MAVLink and database services."""
    if "pymavlink" not in sys.modules:
        pymavlink = types.ModuleType("pymavlink")
        pymavlink.mavutil = types.SimpleNamespace()
        pymavlink.mavutil.mavlink = types.SimpleNamespace(MAV_CMD_DO_CHANGE_SPEED=178)
        sys.modules["pymavlink"] = pymavlink
        sys.modules["pymavlink.mavutil"] = pymavlink.mavutil

    if "src.database.connection" not in sys.modules:
        connection = types.ModuleType("src.database.connection")

        async def init_database_async():
            return None

        def get_async_session_local():
            return None

        connection.init_database_async = init_database_async
        connection.get_async_session_local = get_async_session_local
        sys.modules["src.database.connection"] = connection

    if "src.database.mission_db" not in sys.modules:
        mission_db = types.ModuleType("src.database.mission_db")
        mission_db.MissionDB = types.SimpleNamespace()
        sys.modules["src.database.mission_db"] = mission_db

    if "src.database.waypoint_db" not in sys.modules:
        waypoint_db = types.ModuleType("src.database.waypoint_db")
        waypoint_db.WaypointDB = types.SimpleNamespace()
        sys.modules["src.database.waypoint_db"] = waypoint_db

    if "src.database.schemas" not in sys.modules:
        schemas = types.ModuleType("src.database.schemas")

        class _Schema:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        schemas.MissionCreate = _Schema
        schemas.WaypointCreate = _Schema
        sys.modules["src.database.schemas"] = schemas


install_offline_stubs()

from src.mission.manager import MissionManager  # noqa: E402
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT  # noqa: E402


def now_ms() -> int:
    return int(time.time() * 1000)


def build_msg(method: str, data: dict) -> dict:
    return {
        "tid": f"tid-{method}-{now_ms()}",
        "bid": f"bid-{method}-{now_ms()}",
        "gateway": "EDGEGW001",
        "method": method,
        "timestamp": now_ms(),
        "data": data,
    }


def write_mock_kmz(path: Path) -> None:
    kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.2">
  <Document>
    <wpml:waylineCoordinateSysParam>
      <wpml:coordinateMode>WGS84</wpml:coordinateMode>
      <wpml:heightMode>relativeToStartPoint</wpml:heightMode>
    </wpml:waylineCoordinateSysParam>
    <wpml:takeOffRefPoint>-35.363000,149.165000,584.0</wpml:takeOffRefPoint>
    <wpml:takeOffRefPointAGLHeight>584.0</wpml:takeOffRefPointAGLHeight>
  </Document>
</kml>
"""
    wpml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.2">
  <Document>
    <wpml:executeHeightMode>relativeToStartPoint</wpml:executeHeightMode>
    <wpml:waylineAvoidLimitAreaMode>0</wpml:waylineAvoidLimitAreaMode>
    <Folder>
      <wpml:templateId>0</wpml:templateId>
      <wpml:waylineId>0</wpml:waylineId>
      <Placemark>
        <Point><coordinates>149.165237,-35.363262</coordinates></Point>
        <wpml:index>0</wpml:index>
        <wpml:executeHeight>120</wpml:executeHeight>
      </Placemark>
      <Placemark>
        <Point><coordinates>149.165537,-35.363562</coordinates></Point>
        <wpml:index>1</wpml:index>
        <wpml:executeHeight>140</wpml:executeHeight>
      </Placemark>
      <Placemark>
        <Point><coordinates>149.165837,-35.363862</coordinates></Point>
        <wpml:index>2</wpml:index>
        <wpml:executeHeight>160</wpml:executeHeight>
      </Placemark>
    </Folder>
  </Document>
</kml>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("wpmz/template.kml", kml)
        archive.writestr("wpmz/waylines.wpml", wpml)


class OfflineNest:
    def __init__(self):
        self.calls: list[str] = []
        self.environment_data = {
            "nest_heartbeat": True,
            "plc_heartbeat": True,
            "drone_heartbeat": True,
            "estop_state": False,
            "fault_state": False,
            "system_busy": False,
            "emergency_landing": False,
            "takeoff_allowed": True,
            "gate_state": 1,
            "wind_speed": 1.0,
            "rainfall": 0.0,
            "error_code": 0,
            "last_update": time.time(),
        }

    def get_environment_data(self) -> dict:
        return self.environment_data

    def request_takeoff(self) -> None:
        self.calls.append("request_takeoff")

    def abort_takeoff(self) -> None:
        self.calls.append("abort_takeoff")

    def abort_landing(self) -> None:
        self.calls.append("abort_landing")


class OfflineTelemetry:
    def __init__(self):
        self.data = {
            "connection_status": "connected",
            "batteryRemaining": 88,
            "satelliteNumber": 16,
            "gps_signal_level": 5,
            "gps_fixed": True,
            "armed": False,
            "flight_mode": "GUIDED",
        }

    def get_telemetry_data(self) -> dict:
        return self.data


class OfflineFlightControl:
    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []

    def set_mode(self, mode: str) -> dict:
        self.calls.append(("set_mode", mode))
        return {"success": True, "message": f"set mode {mode}"}

    def one_click_return(self) -> dict:
        self.calls.append(("one_click_return", None))
        return {"success": True, "message": "return home"}

    def land(self) -> dict:
        self.calls.append(("land", None))
        return {"success": True, "message": "land"}


def prepare_payload(flight_id: str, kmz_path: Path) -> dict:
    return {
        "flight_id": flight_id,
        "task_type": 0,
        "execute_time": now_ms() + 30_000,
        "file": {
            "url": kmz_path.as_uri(),
            "fingerprint": "offline-fingerprint",
        },
        "ready_conditions": {
            "battery_capacity": 30,
        },
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


def status_of(reply: dict) -> str:
    return reply.get("data", {}).get("output", {}).get("status", "")


def msg_of(reply: dict) -> str:
    return str(reply.get("msg", ""))


def is_simulate_mission_disabled_reply(reply: dict) -> bool:
    return (
        reply.get("data", {}).get("result") == INVALID_COMMAND_FORMAT
        and "simulate_mission is disabled" in msg_of(reply)
    )


def event_statuses(events: list[dict], method: str) -> list[str]:
    statuses = []
    for event in events:
        if event.get("method") == method:
            statuses.append(event.get("data", {}).get("output", {}).get("status", ""))
    return statuses


def run_takeoff_flow(args) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        kmz_path = tmp_path / "one-key-launch.kmz"
        write_mock_kmz(kmz_path)

        events: list[dict] = []
        nest = OfflineNest()
        flight_control = OfflineFlightControl()
        manager = MissionManager()
        manager.set_event_callback(events.append)
        manager.set_nest_module(nest)
        manager.set_telemetry_module(OfflineTelemetry())
        manager.set_flight_control(flight_control)
        manager._breakpoint_file_path = lambda: str(tmp_path / "breakpoint.json")
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        flight_id = args.flight_id or f"takeoff-flow-{now_ms()}"
        prepare = manager.handle_flighttask_prepare(
            build_msg("flighttask_prepare", prepare_payload(flight_id, kmz_path))
        )
        print(f"prepare result={prepare.get('data', {}).get('result')} status={status_of(prepare)}")
        if prepare.get("data", {}).get("result") != 0 or status_of(prepare) != "sent":
            if is_simulate_mission_disabled_reply(prepare) and not args.fail_on_simulate_disabled:
                print(
                    "takeoff-flow simulation guard ok "
                    "(simulate_mission is disabled; real flight execution is required)"
                )
                return 0
            print(f"flighttask_prepare failed: {prepare}", file=sys.stderr)
            return 3

        file_summary = prepare.get("data", {}).get("output", {}).get("ext", {}).get("file", {})
        waypoint_count = file_summary.get("waypoint_count")
        if not file_summary.get("parsed") or waypoint_count < args.expect_waypoint_count:
            print(f"wayline file not parsed as expected: {file_summary}", file=sys.stderr)
            return 4

        execute = manager.handle_flighttask_execute(
            build_msg("flighttask_execute", {"flight_id": flight_id})
        )
        print(f"execute result={execute.get('data', {}).get('result')} status={status_of(execute)}")
        if execute.get("data", {}).get("result") != 0 or status_of(execute) != "in_progress":
            print(f"flighttask_execute failed: {execute}", file=sys.stderr)
            return 5
        if nest.calls != ["request_takeoff"]:
            print(f"unexpected nest calls after execute: {nest.calls}", file=sys.stderr)
            return 6
        if flight_control.calls:
            print(f"simulation flow should not touch real flight control: {flight_control.calls}", file=sys.stderr)
            return 7

        if not manager.complete_simulated_mission():
            print("complete_simulated_mission returned false", file=sys.stderr)
            return 8
        if manager._status != manager.MISSION_COMPLETED:
            print(f"mission status not completed: {manager._status}", file=sys.stderr)
            return 9

        progress_statuses = event_statuses(events, "flighttask_progress")
        print(f"progress statuses={progress_statuses}")
        for expected in ("sent", "in_progress", "ok"):
            if expected not in progress_statuses:
                print(f"missing flighttask_progress status: {expected}", file=sys.stderr)
                return 10
        if not any(event.get("method") == "flighttask_ready" for event in events):
            print("missing flighttask_ready event", file=sys.stderr)
            return 11

        if args.fail_on_stale:
            print("takeoff-flow is offline; stale MQTT replies are not applicable")

        print(
            "dock3 takeoff flow test ok "
            f"(flight_id={flight_id}, waypoints={waypoint_count}, "
            f"events={len(events)}, nest_calls={','.join(nest.calls)})"
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline Dock3 one-key launch flow regression")
    parser.add_argument("--config", default="config/gateway.yaml")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--expect-waypoint-count", type=int, default=3)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument(
        "--fail-on-simulate-disabled",
        action="store_true",
        help=(
            "Treat the expected real-only rejection of simulate_mission as a failure. "
            "By default the tool records that the simulation guard is active and exits 0."
        ),
    )
    args = parser.parse_args()
    return run_takeoff_flow(args)


if __name__ == "__main__":
    raise SystemExit(main())
