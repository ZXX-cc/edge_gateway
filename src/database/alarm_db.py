from sqlalchemy import select, delete, update, desc
from sqlalchemy.orm import Session
from typing import List
from .models import Alarm


class AlarmDB:
    @staticmethod
    def create_alarm(session: Session, gateway_sn: str, alarm_id: str,
                     alarm_level: int, alarm_content: str, **kwargs) -> Alarm:
        alarm = Alarm(
            gateway_sn=gateway_sn,
            alarm_id=alarm_id,
            alarm_level=alarm_level,
            alarm_content=alarm_content,
            **kwargs
        )
        session.add(alarm)
        session.flush()
        return alarm

    @staticmethod
    def get_alarm(session: Session, alarm_id: str) -> Alarm | None:
        result = session.execute(
            select(Alarm).where(Alarm.alarm_id == alarm_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def get_alarms_by_gateway(session: Session, gateway_sn: str,
                              page: int = 1, page_size: int = 20) -> List[Alarm]:
        result = session.execute(
            select(Alarm)
            .where(Alarm.gateway_sn == gateway_sn)
            .order_by(desc(Alarm.create_time))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return result.scalars().all()

    @staticmethod
    def get_alarm_count(session: Session, gateway_sn: str) -> int:
        result = session.execute(
            select(Alarm).where(Alarm.gateway_sn == gateway_sn)
        )
        return len(result.scalars().all())

    @staticmethod
    def delete_alarms_by_ids(session: Session, alarm_ids: List[str]) -> int:
        result = session.execute(
            delete(Alarm).where(Alarm.alarm_id.in_(alarm_ids))
        )
        return result.rowcount

    @staticmethod
    def delete_all_alarms(session: Session, gateway_sn: str) -> int:
        result = session.execute(
            delete(Alarm).where(Alarm.gateway_sn == gateway_sn)
        )
        return result.rowcount

    @staticmethod
    def mark_alarm_read(session: Session, alarm_id: str) -> bool:
        result = session.execute(
            update(Alarm)
            .where(Alarm.alarm_id == alarm_id)
            .values(is_read=True)
        )
        return result.rowcount > 0

    @staticmethod
    def get_unread_alarms(session: Session, gateway_sn: str) -> List[Alarm]:
        result = session.execute(
            select(Alarm)
            .where(Alarm.gateway_sn == gateway_sn)
            .where(Alarm.is_read == False)
            .order_by(desc(Alarm.create_time))
        )
        return result.scalars().all()