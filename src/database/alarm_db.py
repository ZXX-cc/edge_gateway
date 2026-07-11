import time
from typing import List

from sqlalchemy import delete, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .connection import is_mongo_session, mongo_model_from_document
from .models import Alarm


class AlarmDB:
    @staticmethod
    async def create_alarm(session: AsyncSession, gateway_sn: str, alarm_id: str,
                           alarm_level: int, alarm_content: str, **kwargs) -> Alarm:
        values = {
            "gateway_sn": gateway_sn,
            "alarm_id": alarm_id,
            "alarm_level": alarm_level,
            "alarm_content": alarm_content,
            "alarm_type": kwargs.get("alarm_type", "hms"),
            "is_read": kwargs.get("is_read", 0),
            "create_time": kwargs.get("create_time", int(time.time())),
            "extra_info": kwargs.get("extra_info"),
        }
        alarm = Alarm(**values)
        if is_mongo_session(session):
            await session.run(
                lambda database: database["alarms"].insert_one(
                    {"_id": alarm_id, **values}
                )
            )
            return alarm
        session.add(alarm)
        await session.flush()
        return alarm

    @staticmethod
    async def get_alarm(session: AsyncSession, alarm_id: str) -> Alarm | None:
        if is_mongo_session(session):
            document = await session.run(
                lambda database: database["alarms"].find_one({"alarm_id": alarm_id})
            )
            return mongo_model_from_document(Alarm, document)
        result = await session.execute(select(Alarm).where(Alarm.alarm_id == alarm_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_alarms_by_gateway(session: AsyncSession, gateway_sn: str,
                                    page: int = 1, page_size: int = 20) -> List[Alarm]:
        if is_mongo_session(session):
            documents = await session.run(
                lambda database: list(
                    database["alarms"]
                    .find({"gateway_sn": gateway_sn})
                    .sort("create_time", -1)
                    .skip((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return [mongo_model_from_document(Alarm, item) for item in documents]
        result = await session.execute(
            select(Alarm)
            .where(Alarm.gateway_sn == gateway_sn)
            .order_by(desc(Alarm.create_time))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return result.scalars().all()

    @staticmethod
    async def get_alarm_count(session: AsyncSession, gateway_sn: str) -> int:
        if is_mongo_session(session):
            return await session.run(
                lambda database: database["alarms"].count_documents(
                    {"gateway_sn": gateway_sn}
                )
            )
        result = await session.execute(select(Alarm).where(Alarm.gateway_sn == gateway_sn))
        return len(result.scalars().all())

    @staticmethod
    async def delete_alarms_by_ids(session: AsyncSession, alarm_ids: List[str]) -> int:
        if is_mongo_session(session):
            result = await session.run(
                lambda database: database["alarms"].delete_many(
                    {"alarm_id": {"$in": alarm_ids}}
                )
            )
            return result.deleted_count
        result = await session.execute(delete(Alarm).where(Alarm.alarm_id.in_(alarm_ids)))
        return result.rowcount

    @staticmethod
    async def delete_all_alarms(session: AsyncSession, gateway_sn: str) -> int:
        if is_mongo_session(session):
            result = await session.run(
                lambda database: database["alarms"].delete_many(
                    {"gateway_sn": gateway_sn}
                )
            )
            return result.deleted_count
        result = await session.execute(delete(Alarm).where(Alarm.gateway_sn == gateway_sn))
        return result.rowcount

    @staticmethod
    async def mark_alarm_read(session: AsyncSession, alarm_id: str) -> bool:
        if is_mongo_session(session):
            result = await session.run(
                lambda database: database["alarms"].update_one(
                    {"alarm_id": alarm_id}, {"$set": {"is_read": 1}}
                )
            )
            return result.matched_count > 0
        result = await session.execute(
            update(Alarm).where(Alarm.alarm_id == alarm_id).values(is_read=True)
        )
        return result.rowcount > 0

    @staticmethod
    async def get_unread_alarms(session: AsyncSession, gateway_sn: str) -> List[Alarm]:
        if is_mongo_session(session):
            documents = await session.run(
                lambda database: list(
                    database["alarms"]
                    .find({"gateway_sn": gateway_sn, "is_read": {"$in": [0, False]}})
                    .sort("create_time", -1)
                )
            )
            return [mongo_model_from_document(Alarm, item) for item in documents]
        result = await session.execute(
            select(Alarm)
            .where(Alarm.gateway_sn == gateway_sn)
            .where(Alarm.is_read == False)
            .order_by(desc(Alarm.create_time))
        )
        return result.scalars().all()
