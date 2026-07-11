import logging
import asyncio
from typing import Dict, Optional, List, Callable
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from .connection import (
    get_async_session_local,
    init_database_async,
    is_mongo_session,
    mongo_expired_filter,
    mongo_model_from_document,
)
from .models import OperationRecord, Alarm, MediaFile
from .time_utils import expired_timestamp_condition

logger = logging.getLogger(__name__)


class CloudSyncManager:
    def __init__(self, mqtt_client=None):
        self._mqtt_client = mqtt_client
        self._running = False
        self._sync_interval = 300
        self._sync_task: Optional[asyncio.Task] = None
        self._sync_lock = asyncio.Lock()

    async def start(self):
        """启动同步管理器"""
        if self._running:
            return
        await init_database_async()
        self._running = True
        logger.info("CloudSyncManager database initialized")
        self._sync_task = asyncio.create_task(self._periodic_sync())
        logger.info("CloudSyncManager started")

    async def stop(self):
        """停止同步管理器"""
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        logger.info("CloudSyncManager stopped")

    def stop_sync(self):
        """同步停止同步管理器（用于信号处理器等非异步上下文）"""
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
        logger.info("CloudSyncManager stopped (sync)")

    async def _periodic_sync(self):
        """定期同步任务"""
        while self._running:
            try:
                async with self._sync_lock:
                    await self._sync_operations()
                    await self._sync_alarms()
                    await self._sync_media_metadata()
            except Exception as e:
                logger.error(f"Cloud sync failed: {e}", exc_info=True)

            await asyncio.sleep(self._sync_interval)

    async def _sync_operations(self):
        """同步操作记录到云端"""
        AsyncSessionLocal = get_async_session_local()
        async with AsyncSessionLocal() as session:
            if is_mongo_session(session):
                query = {
                    'status': {'$in': ['completed', 'failed']},
                    'end_time': {'$ne': None},
                    **mongo_expired_filter('end_time', 3 * 24 * 3600),
                }
                await self._sync_mongo_collection(
                    session, 'operation_record', query, OperationRecord,
                    self._report_operation_to_cloud,
                )
                return
            result = await session.execute(
                select(OperationRecord)
                .filter(OperationRecord.end_time.isnot(None))
                .filter(expired_timestamp_condition(OperationRecord.end_time, 3 * 24 * 3600))
                .filter(OperationRecord.status.in_(['completed', 'failed']))
            )
            records = result.scalars().all()

            if records and self._mqtt_client:
                for record in records:
                    await self._report_operation_to_cloud(record)
                    await session.delete(record)

                await session.commit()
                logger.info(f"Synchronized {len(records)} operation records to cloud")

    async def _sync_alarms(self):
        """同步告警记录到云端"""
        AsyncSessionLocal = get_async_session_local()
        async with AsyncSessionLocal() as session:
            if is_mongo_session(session):
                query = {
                    'is_read': {'$in': [1, True]},
                    **mongo_expired_filter('create_time', 24 * 3600),
                }
                await self._sync_mongo_collection(
                    session, 'alarms', query, Alarm, self._report_alarm_to_cloud,
                )
                return
            result = await session.execute(
                select(Alarm)
                .filter(expired_timestamp_condition(Alarm.create_time, 24 * 3600))
                .filter(Alarm.is_read == 1)
            )
            alarms = result.scalars().all()

            if alarms and self._mqtt_client:
                for alarm in alarms:
                    await self._report_alarm_to_cloud(alarm)
                    await session.delete(alarm)

                await session.commit()
                logger.info(f"Synchronized {len(alarms)} alarms to cloud")

    async def _sync_media_metadata(self):
        """同步媒体文件元数据到云端（上传完成后）"""
        AsyncSessionLocal = get_async_session_local()
        async with AsyncSessionLocal() as session:
            if is_mongo_session(session):
                query = {
                    'upload_status': 'completed',
                    **mongo_expired_filter('created_at', 7 * 24 * 3600),
                }
                await self._sync_mongo_collection(
                    session, 'media_files', query, MediaFile,
                    self._report_media_to_cloud,
                )
                return
            result = await session.execute(
                select(MediaFile)
                .filter(expired_timestamp_condition(MediaFile.created_at, 7 * 24 * 3600))
                .filter(MediaFile.upload_status == 'completed')
            )
            media_files = result.scalars().all()

            if media_files and self._mqtt_client:
                for media in media_files:
                    await self._report_media_to_cloud(media)
                    await session.delete(media)

                await session.commit()
                logger.info(f"Synchronized {len(media_files)} media metadata to cloud")

    async def _sync_mongo_collection(self, session, collection_name, query,
                                     model_class, reporter):
        documents = await session.run(
            lambda database: list(database[collection_name].find(query))
        )
        if not documents or not self._mqtt_client:
            return
        for document in documents:
            await reporter(mongo_model_from_document(model_class, document))
        document_ids = [document['_id'] for document in documents]
        await session.run(
            lambda database: database[collection_name].delete_many(
                {'_id': {'$in': document_ids}}
            )
        )
        await session.commit()
        logger.info("Synchronized %s %s records to cloud", len(documents), collection_name)

    async def _report_operation_to_cloud(self, record: OperationRecord):
        """上报操作记录到云端"""
        event_data = {
            'operation_id': record.operation_id,
            'device_sn': record.device_sn,
            'command': record.command,
            'status': record.status,
            'progress': record.progress,
            'start_time': record.start_time,
            'end_time': record.end_time,
            'error_message': record.error_message
        }
        try:
            self._mqtt_client.publish_event(event_data)
        except Exception as e:
            logger.warning(f"Failed to sync operation {record.operation_id}: {e}")

    async def _report_alarm_to_cloud(self, alarm: Alarm):
        """上报告警到云端"""
        event_data = {
            'alarm_id': alarm.alarm_id,
            'gateway_sn': alarm.gateway_sn,
            'alarm_level': alarm.alarm_level,
            'alarm_content': alarm.alarm_content,
            'alarm_type': alarm.alarm_type,
            'create_time': alarm.create_time,
            'extra_info': alarm.extra_info
        }
        try:
            self._mqtt_client.publish_event(event_data)
        except Exception as e:
            logger.warning(f"Failed to sync alarm {alarm.alarm_id}: {e}")

    async def _report_media_to_cloud(self, media: MediaFile):
        """上报媒体文件元数据到云端"""
        event_data = {
            'id': media.id,
            'mission_id': media.mission_id,
            'device_sn': media.device_sn,
            'file_name': media.file_name,
            'file_size': media.file_size,
            'file_type': media.file_type,
            'upload_url': media.upload_url,
            'capture_time': media.capture_time,
            'latitude': media.latitude,
            'longitude': media.longitude,
            'altitude': media.altitude
        }
        try:
            self._mqtt_client.publish_event(event_data)
        except Exception as e:
            logger.warning(f"Failed to sync media {media.file_name}: {e}")

    async def sync_now(self):
        """立即执行一次同步"""
        if self._running:
            async with self._sync_lock:
                await self._sync_operations()
                await self._sync_alarms()
                await self._sync_media_metadata()
            logger.info("Manual cloud sync completed")
