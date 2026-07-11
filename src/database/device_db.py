from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .connection import is_mongo_session, mongo_model_from_document
from .models import DeviceBinding


class DeviceDB:
    @staticmethod
    async def get_device_binding(session: AsyncSession, gateway_sn: str) -> DeviceBinding | None:
        if is_mongo_session(session):
            document = await session.run(
                lambda database: database["device_binding"].find_one(
                    {"gateway_sn": gateway_sn}
                )
            )
            return mongo_model_from_document(DeviceBinding, document)
        result = await session.execute(
            select(DeviceBinding).where(DeviceBinding.gateway_sn == gateway_sn)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert_device_binding(session: AsyncSession, gateway_sn: str, **kwargs) -> DeviceBinding:
        if is_mongo_session(session):
            allowed = {column.name for column in DeviceBinding.__table__.columns}
            values = {key: value for key, value in kwargs.items() if key in allowed and key != "id"}
            values["gateway_sn"] = gateway_sn
            await session.run(
                lambda database: database["device_binding"].update_one(
                    {"gateway_sn": gateway_sn}, {"$set": values}, upsert=True
                )
            )
            return await DeviceDB.get_device_binding(session, gateway_sn)

        existing = await DeviceDB.get_device_binding(session, gateway_sn)
        if existing:
            for key, value in kwargs.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
        else:
            existing = DeviceBinding(gateway_sn=gateway_sn, **kwargs)
            session.add(existing)
        await session.flush()
        return existing

    @staticmethod
    async def update_binding_status(session: AsyncSession, gateway_sn: str, bound: bool) -> bool:
        if is_mongo_session(session):
            result = await session.run(
                lambda database: database["device_binding"].update_one(
                    {"gateway_sn": gateway_sn}, {"$set": {"is_bound": int(bool(bound))}}
                )
            )
            return result.matched_count > 0
        result = await session.execute(
            update(DeviceBinding)
            .where(DeviceBinding.gateway_sn == gateway_sn)
            .values(is_bound=bound)
        )
        return result.rowcount > 0

    @staticmethod
    async def delete_device_binding(session: AsyncSession, gateway_sn: str) -> bool:
        if is_mongo_session(session):
            result = await session.run(
                lambda database: database["device_binding"].delete_one(
                    {"gateway_sn": gateway_sn}
                )
            )
            return result.deleted_count > 0
        existing = await DeviceDB.get_device_binding(session, gateway_sn)
        if existing:
            await session.delete(existing)
            await session.flush()
            return True
        return False

    @staticmethod
    async def get_bound_workspace(session: AsyncSession, gateway_sn: str) -> dict | None:
        binding = await DeviceDB.get_device_binding(session, gateway_sn)
        if binding and binding.is_bound:
            return {
                "workspace_id": binding.workspace_id,
                "workspace_name": binding.workspace_name,
                "organization_id": binding.organization_id,
                "organization_name": binding.organization_name,
                "bind_time": binding.bind_time,
            }
        return None
