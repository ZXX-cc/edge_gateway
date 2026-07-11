import time
import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from .connection import is_mongo_session, mongo_expired_filter, mongo_model_from_document
from .models import OperationRecord


def _operation_values(device_sn, command, params, operator, operation_id):
    return {
        "device_sn": device_sn,
        "operation_id": operation_id,
        "command": command,
        "status": "running",
        "progress": 0,
        "params": params,
        "result": None,
        "error_message": None,
        "start_time": int(time.time() * 1000),
        "end_time": None,
        "operator": operator,
    }


class OperationDB:
    @staticmethod
    async def create_operation_async(session: AsyncSession, device_sn: str, command: str,
                                      params: dict = None, operator: str = "",
                                      operation_id: str = None) -> str:
        operation_id = operation_id or str(uuid.uuid4())
        values = _operation_values(device_sn, command, params, operator, operation_id)
        if is_mongo_session(session):
            await session.run(
                lambda database: database["operation_record"].insert_one(
                    {"_id": operation_id, **values}
                )
            )
        else:
            session.add(OperationRecord(**values))
            await session.flush()
        return operation_id

    @staticmethod
    def create_operation_sync(session: Session, device_sn: str, command: str,
                              params: dict = None, operator: str = "",
                              operation_id: str = None) -> str:
        operation_id = operation_id or str(uuid.uuid4())
        values = _operation_values(device_sn, command, params, operator, operation_id)
        if is_mongo_session(session):
            session.collection("operation_record").insert_one({"_id": operation_id, **values})
        else:
            session.add(OperationRecord(**values))
            session.flush()
        return operation_id

    @staticmethod
    async def _update(session, operation_id: str, values: dict):
        if is_mongo_session(session):
            result = await session.run(
                lambda database: database["operation_record"].update_one(
                    {"operation_id": operation_id}, {"$set": values}
                )
            )
            return result.matched_count > 0
        result = await session.execute(
            update(OperationRecord)
            .where(OperationRecord.operation_id == operation_id)
            .values(**values)
        )
        return result.rowcount > 0

    @staticmethod
    async def update_progress_async(session: AsyncSession, operation_id: str, progress: int):
        return await OperationDB._update(session, operation_id, {"progress": progress})

    @staticmethod
    async def complete_operation_async(session: AsyncSession, operation_id: str, result: dict = None):
        return await OperationDB._update(
            session,
            operation_id,
            {"status": "completed", "progress": 100, "result": result, "end_time": int(time.time() * 1000)},
        )

    @staticmethod
    async def fail_operation_async(session: AsyncSession, operation_id: str, error_message: str):
        return await OperationDB._update(
            session,
            operation_id,
            {"status": "failed", "error_message": error_message, "end_time": int(time.time() * 1000)},
        )

    @staticmethod
    async def get_operation_async(session: AsyncSession, operation_id: str) -> OperationRecord | None:
        if is_mongo_session(session):
            document = await session.run(
                lambda database: database["operation_record"].find_one(
                    {"operation_id": operation_id}
                )
            )
            return mongo_model_from_document(OperationRecord, document)
        result = await session.execute(
            select(OperationRecord).where(OperationRecord.operation_id == operation_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_running_operations_async(session: AsyncSession, device_sn: str):
        return await OperationDB._get_history(session, device_sn, None, "running")

    @staticmethod
    async def get_operation_history_async(session: AsyncSession, device_sn: str, limit: int = 50):
        return await OperationDB._get_history(session, device_sn, limit, None)

    @staticmethod
    async def _get_history(session, device_sn, limit, status):
        if is_mongo_session(session):
            query = {"device_sn": device_sn}
            if status:
                query["status"] = status
            def load(database):
                cursor = database["operation_record"].find(query).sort("start_time", -1)
                return list(cursor.limit(limit)) if limit else list(cursor)
            documents = await session.run(load)
            return [mongo_model_from_document(OperationRecord, item) for item in documents]
        query = select(OperationRecord).where(OperationRecord.device_sn == device_sn)
        if status:
            query = query.where(OperationRecord.status == status)
        query = query.order_by(OperationRecord.start_time.desc())
        if limit:
            query = query.limit(limit)
        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def clear_old_operations_async(session: AsyncSession, device_sn: str, days_to_keep: int = 30):
        if is_mongo_session(session):
            query = {"device_sn": device_sn, **mongo_expired_filter("start_time", days_to_keep * 86400)}
            result = await session.run(
                lambda database: database["operation_record"].delete_many(query)
            )
            return result.deleted_count
        cutoff_time = int(time.time() * 1000) - days_to_keep * 86400 * 1000
        result = await session.execute(
            delete(OperationRecord)
            .where(OperationRecord.device_sn == device_sn)
            .where(OperationRecord.start_time < cutoff_time)
        )
        return result.rowcount
