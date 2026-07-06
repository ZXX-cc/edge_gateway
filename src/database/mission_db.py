import uuid
from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from .models import Mission
from .schemas import MissionCreate


class MissionDB:
    @staticmethod
    async def create_mission(session: AsyncSession, mission_data: MissionCreate) -> Mission:
        db_mission = Mission(
            id=str(uuid.uuid4()),
            name=mission_data.name,
            device_id=str(mission_data.device_id),
            status=mission_data.status,
            execution_conditions=mission_data.execution_conditions
        )
        session.add(db_mission)
        await session.flush()
        return db_mission

    @staticmethod
    async def get_mission(session: AsyncSession, mission_id: UUID) -> Mission | None:
        result = await session.execute(
            select(Mission).where(Mission.id == str(mission_id))
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_mission_by_external_id(session: AsyncSession, external_id: str) -> Mission | None:
        result = await session.execute(
            select(Mission)
            .where(Mission.name == external_id)
            .order_by(desc(Mission.created_at))
        )
        return result.scalars().first()

    @staticmethod
    async def update_mission_status(session: AsyncSession, mission_id: UUID, status: str) -> bool:
        result = await session.execute(
            update(Mission)
            .where(Mission.id == str(mission_id))
            .values(status=status)
        )
        return result.rowcount > 0

    @staticmethod
    async def get_pending_missions(session: AsyncSession) -> list:
        result = await session.execute(
            select(Mission)
            .where(Mission.status.in_(['running', 'paused']))
            .order_by(Mission.created_at.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def get_all_missions(session: AsyncSession, limit: int = 50) -> list:
        result = await session.execute(
            select(Mission)
            .order_by(desc(Mission.created_at))
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def delete_mission(session: AsyncSession, mission_id: UUID) -> bool:
        result = await session.execute(
            update(Mission)
            .where(Mission.id == str(mission_id))
            .values(status='deleted')
        )
        return result.rowcount > 0