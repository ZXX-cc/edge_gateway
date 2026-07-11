#!/usr/bin/env python3
"""Ubuntu MongoDB 运行时数据层审计；写入数据使用隔离标识并自动清理。"""
import argparse
import asyncio
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database.alarm_db import AlarmDB
from src.database.connection import (
    SessionLocal,
    close_database,
    configure_database,
    get_async_session_local,
    get_database_backend,
    get_mongo_database,
    init_database,
    is_mongo_session,
)
from src.database.device_db import DeviceDB
from src.database.device_state_db import DeviceStateDB
from src.database.media_db import MediaDB
from src.database.mission_db import MissionDB
from src.database.operation_db import OperationDB
from src.database.schemas import MissionCreate, WaypointCreate
from src.database.telemetry_db import TelemetryDB
from src.database.waypoint_db import WaypointDB
from src.utils.config import Config, load_config


EXPECTED_COLLECTIONS = {
    "missions",
    "waypoints",
    "device_state",
    "device_binding",
    "alarms",
    "telemetry_data",
    "operation_record",
    "media_files",
}

REQUIRED_INDEX_FIELDS = {
    "missions": {("id",), ("name", "created_at"), ("status", "created_at")},
    "waypoints": {("mission_id", "sequence")},
    "device_state": {("gateway_sn",)},
    "device_binding": {("gateway_sn",)},
    "alarms": {("alarm_id",), ("gateway_sn", "create_time")},
    "telemetry_data": {("device_sn", "timestamp")},
    "operation_record": {("operation_id",), ("device_sn", "start_time")},
    "media_files": {("id",), ("device_sn", "upload_status", "created_at")},
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/gateway.yaml")
    parser.add_argument(
        "--write-check",
        action="store_true",
        help="执行全部运行时仓储读写检查；审计数据会在 finally 中删除",
    )
    return parser.parse_args()


def audit_sync_storage(audit_id: str):
    device_sn = f"__mongo_audit_telemetry_{audit_id}"
    gateway_sn = f"__mongo_audit_state_{audit_id}"
    with SessionLocal() as session:
        if not is_mongo_session(session):
            raise RuntimeError("SessionLocal did not select MongoDB")
        TelemetryDB.save_telemetry_sync(
            session,
            device_sn,
            {
                "timestamp": 1_800_000_000_000,
                "latitude": 30.0,
                "longitude": 120.0,
                "battery": {"capacity_percent": 99},
                "position_state": {"gps_number": 15, "is_fixed": 1},
            },
        )
        latest = TelemetryDB.get_latest_telemetry_sync(session, device_sn)
        if latest is None or latest.battery_capacity != 99:
            raise RuntimeError("MongoDB telemetry readback mismatch")
        DeviceStateDB.upsert_device_state(session, gateway_sn, cover_state=2)
        state = DeviceStateDB.get_device_state(session, gateway_sn)
        if state is None or state.cover_state != 2:
            raise RuntimeError("MongoDB device state readback mismatch")
        session.commit()


async def audit_async_storage(audit_id: str):
    gateway_sn = f"__mongo_audit_gateway_{audit_id}"
    operation_id = f"__mongo_audit_operation_{audit_id}"
    alarm_id = f"__mongo_audit_alarm_{audit_id}"
    media_name = f"__mongo_audit_media_{audit_id}.jpg"
    session_factory = get_async_session_local()
    async with session_factory() as session:
        if not is_mongo_session(session):
            raise RuntimeError("AsyncSessionLocal did not select MongoDB")

        mission = await MissionDB.create_mission(
            session,
            MissionCreate(
                name=f"__mongo_audit_mission_{audit_id}",
                device_id=str(uuid.uuid4()),
                status="uploaded",
                execution_conditions={"audit": True},
            ),
        )
        await WaypointDB.batch_create_waypoints_for_mission(
            session,
            mission.id,
            [
                WaypointCreate(
                    mission_id=mission.id,
                    sequence=0,
                    command=16,
                    frame=3,
                    latitude=30.0,
                    longitude=120.0,
                    altitude=50.0,
                )
            ],
        )
        loaded_mission = await MissionDB.get_mission(session, uuid.UUID(mission.id))
        loaded_waypoints = await WaypointDB.get_waypoints_by_mission(
            session, uuid.UUID(mission.id)
        )
        if loaded_mission is None or len(loaded_waypoints) != 1:
            raise RuntimeError("MongoDB mission/waypoint readback mismatch")

        await OperationDB.create_operation_async(
            session, gateway_sn, "mongodb_audit", {"audit": True}, operation_id=operation_id
        )
        await OperationDB.complete_operation_async(
            session, operation_id, {"audit": "ok"}
        )
        operation = await OperationDB.get_operation_async(session, operation_id)
        if operation is None or operation.status != "completed":
            raise RuntimeError("MongoDB operation readback mismatch")

        await AlarmDB.create_alarm(
            session, gateway_sn, alarm_id, 0, "MongoDB runtime audit"
        )
        alarm = await AlarmDB.get_alarm(session, alarm_id)
        if alarm is None:
            raise RuntimeError("MongoDB alarm readback mismatch")

        await DeviceDB.upsert_device_binding(
            session,
            gateway_sn,
            workspace_id="audit",
            organization_id="audit",
            is_bound=1,
        )
        binding = await DeviceDB.get_device_binding(session, gateway_sn)
        if binding is None or not binding.is_bound:
            raise RuntimeError("MongoDB device binding readback mismatch")

        media = await MediaDB.create_media_file(
            session,
            {
                "mission_id": mission.id,
                "device_sn": gateway_sn,
                "file_name": media_name,
                "file_path": f"/tmp/{media_name}",
                "file_size": 1,
                "file_type": "photo",
            },
        )
        loaded_media = await MediaDB.get_media_by_id(session, media.id)
        if loaded_media is None:
            raise RuntimeError("MongoDB media readback mismatch")
        await session.commit()


def cleanup_audit_data(audit_id: str):
    database = get_mongo_database()
    mission_ids = [
        document.get("id")
        for document in database["missions"].find(
            {"name": f"__mongo_audit_mission_{audit_id}"}, {"id": 1}
        )
        if document.get("id")
    ]
    if mission_ids:
        database["waypoints"].delete_many({"mission_id": {"$in": mission_ids}})
    database["missions"].delete_many({"name": f"__mongo_audit_mission_{audit_id}"})
    database["telemetry_data"].delete_many({"device_sn": f"__mongo_audit_telemetry_{audit_id}"})
    database["device_state"].delete_many({"gateway_sn": f"__mongo_audit_state_{audit_id}"})
    database["operation_record"].delete_many({"operation_id": f"__mongo_audit_operation_{audit_id}"})
    database["alarms"].delete_many({"alarm_id": f"__mongo_audit_alarm_{audit_id}"})
    database["device_binding"].delete_many({"gateway_sn": f"__mongo_audit_gateway_{audit_id}"})
    database["media_files"].delete_many({"file_name": f"__mongo_audit_media_{audit_id}.jpg"})


def main() -> int:
    args = parse_args()
    load_config(args.config)
    configure_database(Config.get_database_config())
    if get_database_backend() != "mongodb":
        print("MongoDB audit requires database.backend=mongodb", file=sys.stderr)
        return 2

    audit_id = uuid.uuid4().hex
    try:
        init_database()
        database = get_mongo_database()
        database.command("ping")
        collections = set(database.list_collection_names())
        missing = sorted(EXPECTED_COLLECTIONS - collections)
        if missing:
            print(f"MongoDB missing collections after initialization: {missing}", file=sys.stderr)
            return 3
        missing_indexes = {}
        for collection_name, required_fields in REQUIRED_INDEX_FIELDS.items():
            index_info = database[collection_name].index_information()
            actual_fields = {
                tuple(field for field, _direction in details.get("key", []))
                for details in index_info.values()
            }
            missing_fields = sorted(required_fields - actual_fields)
            if missing_fields:
                missing_indexes[collection_name] = missing_fields
        if missing_indexes:
            print(f"MongoDB missing indexes: {missing_indexes}", file=sys.stderr)
            return 4
        print(
            f"MONGODB_CONNECTION_OK database={database.name} "
            f"collections={len(collections)}"
        )
        print(f"MONGODB_INDEX_OK collections={len(REQUIRED_INDEX_FIELDS)}")

        if args.write_check:
            audit_sync_storage(audit_id)
            asyncio.run(audit_async_storage(audit_id))
            print("MONGODB_RUNTIME_WRITE_READ_OK domains=8")

        print(
            f"MONGODB_RUNTIME_AUDIT_OK backend={get_database_backend()} "
            f"write_check={args.write_check}"
        )
        return 0
    finally:
        if args.write_check:
            try:
                cleanup_audit_data(audit_id)
            except Exception as error:
                print(f"MongoDB audit cleanup warning: {error}", file=sys.stderr)
        close_database()


if __name__ == "__main__":
    raise SystemExit(main())
