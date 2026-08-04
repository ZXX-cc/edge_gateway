import time
import uuid
from uuid import UUID

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .connection import is_mongo_session, mongo_model_from_document
from .models import Mission
from .schemas import MissionCreate


class MissionDB:
    @staticmethod
    async def create_mission(session: AsyncSession, mission_data: MissionCreate) -> Mission:
        mission_id = str(uuid.uuid4())
        values = {
            "id": mission_id,
            "name": mission_data.name,
            "device_id": str(mission_data.device_id),
            "status": mission_data.status,
            "execution_conditions": mission_data.execution_conditions,
            "created_at": int(time.time()),
        }
        if is_mongo_session(session):
            document = {"_id": mission_id, **values}
            await session.run(lambda database: database["missions"].insert_one(document))
            return Mission(**values)

        db_mission = Mission(**values)
        session.add(db_mission)
        await session.flush()
        return db_mission

    @staticmethod
    async def get_mission(session: AsyncSession, mission_id: UUID) -> Mission | None:
        mission_id = str(mission_id)
        if is_mongo_session(session):
            document = await session.run(
                lambda database: database["missions"].find_one({"id": mission_id})
            )
            return mongo_model_from_document(Mission, document)
        result = await session.execute(select(Mission).where(Mission.id == mission_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_mission_by_external_id(session: AsyncSession, external_id: str) -> Mission | None:
        if is_mongo_session(session):
            document = await session.run(
                lambda database: database["missions"].find_one(
                    {"name": external_id}, sort=[("created_at", -1)]
                )
            )
            return mongo_model_from_document(Mission, document)
        result = await session.execute(
            select(Mission)
            .where(Mission.name == external_id)
            .order_by(desc(Mission.created_at))
        )
        return result.scalars().first()

    @staticmethod
    async def update_mission_status(session: AsyncSession, mission_id: UUID, status: str) -> bool:
        mission_id = str(mission_id)
        if is_mongo_session(session):
            result = await session.run(
                lambda database: database["missions"].update_one(
                    {"id": mission_id}, {"$set": {"status": status}}
                )
            )
            return result.matched_count > 0
        result = await session.execute(
            update(Mission).where(Mission.id == mission_id).values(status=status)
        )
        return result.rowcount > 0

    @staticmethod
    async def update_mission_breakpoint(
        session: AsyncSession,
        external_id: str,
        breakpoint_data: dict,
    ) -> bool:
        """Persist the latest wayline breakpoint on the mission document.

        ``external_id`` is the DJI ``flight_id`` stored as ``Mission.name``.
        MongoDB stores the full breakpoint under
        ``execution_conditions.breakpoint``.  SQLite fallback keeps the same
        shape in the JSON column so the recovery code stays backend-neutral.
        """
        if not external_id:
            return False
        breakpoint_data = dict(breakpoint_data or {})
        if is_mongo_session(session):
            def write_latest_breakpoint(database):
                collection = database["missions"]
                document = collection.find_one({"name": external_id}, sort=[("created_at", -1)])
                if not document:
                    return 0
                selector = {"id": document.get("id")} if document.get("id") else {"_id": document.get("_id")}
                result = collection.update_one(
                    selector,
                    {
                        "$set": {
                            "execution_conditions.breakpoint": breakpoint_data,
                            "execution_conditions.breakpoint_updated_at": breakpoint_data.get(
                                "updated_at", int(time.time() * 1000)
                            ),
                        }
                    },
                    upsert=False,
                )
                return result.matched_count

            matched_count = await session.run(write_latest_breakpoint)
            return matched_count > 0

        mission = await MissionDB.get_mission_by_external_id(session, external_id)
        if not mission:
            return False
        execution_conditions = dict(mission.execution_conditions or {})
        execution_conditions["breakpoint"] = breakpoint_data
        execution_conditions["breakpoint_updated_at"] = breakpoint_data.get(
            "updated_at", int(time.time() * 1000)
        )
        result = await session.execute(
            update(Mission)
            .where(Mission.id == str(mission.id))
            .values(execution_conditions=execution_conditions)
        )
        return result.rowcount > 0

    @staticmethod
    async def get_mission_breakpoint(session: AsyncSession, external_id: str) -> dict | None:
        if not external_id:
            return None
        mission = await MissionDB.get_mission_by_external_id(session, external_id)
        if not mission:
            return None
        execution_conditions = mission.execution_conditions or {}
        breakpoint_data = execution_conditions.get("breakpoint")
        return dict(breakpoint_data) if isinstance(breakpoint_data, dict) else None

    @staticmethod
    async def clear_mission_breakpoint(session: AsyncSession, external_id: str) -> bool:
        if not external_id:
            return False
        if is_mongo_session(session):
            def clear_latest_breakpoint(database):
                collection = database["missions"]
                document = collection.find_one({"name": external_id}, sort=[("created_at", -1)])
                if not document:
                    return 0
                selector = {"id": document.get("id")} if document.get("id") else {"_id": document.get("_id")}
                result = collection.update_one(
                    selector,
                    {
                        "$unset": {
                            "execution_conditions.breakpoint": "",
                            "execution_conditions.breakpoint_updated_at": "",
                        }
                    },
                )
                return result.matched_count

            matched_count = await session.run(clear_latest_breakpoint)
            return matched_count > 0

        mission = await MissionDB.get_mission_by_external_id(session, external_id)
        if not mission:
            return False
        execution_conditions = dict(mission.execution_conditions or {})
        execution_conditions.pop("breakpoint", None)
        execution_conditions.pop("breakpoint_updated_at", None)
        result = await session.execute(
            update(Mission)
            .where(Mission.id == str(mission.id))
            .values(execution_conditions=execution_conditions)
        )
        return result.rowcount > 0

    @staticmethod
    async def get_pending_missions(session: AsyncSession) -> list:
        pending_statuses = ["in_progress", "paused", "running"]
        if is_mongo_session(session):
            documents = await session.run(
                lambda database: list(
                    database["missions"]
                    .find({"status": {"$in": pending_statuses}})
                    .sort("created_at", -1)
                )
            )
            return [mongo_model_from_document(Mission, item) for item in documents]
        result = await session.execute(
            select(Mission)
            .where(Mission.status.in_(pending_statuses))
            .order_by(Mission.created_at.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def get_all_missions(session: AsyncSession, limit: int = 50) -> list:
        if is_mongo_session(session):
            documents = await session.run(
                lambda database: list(
                    database["missions"].find({}).sort("created_at", -1).limit(limit)
                )
            )
            return [mongo_model_from_document(Mission, item) for item in documents]
        result = await session.execute(
            select(Mission).order_by(desc(Mission.created_at)).limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def delete_mission(session: AsyncSession, mission_id: UUID) -> bool:
        return await MissionDB.update_mission_status(session, mission_id, "deleted")
