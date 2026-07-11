import time
import uuid
from typing import List

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .connection import is_mongo_session, mongo_model_from_document
from .models import MediaFile


class MediaDB:
    @staticmethod
    async def create_media_file(session: AsyncSession, media_data: dict) -> MediaFile:
        media_id = str(uuid.uuid4())
        values = {
            "id": media_id,
            "mission_id": media_data.get("mission_id"),
            "device_sn": media_data["device_sn"],
            "file_name": media_data["file_name"],
            "file_path": media_data["file_path"],
            "file_size": media_data["file_size"],
            "file_type": media_data["file_type"],
            "resolution": media_data.get("resolution"),
            "duration": media_data.get("duration"),
            "capture_time": media_data.get("capture_time"),
            "latitude": media_data.get("latitude"),
            "longitude": media_data.get("longitude"),
            "altitude": media_data.get("altitude"),
            "upload_status": media_data.get("upload_status", "pending"),
            "upload_url": media_data.get("upload_url"),
            "thumbnail_url": media_data.get("thumbnail_url"),
            "created_at": media_data.get("created_at", int(time.time())),
        }
        media = MediaFile(**values)
        if is_mongo_session(session):
            await session.run(
                lambda database: database["media_files"].insert_one(
                    {"_id": media_id, **values}
                )
            )
            return media
        session.add(media)
        await session.flush()
        return media

    @staticmethod
    async def get_media_by_id(session: AsyncSession, media_id: str) -> MediaFile | None:
        if is_mongo_session(session):
            document = await session.run(
                lambda database: database["media_files"].find_one({"id": media_id})
            )
            return mongo_model_from_document(MediaFile, document)
        result = await session.execute(select(MediaFile).where(MediaFile.id == media_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def _load(session, query: dict, sort_field: str, direction: int, limit=None):
        def load(database):
            cursor = database["media_files"].find(query).sort(sort_field, direction)
            return list(cursor.limit(limit)) if limit else list(cursor)
        documents = await session.run(load)
        return [mongo_model_from_document(MediaFile, item) for item in documents]

    @staticmethod
    async def get_media_by_mission(session: AsyncSession, mission_id: str) -> List[MediaFile]:
        if is_mongo_session(session):
            return await MediaDB._load(session, {"mission_id": mission_id}, "capture_time", -1)
        result = await session.execute(
            select(MediaFile)
            .where(MediaFile.mission_id == mission_id)
            .order_by(MediaFile.capture_time.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def get_media_by_device(session: AsyncSession, device_sn: str, limit: int = 100) -> List[MediaFile]:
        if is_mongo_session(session):
            return await MediaDB._load(session, {"device_sn": device_sn}, "created_at", -1, limit)
        result = await session.execute(
            select(MediaFile)
            .where(MediaFile.device_sn == device_sn)
            .order_by(MediaFile.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_pending_uploads(session: AsyncSession, device_sn: str = None) -> List[MediaFile]:
        if is_mongo_session(session):
            query = {"upload_status": "pending"}
            if device_sn:
                query["device_sn"] = device_sn
            return await MediaDB._load(session, query, "created_at", 1)
        query = select(MediaFile).where(MediaFile.upload_status == "pending")
        if device_sn:
            query = query.where(MediaFile.device_sn == device_sn)
        result = await session.execute(query.order_by(MediaFile.created_at.asc()))
        return result.scalars().all()

    @staticmethod
    async def update_upload_status(session: AsyncSession, media_id: str, status: str,
                                   upload_url: str = None) -> bool:
        values = {"upload_status": status}
        if upload_url:
            values["upload_url"] = upload_url
        if is_mongo_session(session):
            result = await session.run(
                lambda database: database["media_files"].update_one(
                    {"id": media_id}, {"$set": values}
                )
            )
            return result.matched_count > 0
        result = await session.execute(
            update(MediaFile).where(MediaFile.id == media_id).values(**values)
        )
        return result.rowcount > 0

    @staticmethod
    async def delete_media(session: AsyncSession, media_id: str) -> bool:
        if is_mongo_session(session):
            result = await session.run(
                lambda database: database["media_files"].delete_one({"id": media_id})
            )
            return result.deleted_count > 0
        result = await session.execute(delete(MediaFile).where(MediaFile.id == media_id))
        return result.rowcount > 0

    @staticmethod
    async def batch_delete_by_mission(session: AsyncSession, mission_id: str) -> int:
        if is_mongo_session(session):
            result = await session.run(
                lambda database: database["media_files"].delete_many(
                    {"mission_id": mission_id}
                )
            )
            return result.deleted_count
        result = await session.execute(delete(MediaFile).where(MediaFile.mission_id == mission_id))
        return result.rowcount
