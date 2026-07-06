import uuid
from sqlalchemy import select, update, delete, desc
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import List
from .models import MediaFile


class MediaDB:
    @staticmethod
    async def create_media_file(session: AsyncSession, media_data: dict) -> MediaFile:
        db_media = MediaFile(
            id=str(uuid.uuid4()),
            mission_id=media_data.get('mission_id'),
            device_sn=media_data['device_sn'],
            file_name=media_data['file_name'],
            file_path=media_data['file_path'],
            file_size=media_data['file_size'],
            file_type=media_data['file_type'],
            resolution=media_data.get('resolution'),
            duration=media_data.get('duration'),
            capture_time=media_data.get('capture_time'),
            latitude=media_data.get('latitude'),
            longitude=media_data.get('longitude'),
            altitude=media_data.get('altitude'),
            upload_status=media_data.get('upload_status', 'pending'),
            upload_url=media_data.get('upload_url'),
            thumbnail_url=media_data.get('thumbnail_url'),
        )
        session.add(db_media)
        await session.flush()
        return db_media

    @staticmethod
    async def get_media_by_id(session: AsyncSession, media_id: str) -> MediaFile | None:
        result = await session.execute(
            select(MediaFile).where(MediaFile.id == media_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_media_by_mission(session: AsyncSession, mission_id: str) -> List[MediaFile]:
        result = await session.execute(
            select(MediaFile)
            .where(MediaFile.mission_id == mission_id)
            .order_by(MediaFile.capture_time.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def get_media_by_device(session: AsyncSession, device_sn: str, limit: int = 100) -> List[MediaFile]:
        result = await session.execute(
            select(MediaFile)
            .where(MediaFile.device_sn == device_sn)
            .order_by(MediaFile.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_pending_uploads(session: AsyncSession, device_sn: str = None) -> List[MediaFile]:
        query = select(MediaFile).where(MediaFile.upload_status == 'pending')
        if device_sn:
            query = query.where(MediaFile.device_sn == device_sn)
        result = await session.execute(query.order_by(MediaFile.created_at.asc()))
        return result.scalars().all()

    @staticmethod
    async def update_upload_status(session: AsyncSession, media_id: str, status: str, upload_url: str = None) -> bool:
        update_data = {'upload_status': status}
        if upload_url:
            update_data['upload_url'] = upload_url
        result = await session.execute(
            update(MediaFile)
            .where(MediaFile.id == media_id)
            .values(**update_data)
        )
        return result.rowcount > 0

    @staticmethod
    async def delete_media(session: AsyncSession, media_id: str) -> bool:
        result = await session.execute(
            delete(MediaFile).where(MediaFile.id == media_id)
        )
        return result.rowcount > 0

    @staticmethod
    async def batch_delete_by_mission(session: AsyncSession, mission_id: str) -> int:
        result = await session.execute(
            delete(MediaFile).where(MediaFile.mission_id == mission_id)
        )
        return result.rowcount