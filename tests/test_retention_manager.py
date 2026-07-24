import json
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.database.connection import Base
from src.database.models import Alarm, MediaFile, OperationRecord, TelemetryData
from src.database.retention_manager import DataRetentionManager
from src.utils.local_history_logger import LocalHistoryLogger


class RetentionManagerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._temporary_dir = tempfile.TemporaryDirectory()
        database_path = Path(self._temporary_dir.name) / "retention-test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session_patch = patch(
            "src.database.retention_manager.get_async_session_local",
            return_value=self.session_factory,
        )
        self.session_patch.start()
        self.manager = DataRetentionManager()
        self.manager._telemetry_hours = 1
        self.manager._operation_days = 1
        self.manager._alarm_days = 1
        self.manager._media_uploaded_days = 1
        self.manager._local_history_flight_telemetry_days = 7
        self.manager._local_history_device_state_days = 7

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()
        self._temporary_dir.cleanup()

    async def test_manual_cleanup_returns_counts_and_preserves_active_records(self):
        marker = uuid.uuid4().hex
        now_seconds = int(time.time())
        now_milliseconds = now_seconds * 1000
        old_seconds = now_seconds - 2 * 86400
        old_milliseconds = old_seconds * 1000

        async with self.session_factory() as session:
            session.add_all([
                TelemetryData(device_sn=f"old-telemetry-{marker}", timestamp=old_seconds),
                TelemetryData(device_sn=f"fresh-telemetry-{marker}", timestamp=now_milliseconds),
                OperationRecord(
                    device_sn="RETENTION_TEST",
                    operation_id=f"old-operation-{marker}",
                    command="test",
                    status="completed",
                    start_time=old_milliseconds,
                    end_time=old_milliseconds,
                ),
                OperationRecord(
                    device_sn="RETENTION_TEST",
                    operation_id=f"running-operation-{marker}",
                    command="test",
                    status="running",
                    start_time=old_milliseconds,
                    end_time=None,
                ),
                Alarm(
                    gateway_sn="RETENTION_TEST",
                    alarm_id=f"old-alarm-{marker}",
                    alarm_level=1,
                    alarm_content="old",
                    create_time=old_seconds,
                ),
                MediaFile(
                    id=f"old-media-{marker}",
                    device_sn="RETENTION_TEST",
                    file_name="old.jpg",
                    file_path="/tmp/old.jpg",
                    file_size=1,
                    file_type="image",
                    upload_status="completed",
                    created_at=old_seconds,
                ),
                MediaFile(
                    id=f"pending-media-{marker}",
                    device_sn="RETENTION_TEST",
                    file_name="pending.jpg",
                    file_path="/tmp/pending.jpg",
                    file_size=1,
                    file_type="image",
                    upload_status="pending",
                    created_at=old_seconds,
                ),
            ])
            await session.commit()

        deleted = await self.manager.manual_cleanup()
        self.assertEqual(deleted["telemetry"], 1)
        self.assertEqual(deleted["operation"], 1)
        self.assertEqual(deleted["alarm"], 1)
        self.assertEqual(deleted["media_uploaded"], 1)
        self.assertEqual(deleted["flight_telemetry_history"], 0)
        self.assertEqual(deleted["device_state_history"], 0)
        self.assertEqual(self.manager.get_status()["last_cleanup_counts"], deleted)
        self.assertGreater(self.manager.get_status()["last_cleanup_time"], 0)

        async with self.session_factory() as session:
            operations = set((await session.execute(
                select(OperationRecord.operation_id).where(
                    OperationRecord.operation_id.in_([
                        f"old-operation-{marker}", f"running-operation-{marker}",
                    ])
                )
            )).scalars().all())
            media = set((await session.execute(
                select(MediaFile.id).where(
                    MediaFile.id.in_([f"old-media-{marker}", f"pending-media-{marker}"])
                )
            )).scalars().all())

        self.assertEqual(operations, {f"running-operation-{marker}"})
        self.assertEqual(media, {f"pending-media-{marker}"})

    async def test_manual_cleanup_removes_expired_local_history_files(self):
        history_root = Path(self._temporary_dir.name) / "history"
        telemetry_dir = history_root / "flight_telemetry"
        state_dir = history_root / "device_state"
        telemetry_dir.mkdir(parents=True)
        state_dir.mkdir(parents=True)

        old_day = datetime.now().date() - timedelta(days=8)
        fresh_day = datetime.now().date()
        old_telemetry = telemetry_dir / f"{old_day:%Y-%m-%d}.jsonl"
        fresh_telemetry = telemetry_dir / f"{fresh_day:%Y-%m-%d}.jsonl"
        old_state = state_dir / f"{old_day:%Y-%m-%d}.jsonl"
        fresh_state = state_dir / f"{fresh_day:%Y-%m-%d}.jsonl"

        for path in (old_telemetry, fresh_telemetry, old_state, fresh_state):
            path.write_text('{"ok":true}\n', encoding="utf-8")

        def config_get(key, default=None):
            values = {
                "local_history.enabled": True,
                "local_history.dir": str(history_root),
                "local_history.flight_telemetry_days": 7,
                "local_history.device_state_days": 7,
            }
            return values.get(key, default)

        with patch("src.utils.local_history_logger.Config.get", side_effect=config_get):
            deleted = await self.manager.manual_cleanup()

        self.assertEqual(deleted["flight_telemetry_history"], 1)
        self.assertEqual(deleted["device_state_history"], 1)
        self.assertFalse(old_telemetry.exists())
        self.assertFalse(old_state.exists())
        self.assertTrue(fresh_telemetry.exists())
        self.assertTrue(fresh_state.exists())

    async def test_local_history_logger_writes_jsonl_files(self):
        history_root = Path(self._temporary_dir.name) / "history"

        def config_get(key, default=None):
            values = {
                "local_history.enabled": True,
                "local_history.dir": str(history_root),
                "local_history.flight_telemetry_days": 7,
                "local_history.device_state_days": 7,
            }
            return values.get(key, default)

        with patch("src.utils.local_history_logger.Config.get", side_effect=config_get):
            history_logger = LocalHistoryLogger()
            history_logger.record_flight_telemetry(
                "DRONE001",
                {"latitude": 44.1, "raw_value": object()},
                source="aircraft_osd",
            )
            history_logger.record_device_state_change(
                "EDGEGW001",
                {"mode_code": 1, "state": "online"},
                source="telemetry_state",
            )

        today = datetime.now().strftime("%Y-%m-%d")
        telemetry_file = history_root / "flight_telemetry" / f"{today}.jsonl"
        state_file = history_root / "device_state" / f"{today}.jsonl"
        telemetry_record = json.loads(telemetry_file.read_text(encoding="utf-8").strip())
        state_record = json.loads(state_file.read_text(encoding="utf-8").strip())

        self.assertEqual(telemetry_record["device_sn"], "DRONE001")
        self.assertEqual(telemetry_record["source"], "aircraft_osd")
        self.assertEqual(telemetry_record["telemetry"]["latitude"], 44.1)
        self.assertIn("raw_value", telemetry_record["telemetry"])
        self.assertEqual(state_record["device_sn"], "EDGEGW001")
        self.assertEqual(state_record["source"], "telemetry_state")
        self.assertEqual(state_record["state"]["state"], "online")


if __name__ == "__main__":
    unittest.main()
