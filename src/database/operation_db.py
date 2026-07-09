import uuid
import time
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from .models import OperationRecord


class OperationDB:
    @staticmethod
    async def create_operation_async(session: AsyncSession, device_sn: str, command: str,
                                      params: dict = None, operator: str = "",
                                      operation_id: str = None) -> str:
        operation_id = operation_id or str(uuid.uuid4())
        record = OperationRecord(
            device_sn=device_sn,
            operation_id=operation_id,
            command=command,
            status='running',
            progress=0,
            params=params,
            start_time=int(time.time() * 1000),
            operator=operator
        )
        session.add(record)
        await session.flush()
        return operation_id

    @staticmethod
    def create_operation_sync(session: Session, device_sn: str, command: str,
                               params: dict = None, operator: str = "",
                               operation_id: str = None) -> str:
        operation_id = operation_id or str(uuid.uuid4())
        record = OperationRecord(
            device_sn=device_sn,
            operation_id=operation_id,
            command=command,
            status='running',
            progress=0,
            params=params,
            start_time=int(time.time() * 1000),
            operator=operator
        )
        session.add(record)
        session.flush()
        return operation_id

    @staticmethod
    async def update_progress_async(session: AsyncSession, operation_id: str, progress: int):
        await session.execute(
            update(OperationRecord)
            .where(OperationRecord.operation_id == operation_id)
            .values(progress=progress)
        )

    @staticmethod
    async def complete_operation_async(session: AsyncSession, operation_id: str, result: dict = None):
        await session.execute(
            update(OperationRecord)
            .where(OperationRecord.operation_id == operation_id)
            .values(
                status='completed',
                progress=100,
                result=result,
                end_time=int(time.time() * 1000)
            )
        )

    @staticmethod
    async def fail_operation_async(session: AsyncSession, operation_id: str, error_message: str):
        await session.execute(
            update(OperationRecord)
            .where(OperationRecord.operation_id == operation_id)
            .values(
                status='failed',
                error_message=error_message,
                end_time=int(time.time() * 1000)
            )
        )

    @staticmethod
    async def get_operation_async(session: AsyncSession, operation_id: str) -> OperationRecord | None:
        result = await session.execute(
            select(OperationRecord)
            .where(OperationRecord.operation_id == operation_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_running_operations_async(session: AsyncSession, device_sn: str):
        result = await session.execute(
            select(OperationRecord)
            .where(OperationRecord.device_sn == device_sn)
            .where(OperationRecord.status == 'running')
            .order_by(OperationRecord.start_time.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def get_operation_history_async(session: AsyncSession, device_sn: str, limit: int = 50):
        result = await session.execute(
            select(OperationRecord)
            .where(OperationRecord.device_sn == device_sn)
            .order_by(OperationRecord.start_time.desc())
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def clear_old_operations_async(session: AsyncSession, device_sn: str, days_to_keep: int = 30):
        cutoff_time = int(time.time() * 1000) - (days_to_keep * 24 * 3600 * 1000)
        await session.execute(
            delete(OperationRecord)
            .where(OperationRecord.device_sn == device_sn)
            .where(OperationRecord.start_time < cutoff_time)
        )
