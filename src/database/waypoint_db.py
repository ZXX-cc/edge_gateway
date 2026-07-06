from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import List
from .models import Waypoint
from .schemas import WaypointCreate


class WaypointDB:
    @staticmethod
    async def batch_create_waypoints_for_mission(
        session: AsyncSession,
        mission_id: UUID,
        waypoints_data: List[WaypointCreate]
    ) -> List[Waypoint]:
        db_waypoints = []
        for wp_data in waypoints_data:
            db_wp = Waypoint(
                mission_id=str(mission_id),
                sequence=wp_data.sequence,
                command=wp_data.command,
                frame=wp_data.frame,
                latitude=wp_data.latitude,
                longitude=wp_data.longitude,
                altitude=wp_data.altitude,
                param1=wp_data.param1,
                param2=wp_data.param2,
                param3=wp_data.param3,
                param4=wp_data.param4,
                autocontinue=wp_data.autocontinue,
                action_type=wp_data.action_type,
                flight_speed=wp_data.flight_speed,
                photo_interval=wp_data.photo_interval,
            )
            db_wp.mission_id = str(mission_id)
            db_waypoints.append(db_wp)
        session.add_all(db_waypoints)
        await session.flush()
        return db_waypoints

    @staticmethod
    async def replace_mission_waypoints(
        session: AsyncSession,
        mission_id: UUID,
        waypoints_data: List[WaypointCreate]
    ) -> List[Waypoint]:
        await session.execute(
            delete(Waypoint).where(Waypoint.mission_id == str(mission_id))
        )
        return await WaypointDB.batch_create_waypoints_for_mission(session, mission_id, waypoints_data)

    @staticmethod
    async def get_waypoints_by_mission(
        session: AsyncSession,
        mission_id: UUID
    ) -> List[Waypoint]:
        result = await session.execute(
            select(Waypoint)
            .where(Waypoint.mission_id == str(mission_id))
            .order_by(Waypoint.sequence.asc())
        )
        return result.scalars().all()

    @staticmethod
    async def count_waypoints_by_mission(session: AsyncSession, mission_id: UUID) -> int:
        result = await session.execute(
            select(Waypoint)
            .where(Waypoint.mission_id == str(mission_id))
        )
        return len(result.scalars().all())

    @staticmethod
    async def delete_waypoints_by_mission(session: AsyncSession, mission_id: UUID) -> int:
        result = await session.execute(
            delete(Waypoint).where(Waypoint.mission_id == str(mission_id))
        )
        return result.rowcount