from typing import List
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .connection import is_mongo_session, mongo_model_from_document
from .models import Waypoint
from .schemas import WaypointCreate


def _waypoint_values(mission_id: UUID, waypoint: WaypointCreate) -> dict:
    return {
        "mission_id": str(mission_id),
        "sequence": waypoint.sequence,
        "command": waypoint.command,
        "frame": waypoint.frame,
        "latitude": waypoint.latitude,
        "longitude": waypoint.longitude,
        "altitude": waypoint.altitude,
        "param1": waypoint.param1,
        "param2": waypoint.param2,
        "param3": waypoint.param3,
        "param4": waypoint.param4,
        "autocontinue": waypoint.autocontinue,
        "action_type": waypoint.action_type,
        "flight_speed": waypoint.flight_speed,
        "photo_interval": waypoint.photo_interval,
    }


class WaypointDB:
    @staticmethod
    async def batch_create_waypoints_for_mission(
        session: AsyncSession,
        mission_id: UUID,
        waypoints_data: List[WaypointCreate],
    ) -> List[Waypoint]:
        values = [_waypoint_values(mission_id, waypoint) for waypoint in waypoints_data]
        if is_mongo_session(session):
            if values:
                await session.run(
                    lambda database: database["waypoints"].insert_many(
                        [dict(item) for item in values], ordered=True
                    )
                )
            return [Waypoint(**item) for item in values]

        db_waypoints = [Waypoint(**item) for item in values]
        session.add_all(db_waypoints)
        await session.flush()
        return db_waypoints

    @staticmethod
    async def replace_mission_waypoints(
        session: AsyncSession,
        mission_id: UUID,
        waypoints_data: List[WaypointCreate],
    ) -> List[Waypoint]:
        if is_mongo_session(session):
            await session.run(
                lambda database: database["waypoints"].delete_many(
                    {"mission_id": str(mission_id)}
                )
            )
        else:
            await session.execute(
                delete(Waypoint).where(Waypoint.mission_id == str(mission_id))
            )
        return await WaypointDB.batch_create_waypoints_for_mission(
            session, mission_id, waypoints_data
        )

    @staticmethod
    async def get_waypoints_by_mission(
        session: AsyncSession, mission_id: UUID
    ) -> List[Waypoint]:
        if is_mongo_session(session):
            documents = await session.run(
                lambda database: list(
                    database["waypoints"]
                    .find({"mission_id": str(mission_id)})
                    .sort("sequence", 1)
                )
            )
            return [mongo_model_from_document(Waypoint, item) for item in documents]
        result = await session.execute(
            select(Waypoint)
            .where(Waypoint.mission_id == str(mission_id))
            .order_by(Waypoint.sequence.asc())
        )
        return result.scalars().all()

    @staticmethod
    async def count_waypoints_by_mission(session: AsyncSession, mission_id: UUID) -> int:
        if is_mongo_session(session):
            return await session.run(
                lambda database: database["waypoints"].count_documents(
                    {"mission_id": str(mission_id)}
                )
            )
        result = await session.execute(
            select(Waypoint).where(Waypoint.mission_id == str(mission_id))
        )
        return len(result.scalars().all())

    @staticmethod
    async def delete_waypoints_by_mission(session: AsyncSession, mission_id: UUID) -> int:
        if is_mongo_session(session):
            result = await session.run(
                lambda database: database["waypoints"].delete_many(
                    {"mission_id": str(mission_id)}
                )
            )
            return result.deleted_count
        result = await session.execute(
            delete(Waypoint).where(Waypoint.mission_id == str(mission_id))
        )
        return result.rowcount
