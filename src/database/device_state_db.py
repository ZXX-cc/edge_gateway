from sqlalchemy import select, update, insert
from sqlalchemy.orm import Session
from .models import DeviceState


class DeviceStateDB:
    @staticmethod
    def get_device_state(session: Session, gateway_sn: str) -> DeviceState | None:
        result = session.execute(
            select(DeviceState).where(DeviceState.gateway_sn == gateway_sn)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def upsert_device_state(session: Session, gateway_sn: str, **kwargs) -> DeviceState:
        existing = DeviceStateDB.get_device_state(session, gateway_sn)
        if existing:
            for key, value in kwargs.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
        else:
            existing = DeviceState(gateway_sn=gateway_sn, **kwargs)
            session.add(existing)
        session.flush()
        return existing

    @staticmethod
    def update_device_state(session: Session, gateway_sn: str, **kwargs) -> bool:
        result = session.execute(
            update(DeviceState)
            .where(DeviceState.gateway_sn == gateway_sn)
            .values(**kwargs)
        )
        return result.rowcount > 0