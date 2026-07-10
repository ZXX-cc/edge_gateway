#!/usr/bin/env python3
"""Run an isolated data-retention and operation-record audit.

The audit always uses a newly created SQLite file.  It deliberately refuses an
existing database and the project's ``missions.db`` so that it cannot delete
records from a running gateway.  It verifies both legacy seconds and Dock3
milliseconds timestamps, then prints concise evidence suitable for Ubuntu
acceptance logs.
"""

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Audit short-cycle database retention in an isolated SQLite database"
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config" / "gateway.yaml"),
        help="gateway YAML used to load retention durations",
    )
    parser.add_argument(
        "--database",
        default=None,
        help="new audit SQLite path; an existing path is refused",
    )
    parser.add_argument(
        "--keep-database",
        action="store_true",
        help="keep the generated audit database for manual inspection",
    )
    return parser.parse_args()


def prepare_database_path(args):
    temporary_dir = None
    if args.database:
        database_path = Path(args.database).expanduser().resolve()
        project_database = (PROJECT_ROOT / "missions.db").resolve()
        if database_path == project_database:
            raise ValueError("refusing project missions.db; choose a new /tmp audit path")
        if database_path.exists():
            raise ValueError(f"refusing existing database: {database_path}")
        database_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        temporary_dir = Path(tempfile.mkdtemp(prefix="edge_gateway_retention_audit_"))
        database_path = temporary_dir / "retention_audit.db"
    return database_path, temporary_dir


def retention_age_seconds(value, unit_seconds, category):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid retention.{category}: {value!r}") from None
    if value <= 0:
        raise ValueError(
            f"retention.{category} must be greater than zero for this audit; got {value}"
        )
    return value * unit_seconds


