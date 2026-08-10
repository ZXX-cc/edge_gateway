import json
import logging
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.database.connection import Base
from src.database.models import Alarm, MediaFile, OperationRecord, TelemetryData
from src.database.retention_manager import DataRetentionManager
from src.utils.local_history_logger import LocalHistoryLogger
from src.utils.log_retention import cleanup_log_files
from src.utils.logger import DailyMonthlyFileHandler


ZERO_LOG_COUNTS = {
    "log_deleted": 0,
    "log_compressed": 0,
    "log_disk_pressure_deleted": 0,
    "log_disk_pressure_freed_bytes": 0,
}


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
        self.log_cleanup_patch = patch(
            "src.database.retention_manager.cleanup_log_files",
            return_value=dict(ZERO_LOG_COUNTS),
        )
        self.log_cleanup_mock = self.log_cleanup_patch.start()
        self.manager = DataRetentionManager()
        self.manager._telemetry_hours = 1
        self.manager._operation_days = 1
        self.manager._alarm_days = 1
        self.manager._media_uploaded_days = 1
        self.manager._local_history_flight_telemetry_days = 7
        self.manager._local_history_device_state_days = 7

    async def asyncTearDown(self):
        self.log_cleanup_patch.stop()
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
        self.assertEqual(deleted["flight_telemetry_history_compressed"], 0)
        self.assertEqual(deleted["device_state_history_compressed"], 0)
        self.assertEqual(deleted["log_deleted"], 0)
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
        telemetry_dir = history_root / "flight_telemetry" / f"{datetime.now():%Y-%m}"
        state_dir = history_root / "device_state" / f"{datetime.now():%Y-%m}"
        telemetry_dir.mkdir(parents=True)
        state_dir.mkdir(parents=True)

        old_day = datetime.now().date() - timedelta(days=8)
        compress_day = datetime.now().date() - timedelta(days=2)
        fresh_day = datetime.now().date()
        old_telemetry = telemetry_dir / f"{old_day:%Y-%m-%d}_00.jsonl"
        compress_telemetry = telemetry_dir / f"{compress_day:%Y-%m-%d}_00.jsonl"
        fresh_telemetry = telemetry_dir / f"{fresh_day:%Y-%m-%d}_00.jsonl"
        old_state = state_dir / f"{old_day:%Y-%m-%d}.jsonl"
        compress_state = state_dir / f"{compress_day:%Y-%m-%d}.jsonl"
        fresh_state = state_dir / f"{fresh_day:%Y-%m-%d}.jsonl"

        for path in (old_telemetry, compress_telemetry, fresh_telemetry, old_state, compress_state, fresh_state):
            path.write_text('{"ok":true}\n', encoding="utf-8")

        def config_get(key, default=None):
            values = {
                "local_history.enabled": True,
                "local_history.dir": str(history_root),
                "local_history.flight_telemetry_days": 7,
                "local_history.device_state_days": 7,
                "local_history.flight_telemetry_granularity": "hour",
                "local_history.device_state_granularity": "day",
                "local_history.compress_after_days": 1,
            }
            return values.get(key, default)

        with patch("src.utils.local_history_logger.Config.get", side_effect=config_get):
            deleted = await self.manager.manual_cleanup()

        self.assertEqual(deleted["flight_telemetry_history"], 1)
        self.assertEqual(deleted["device_state_history"], 1)
        self.assertEqual(deleted["flight_telemetry_history_compressed"], 1)
        self.assertEqual(deleted["device_state_history_compressed"], 1)
        self.assertFalse(old_telemetry.exists())
        self.assertFalse(old_state.exists())
        self.assertFalse(compress_telemetry.exists())
        self.assertFalse(compress_state.exists())
        self.assertTrue(compress_telemetry.with_name(compress_telemetry.name + ".gz").exists())
        self.assertTrue(compress_state.with_name(compress_state.name + ".gz").exists())
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
                "local_history.flight_telemetry_granularity": "hour",
                "local_history.device_state_granularity": "day",
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

        now = datetime.now()
        telemetry_file = history_root / "flight_telemetry" / f"{now:%Y-%m}" / f"{now:%Y-%m-%d_%H}.jsonl"
        state_file = history_root / "device_state" / f"{now:%Y-%m}" / f"{now:%Y-%m-%d}.jsonl"
        telemetry_record = json.loads(telemetry_file.read_text(encoding="utf-8").strip())
        state_record = json.loads(state_file.read_text(encoding="utf-8").strip())

        self.assertEqual(telemetry_record["device_sn"], "DRONE001")
        self.assertEqual(telemetry_record["source"], "aircraft_osd")
        self.assertEqual(telemetry_record["telemetry"]["latitude"], 44.1)
        self.assertIn("raw_value", telemetry_record["telemetry"])
        self.assertEqual(state_record["device_sn"], "EDGEGW001")
        self.assertEqual(state_record["source"], "telemetry_state")
        self.assertEqual(state_record["state"]["state"], "online")

    async def test_disk_pressure_triggers_log_and_history_cleanup(self):
        events = []
        self.manager.set_event_callback(events.append)
        self.manager._disk_cleanup_cooldown_seconds = 0
        self.manager._disk_high_watermark_percent = 80
        self.manager._disk_target_percent = 75
        self.log_cleanup_mock.return_value = {
            "log_deleted": 1,
            "log_compressed": 0,
            "log_disk_pressure_deleted": 1,
            "log_disk_pressure_freed_bytes": 1024,
        }

        with patch("src.database.retention_manager.Config.get", return_value=self._temporary_dir.name), \
                patch(
                    "src.database.retention_manager.shutil.disk_usage",
                    return_value=SimpleNamespace(total=100, used=85, free=15),
                ), \
                patch.object(
                    self.manager,
                    "_clean_local_history_for_disk_pressure",
                    return_value={"history_disk_pressure_deleted": 1, "history_disk_pressure_freed_bytes": 2048},
                ) as history_cleanup:
            await self.manager._check_disk_pressure()

        self.assertEqual(self.manager._last_disk_usage_percent, 85)
        self.assertGreater(self.manager._last_disk_cleanup_time, 0)
        self.assertEqual(self.log_cleanup_mock.call_count, 1)
        history_cleanup.assert_called_once()
        self.assertEqual(events[0]["type"], "disk_cleanup")
        self.assertEqual(events[0]["data"]["used_percent"], 85)

    async def test_cleanup_log_files_uses_category_retention_and_compresses_old_files(self):
        log_root = Path(self._temporary_dir.name) / "logs"
        today = datetime.now().date()
        debug_old = log_root / "mqtt_trace" / f"{today:%Y-%m}" / f"mqtt_trace_{today - timedelta(days=4):%Y-%m-%d}.log"
        normal_old = log_root / "mission" / f"{today:%Y-%m}" / f"mission_{today - timedelta(days=8):%Y-%m-%d}.log"
        important_recent = log_root / "important" / f"{today:%Y-%m}" / f"important_{today - timedelta(days=20):%Y-%m-%d}.log"
        important_old = log_root / "important" / f"{today:%Y-%m}" / f"important_{today - timedelta(days=31):%Y-%m-%d}.log"
        normal_compress = log_root / "payload" / f"{today:%Y-%m}" / f"payload_{today - timedelta(days=2):%Y-%m-%d}.log"
        for path in (debug_old, normal_old, important_recent, important_old, normal_compress):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("log\n", encoding="utf-8")

        counts = cleanup_log_files({
            "dir": str(log_root),
            "retention": {
                "enabled": True,
                "normal_days": 7,
                "important_days": 30,
                "debug_days": 3,
                "compress_after_days": 1,
                "disk_cleanup_enabled": False,
            },
        })

        self.assertEqual(counts["log_deleted"], 3)
        self.assertEqual(counts["log_compressed"], 2)
        self.assertFalse(debug_old.exists())
        self.assertFalse(normal_old.exists())
        self.assertFalse(important_old.exists())
        self.assertTrue(important_recent.with_name(important_recent.name + ".gz").exists())
        self.assertTrue(normal_compress.with_name(normal_compress.name + ".gz").exists())

    async def test_daily_monthly_file_handler_rotates_when_file_is_too_large(self):
        log_root = Path(self._temporary_dir.name) / "rotating_logs"
        handler = DailyMonthlyFileHandler(str(log_root), "rotation", max_bytes=40)
        handler.setFormatter(logging.Formatter("%(message)s"))
        record_one = logging.LogRecord("rotation-test", logging.INFO, __file__, 1, "x" * 30, None, None)
        record_two = logging.LogRecord("rotation-test", logging.INFO, __file__, 2, "y" * 30, None, None)

        try:
            handler.emit(record_one)
            handler.emit(record_two)
        finally:
            handler.close()

        month_dir = log_root / f"{datetime.fromtimestamp(record_one.created):%Y-%m}"
        base_file = month_dir / f"rotation_{datetime.fromtimestamp(record_one.created):%Y-%m-%d}.log"
        rotated_file = month_dir / f"rotation_{datetime.fromtimestamp(record_one.created):%Y-%m-%d}_001.log"
        self.assertTrue(base_file.exists())
        self.assertTrue(rotated_file.exists())
        self.assertIn("x" * 30, base_file.read_text(encoding="utf-8"))
        self.assertIn("y" * 30, rotated_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
