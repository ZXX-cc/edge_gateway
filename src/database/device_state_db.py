from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .connection import is_mongo_session, mongo_model_from_document
from .models import DeviceState


class DeviceStateDB:
    @staticmethod
    def get_device_state(session: Session, gateway_sn: str) -> DeviceState | None:
        if is_mongo_session(session):
            document = session.collection("device_state").find_one(
                {"gateway_sn": gateway_sn}
            )
            return mongo_model_from_document(DeviceState, document)
        result = session.execute(
            select(DeviceState).where(DeviceState.gateway_sn == gateway_sn)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def upsert_device_state(session: Session, gateway_sn: str, **kwargs) -> DeviceState:
        if is_mongo_session(session):
            allowed = {column.name for column in DeviceState.__table__.columns}
            values = {key: value for key, value in kwargs.items() if key in allowed and key != "id"}
            values.update({"gateway_sn": gateway_sn, "last_update": datetime.utcnow()})
            session.collection("device_state").update_one(
                {"gateway_sn": gateway_sn}, {"$set": values}, upsert=True
            )
            return DeviceStateDB.get_device_state(session, gateway_sn)

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
        if is_mongo_session(session):
            values = dict(kwargs)
            values["last_update"] = datetime.utcnow()
            result = session.collection("device_state").update_one(
                {"gateway_sn": gateway_sn}, {"$set": values}
            )
            return result.matched_count > 0
        result = session.execute(
            update(DeviceState)
            .where(DeviceState.gateway_sn == gateway_sn)
            .values(**kwargs)
        )
        return result.rowcount > 0
