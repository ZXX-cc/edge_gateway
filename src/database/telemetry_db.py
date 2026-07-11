from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from .connection import (
    is_mongo_session,
    mongo_expired_filter,
    mongo_model_from_document,
)
from .models import TelemetryData
from .time_utils import expired_timestamp_condition

MAX_LOCAL_TELEMETRY = 3600


def _telemetry_values(device_sn: str, osd_data: dict) -> dict:
    battery = osd_data.get("battery", {})
    position_state = osd_data.get("position_state", {})
    return {
        "device_sn": device_sn,
        "timestamp": osd_data.get("timestamp", int(osd_data.get("timestamp_ms", 0))),
        "latitude": osd_data.get("latitude"),
        "longitude": osd_data.get("longitude"),
        "altitude": osd_data.get("altitude"),
        "height": osd_data.get("height"),
        "horizontal_speed": osd_data.get("horizontal_speed"),
        "vertical_speed": osd_data.get("vertical_speed"),
        "attitude_pitch": osd_data.get("attitude_pitch"),
        "attitude_roll": osd_data.get("attitude_roll"),
        "attitude_head": osd_data.get("attitude_head"),
        "battery_capacity": battery.get("capacity_percent"),
        "battery_voltage": battery.get("voltage"),
        "battery_current": battery.get("current"),
        "battery_temperature": battery.get("temperature"),
        "gps_number": position_state.get("gps_number"),
        "gps_fixed": position_state.get("is_fixed"),
        "wind_speed": osd_data.get("wind_speed"),
        "wind_direction": osd_data.get("wind_direction"),
        "mode_code": osd_data.get("mode_code"),
        "gear": osd_data.get("gear"),
        "flight_time": osd_data.get("flight_time"),
    }


class TelemetryDB:
    @staticmethod
    async def save_telemetry_async(session: AsyncSession, device_sn: str, osd_data: dict):
        values = _telemetry_values(device_sn, osd_data)
        telemetry = TelemetryData(**values)
        if is_mongo_session(session):
            await session.run(
                lambda database: database["telemetry_data"].insert_one(dict(values))
            )
            await TelemetryDB._trim_telemetry_buffer(session, device_sn)
            return telemetry
        session.add(telemetry)
        await session.flush()
        await TelemetryDB._trim_telemetry_buffer(session, device_sn)
        return telemetry

    @staticmethod
    def save_telemetry_sync(session: Session, device_sn: str, osd_data: dict):
        values = _telemetry_values(device_sn, osd_data)
        telemetry = TelemetryData(**values)
        if is_mongo_session(session):
            session.collection("telemetry_data").insert_one(dict(values))
            TelemetryDB._trim_telemetry_buffer_sync(session, device_sn)
            return telemetry
        session.add(telemetry)
        session.flush()
        TelemetryDB._trim_telemetry_buffer_sync(session, device_sn)
        return telemetry

    @staticmethod
    async def _trim_telemetry_buffer(session: AsyncSession, device_sn: str):
        if is_mongo_session(session):
            def trim(database):
                collection = database["telemetry_data"]
                excess = collection.count_documents({"device_sn": device_sn}) - MAX_LOCAL_TELEMETRY
                if excess <= 0:
                    return
                ids = [
                    item["_id"]
                    for item in collection.find(
                        {"device_sn": device_sn}, {"_id": 1}
                    ).sort([("timestamp", 1), ("_id", 1)]).limit(excess)
                ]
                if ids:
                    collection.delete_many({"_id": {"$in": ids}})
            await session.run(trim)
            return
        result = await session.execute(
            select(TelemetryData.id)
            .where(TelemetryData.device_sn == device_sn)
            .order_by(TelemetryData.id.asc())
        )
        all_ids = [row[0] for row in result.all()]
        if len(all_ids) > MAX_LOCAL_TELEMETRY:
            ids_to_delete = all_ids[: len(all_ids) - MAX_LOCAL_TELEMETRY]
            await session.execute(delete(TelemetryData).where(TelemetryData.id.in_(ids_to_delete)))

    @staticmethod
    def _trim_telemetry_buffer_sync(session: Session, device_sn: str):
        if is_mongo_session(session):
            collection = session.collection("telemetry_data")
            excess = collection.count_documents({"device_sn": device_sn}) - MAX_LOCAL_TELEMETRY
            if excess <= 0:
                return
            ids = [
                item["_id"]
                for item in collection.find({"device_sn": device_sn}, {"_id": 1})
                .sort([("timestamp", 1), ("_id", 1)])
                .limit(excess)
            ]
            if ids:
                collection.delete_many({"_id": {"$in": ids}})
            return
        result = session.execute(
            select(TelemetryData.id)
            .where(TelemetryData.device_sn == device_sn)
            .order_by(TelemetryData.id.asc())
        )
        all_ids = [row[0] for row in result.all()]
        if len(all_ids) > MAX_LOCAL_TELEMETRY:
            ids_to_delete = all_ids[: len(all_ids) - MAX_LOCAL_TELEMETRY]
            session.execute(delete(TelemetryData).where(TelemetryData.id.in_(ids_to_delete)))

    @staticmethod
    async def get_latest_telemetry_async(session: AsyncSession, device_sn: str) -> TelemetryData | None:
        if is_mongo_session(session):
            document = await session.run(
                lambda database: database["telemetry_data"].find_one(
                    {"device_sn": device_sn}, sort=[("timestamp", -1), ("_id", -1)]
                )
            )
            return mongo_model_from_document(TelemetryData, document)
        result = await session.execute(
            select(TelemetryData)
            .where(TelemetryData.device_sn == device_sn)
            .order_by(TelemetryData.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def get_latest_telemetry_sync(session: Session, device_sn: str) -> TelemetryData | None:
        if is_mongo_session(session):
            document = session.collection("telemetry_data").find_one(
                {"device_sn": device_sn}, sort=[("timestamp", -1), ("_id", -1)]
            )
            return mongo_model_from_document(TelemetryData, document)
        result = session.execute(
            select(TelemetryData)
            .where(TelemetryData.device_sn == device_sn)
            .order_by(TelemetryData.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_telemetry_history_async(session: AsyncSession, device_sn: str, limit: int = 100):
        if is_mongo_session(session):
            documents = await session.run(
                lambda database: list(
                    database["telemetry_data"]
                    .find({"device_sn": device_sn})
                    .sort([("timestamp", -1), ("_id", -1)])
                    .limit(limit)
                )
            )
            return [mongo_model_from_document(TelemetryData, item) for item in documents]
        result = await session.execute(
            select(TelemetryData)
            .where(TelemetryData.device_sn == device_sn)
            .order_by(TelemetryData.id.desc())
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def clear_old_data_async(session: AsyncSession, device_sn: str, hours_to_keep: int = 2):
        if is_mongo_session(session):
            query = {"device_sn": device_sn, **mongo_expired_filter("timestamp", hours_to_keep * 3600)}
            result = await session.run(
                lambda database: database["telemetry_data"].delete_many(query)
            )
            return result.deleted_count
        result = await session.execute(
            delete(TelemetryData)
            .where(TelemetryData.device_sn == device_sn)
            .where(expired_timestamp_condition(TelemetryData.timestamp, hours_to_keep * 3600))
        )
        return result.rowcount
