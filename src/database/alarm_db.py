from sqlalchemy import select, delete, update, desc
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from .models import Alarm


class AlarmDB:
    @staticmethod
    async def create_alarm(session: AsyncSession, gateway_sn: str, alarm_id: str,
                           alarm_level: int, alarm_content: str, **kwargs) -> Alarm:
        alarm = Alarm(
            gateway_sn=gateway_sn,
            alarm_id=alarm_id,
            alarm_level=alarm_level,
            alarm_content=alarm_content,
            **kwargs
        )
        session.add(alarm)
        await session.flush()
        return alarm

    @staticmethod
    async def get_alarm(session: AsyncSession, alarm_id: str) -> Alarm | None:
        result = await session.execute(
            select(Alarm).where(Alarm.alarm_id == alarm_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_alarms_by_gateway(session: AsyncSession, gateway_sn: str,
                                    page: int = 1, page_size: int = 20) -> List[Alarm]:
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
        result = await session.execute(
            select(Alarm).where(Alarm.gateway_sn == gateway_sn)
        )
        return len(result.scalars().all())

    @staticmethod
    async def delete_alarms_by_ids(session: AsyncSession, alarm_ids: List[str]) -> int:
        result = await session.execute(
            delete(Alarm).where(Alarm.alarm_id.in_(alarm_ids))
        )
        return result.rowcount

    @staticmethod
    async def delete_all_alarms(session: AsyncSession, gateway_sn: str) -> int:
        result = await session.execute(
            delete(Alarm).where(Alarm.gateway_sn == gateway_sn)
        )
        return result.rowcount

    @staticmethod
    async def mark_alarm_read(session: AsyncSession, alarm_id: str) -> bool:
        result = await session.execute(
            update(Alarm)
            .where(Alarm.alarm_id == alarm_id)
            .values(is_read=True)
        )
        return result.rowcount > 0

    @staticmethod
    async def get_unread_alarms(session: AsyncSession, gateway_sn: str) -> List[Alarm]:
        result = await session.execute(
            select(Alarm)
            .where(Alarm.gateway_sn == gateway_sn)
            .where(Alarm.is_read == False)
            .order_by(desc(Alarm.create_time))
        )
        return result.scalars().all()
