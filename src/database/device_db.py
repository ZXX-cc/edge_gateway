from sqlalchemy import select, update
from sqlalchemy.orm import Session
from .models import DeviceBinding


class DeviceDB:
    @staticmethod
    def get_device_binding(session: Session, gateway_sn: str) -> DeviceBinding | None:
        result = session.execute(
            select(DeviceBinding).where(DeviceBinding.gateway_sn == gateway_sn)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def upsert_device_binding(session: Session, gateway_sn: str, **kwargs) -> DeviceBinding:
        existing = DeviceDB.get_device_binding(session, gateway_sn)
        if existing:
            for key, value in kwargs.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
        else:
            existing = DeviceBinding(gateway_sn=gateway_sn, **kwargs)
            session.add(existing)
        session.flush()
        return existing

    @staticmethod
    def update_binding_status(session: Session, gateway_sn: str, bound: bool) -> bool:
        result = session.execute(
            update(DeviceBinding)
            .where(DeviceBinding.gateway_sn == gateway_sn)
            .values(is_bound=bound)
        )
        return result.rowcount > 0

    @staticmethod
    def delete_device_binding(session: Session, gateway_sn: str) -> bool:
        existing = DeviceDB.get_device_binding(session, gateway_sn)
        if existing:
            session.delete(existing)
            session.flush()
            return True
        return False

    @staticmethod
    def get_bound_workspace(session: Session, gateway_sn: str) -> dict | None:
        binding = DeviceDB.get_device_binding(session, gateway_sn)
        if binding and binding.is_bound:
            return {
                'workspace_id': binding.workspace_id,
                'workspace_name': binding.workspace_name,
                'organization_id': binding.organization_id,
                'organization_name': binding.organization_name,
                'bind_time': binding.bind_time
            }
        return None