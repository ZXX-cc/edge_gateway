from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from .models import DeviceBinding


class DeviceDB:
    @staticmethod
    async def get_device_binding(session: AsyncSession, gateway_sn: str) -> DeviceBinding | None:
        result = await session.execute(
            select(DeviceBinding).where(DeviceBinding.gateway_sn == gateway_sn)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert_device_binding(session: AsyncSession, gateway_sn: str, **kwargs) -> DeviceBinding:
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
        result = await session.execute(
            update(DeviceBinding)
            .where(DeviceBinding.gateway_sn == gateway_sn)
            .values(is_bound=bound)
        )
        return result.rowcount > 0

    @staticmethod
    async def delete_device_binding(session: AsyncSession, gateway_sn: str) -> bool:
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
                'workspace_id': binding.workspace_id,
                'workspace_name': binding.workspace_name,
                'organization_id': binding.organization_id,
                'organization_name': binding.organization_name,
                'bind_time': binding.bind_time
            }
        return None