async def run_audit():
    from sqlalchemy import select

    from src.database.connection import async_engine, get_async_session_local, init_database_async
    from src.database.models import Alarm, MediaFile, OperationRecord, TelemetryData
    from src.database.retention_manager import DataRetentionManager

    manager = DataRetentionManager()
    now_seconds = int(time.time())
    now_milliseconds = now_seconds * 1000
    telemetry_age = retention_age_seconds(manager._telemetry_hours, 3600, "telemetry_hours")
    operation_age = retention_age_seconds(manager._operation_days, 86400, "operation_days")
    alarm_age = retention_age_seconds(manager._alarm_days, 86400, "alarm_days")
    media_age = retention_age_seconds(manager._media_uploaded_days, 86400, "media_uploaded_days")
    marker = uuid.uuid4().hex[:12]

    identifiers = {
        "telemetry_old": f"retention-audit-old-{marker}",
        "telemetry_fresh": f"retention-audit-fresh-{marker}",
        "operation_old_ms": f"retention-audit-old-ms-{marker}",
        "operation_old_seconds": f"retention-audit-old-seconds-{marker}",
        "operation_running": f"retention-audit-running-{marker}",
        "operation_fresh": f"retention-audit-fresh-{marker}",
        "alarm_old": f"retention-audit-alarm-old-{marker}",
        "alarm_fresh": f"retention-audit-alarm-fresh-{marker}",
        "media_old": f"retention-audit-media-old-{marker}",
        "media_pending": f"retention-audit-media-pending-{marker}",
        "media_fresh": f"retention-audit-media-fresh-{marker}",
    }

    await init_database_async()
    AsyncSessionLocal = get_async_session_local()
    try:
        async with AsyncSessionLocal() as session:
            session.add_all([
                TelemetryData(
                    device_sn=identifiers["telemetry_old"],
                    timestamp=now_seconds - telemetry_age - 5,
                ),
                TelemetryData(
                    device_sn=identifiers["telemetry_fresh"],
                    timestamp=now_milliseconds,
                ),
                OperationRecord(
                    device_sn="RETENTION_AUDIT",
                    operation_id=identifiers["operation_old_ms"],
                    command="retention_audit",
                    status="completed",
                    progress=100,
                    start_time=now_milliseconds - (operation_age + 10) * 1000,
                    end_time=now_milliseconds - (operation_age + 5) * 1000,
                ),
                OperationRecord(
                    device_sn="RETENTION_AUDIT",
                    operation_id=identifiers["operation_old_seconds"],
                    command="retention_audit",
                    status="failed",
                    progress=0,
                    start_time=now_seconds - operation_age - 10,
                    end_time=now_seconds - operation_age - 5,
                ),
                OperationRecord(
                    device_sn="RETENTION_AUDIT",
                    operation_id=identifiers["operation_running"],
                    command="retention_audit",
                    status="running",
                    progress=50,
                    start_time=now_milliseconds - (operation_age + 10) * 1000,
                    end_time=None,
                ),
                OperationRecord(
                    device_sn="RETENTION_AUDIT",
                    operation_id=identifiers["operation_fresh"],
                    command="retention_audit",
                    status="completed",
                    progress=100,
                    start_time=now_milliseconds,
                    end_time=now_milliseconds,
                ),
                Alarm(
                    gateway_sn="RETENTION_AUDIT",
                    alarm_id=identifiers["alarm_old"],
                    alarm_level=1,
                    alarm_content="retention audit expired alarm",
                    create_time=now_seconds - alarm_age - 5,
                ),
                Alarm(
                    gateway_sn="RETENTION_AUDIT",
                    alarm_id=identifiers["alarm_fresh"],
                    alarm_level=1,
                    alarm_content="retention audit fresh alarm",
                    create_time=now_milliseconds,
                ),
                MediaFile(
                    id=identifiers["media_old"],
                    device_sn="RETENTION_AUDIT",
                    file_name="retention-audit-old.jpg",
                    file_path="/tmp/retention-audit-old.jpg",
                    file_size=1,
                    file_type="image",
                    upload_status="completed",
                    created_at=now_seconds - media_age - 5,
                ),
                MediaFile(
                    id=identifiers["media_pending"],
                    device_sn="RETENTION_AUDIT",
                    file_name="retention-audit-pending.jpg",
                    file_path="/tmp/retention-audit-pending.jpg",
                    file_size=1,
                    file_type="image",
                    upload_status="pending",
                    created_at=now_seconds - media_age - 5,
                ),
                MediaFile(
                    id=identifiers["media_fresh"],
                    device_sn="RETENTION_AUDIT",
                    file_name="retention-audit-fresh.jpg",
                    file_path="/tmp/retention-audit-fresh.jpg",
                    file_size=1,
                    file_type="image",
                    upload_status="completed",
                    created_at=now_milliseconds,
                ),
            ])
            await session.commit()

        deleted_counts = await manager.manual_cleanup()
        expected_counts = {
            "telemetry": 1,
            "operation": 2,
            "alarm": 1,
            "media_uploaded": 1,
        }
        if deleted_counts != expected_counts:
            raise RuntimeError(
                f"unexpected cleanup counts: expected={expected_counts}, actual={deleted_counts}"
            )

        async with AsyncSessionLocal() as session:
            remaining_telemetry = set((await session.execute(
                select(TelemetryData.device_sn).where(
                    TelemetryData.device_sn.in_([
                        identifiers["telemetry_old"], identifiers["telemetry_fresh"],
                    ])
                )
            )).scalars().all())
            remaining_operations = set((await session.execute(
                select(OperationRecord.operation_id).where(
                    OperationRecord.operation_id.in_([
                        identifiers["operation_old_ms"],
                        identifiers["operation_old_seconds"],
                        identifiers["operation_running"],
                        identifiers["operation_fresh"],
                    ])
                )
            )).scalars().all())
            remaining_alarms = set((await session.execute(
                select(Alarm.alarm_id).where(
                    Alarm.alarm_id.in_([identifiers["alarm_old"], identifiers["alarm_fresh"]])
                )
            )).scalars().all())
            remaining_media = set((await session.execute(
                select(MediaFile.id).where(
                    MediaFile.id.in_([
                        identifiers["media_old"],
                        identifiers["media_pending"],
                        identifiers["media_fresh"],
                    ])
                )
            )).scalars().all())

        expected_remaining = {
            "telemetry": {identifiers["telemetry_fresh"]},
            "operation": {identifiers["operation_running"], identifiers["operation_fresh"]},
            "alarm": {identifiers["alarm_fresh"]},
            "media": {identifiers["media_pending"], identifiers["media_fresh"]},
        }
        actual_remaining = {
            "telemetry": remaining_telemetry,
            "operation": remaining_operations,
            "alarm": remaining_alarms,
            "media": remaining_media,
        }
        if actual_remaining != expected_remaining:
            raise RuntimeError(
                f"retention protection check failed: expected={expected_remaining}, "
                f"actual={actual_remaining}"
            )

        status = manager.get_status()
        if status["last_cleanup_counts"] != expected_counts or not status["last_cleanup_time"]:
            raise RuntimeError(f"retention cleanup status not updated: {status}")

        return {
            "deleted_counts": deleted_counts,
            "preserved": {key: len(value) for key, value in expected_remaining.items()},
            "retention": {
                "telemetry_hours": manager._telemetry_hours,
                "operation_days": manager._operation_days,
                "alarm_days": manager._alarm_days,
                "media_uploaded_days": manager._media_uploaded_days,
            },
        }
    finally:
        await async_engine.dispose()


def main():
    args = parse_args()
    try:
        database_path, temporary_dir = prepare_database_path(args)
        from src.utils.config import Config

        Config().load(args.config)
        os.environ["DATABASE_URL"] = f"sqlite:///{database_path}"
        os.environ["ASYNC_DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
        print(f"retention audit database -> {database_path}")
        result = asyncio.run(run_audit())
        print(
            "database retention audit ok "
            f"(deleted={result['deleted_counts']}, preserved={result['preserved']}, "
            f"retention={result['retention']})"
        )
        if args.keep_database:
            print(f"retention audit database kept -> {database_path}")
        elif temporary_dir:
            shutil.rmtree(temporary_dir, ignore_errors=True)
        elif database_path.exists():
            database_path.unlink()
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"database retention audit failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
