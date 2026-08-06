import tempfile
import sys
import sqlite3
import types
import unittest
from importlib.util import find_spec
from pathlib import Path

if "aiosqlite" not in sys.modules and find_spec("aiosqlite") is None:
    aiosqlite_stub = types.ModuleType("aiosqlite")
    aiosqlite_stub.connect = None
    for name in (
        "DatabaseError",
        "Error",
        "IntegrityError",
        "NotSupportedError",
        "OperationalError",
        "ProgrammingError",
        "sqlite_version",
        "sqlite_version_info",
    ):
        setattr(aiosqlite_stub, name, getattr(sqlite3, name))
    sys.modules["aiosqlite"] = aiosqlite_stub

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from src.database.connection import Base
from src.database.mission_platform_snapshot_db import MissionPlatformSnapshotDB
from src.database.models import MissionPlatformSnapshot
from src.utils.mission_platform_snapshot import (
    flush_latest_mission_platform_snapshot,
    publish_platform_event_with_latest_snapshot,
)


def flighttask_event(flight_id: str, status: str = "in_progress") -> dict:
    return {
        "method": "flighttask_progress",
        "data": {
            "result": 0,
            "output": {
                "status": status,
                "progress": {"current_step": 23, "percent": 50},
                "ext": {"flight_id": flight_id, "current_waypoint_index": 2},
            },
        },
        "gateway": "GW001",
        "tid": "tid-1",
        "bid": "bid-1",
        "timestamp": 1,
    }


class FakeMqtt:
    def __init__(self, publish_results):
        self.connected = True
        self.publish_results = list(publish_results)
        self.events = []

    def publish_event(self, event):
        self.events.append(event)
        if self.publish_results:
            return self.publish_results.pop(0)
        return True


class FakeMission:
    def __init__(self, event):
        self.event = event

    def build_current_wayline_progress_event(self):
        return self.event


class MissionPlatformSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tempdir.name) / "mission-platform-snapshot.db"
        self.engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.tempdir.cleanup()

    def test_save_latest_keeps_only_one_snapshot(self):
        with self.Session() as session:
            MissionPlatformSnapshotDB.save_latest(
                session, "GW001", flighttask_event("flight-a", "paused"), source="mission"
            )
            session.commit()
            MissionPlatformSnapshotDB.save_latest(
                session, "GW001", flighttask_event("flight-b", "failed"), source="mission"
            )
            session.commit()

            count = session.execute(select(func.count()).select_from(MissionPlatformSnapshot)).scalar()
            snapshot = MissionPlatformSnapshotDB.get_latest(session, "GW001")

        self.assertEqual(count, 1)
        self.assertEqual(snapshot["flight_id"], "flight-b")
        self.assertEqual(snapshot["payload"]["data"]["output"]["status"], "failed")

    def test_gateway_saves_failed_publish_and_replays_then_clears(self):
        offline_event = flighttask_event("flight-offline", "paused")
        realtime_event = flighttask_event("flight-live", "in_progress")
        offline_mqtt = FakeMqtt([False])

        ok = publish_platform_event_with_latest_snapshot(
            offline_mqtt,
            offline_event,
            gateway_sn="GW001",
            source="mission_manager",
            session_factory=self.Session,
        )
        self.assertFalse(ok)
        with self.Session() as session:
            snapshot = MissionPlatformSnapshotDB.get_latest(session, "GW001")
        self.assertEqual(snapshot["flight_id"], "flight-offline")

        reconnect_mqtt = FakeMqtt([True, True])
        flush_latest_mission_platform_snapshot(
            reconnect_mqtt,
            FakeMission(realtime_event),
            gateway_sn="GW001",
            session_factory=self.Session,
        )

        with self.Session() as session:
            snapshot_after = MissionPlatformSnapshotDB.get_latest(session, "GW001")

        self.assertIsNone(snapshot_after)
        self.assertEqual(
            [event["data"]["output"]["ext"]["flight_id"] for event in reconnect_mqtt.events],
            [
                "flight-offline",
                "flight-live",
            ],
        )


if __name__ == "__main__":
    unittest.main()
