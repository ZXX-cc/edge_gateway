"""
数据保留管理器 - 边缘网关专用（完全异步版）
负责定期清理过期数据，防止数据库无限增长导致内存/存储问题

配置示例:
{
    "retention": {
        "telemetry_hours": 2,
        "operation_days": 3,
        "alarm_days": 1,
        "media_uploaded_days": 7,
        "cleanup_interval_hours": 1,
        "vacuum_interval_hours": 24,
        "max_db_size_mb": 50
    }
}
"""
import os
import time
import asyncio
from typing import Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.database.connection import get_async_session_local, async_engine, DATABASE_URL, init_database_async
from src.database.time_utils import expired_timestamp_condition

logger = get_logger("retention")


class DataRetentionManager:
    """数据保留管理器（完全异步版）"""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._event_callback: Optional[Callable] = None

        self._telemetry_hours = Config.get('retention.telemetry_hours', 2)
        self._operation_days = Config.get('retention.operation_days', 3)
        self._alarm_days = Config.get('retention.alarm_days', 1)
        self._media_uploaded_days = Config.get('retention.media_uploaded_days', 7)
        self._cleanup_interval_hours = Config.get('retention.cleanup_interval_hours', 1)
        self._vacuum_interval_hours = Config.get('retention.vacuum_interval_hours', 24)
        self._max_db_size_mb = Config.get('retention.max_db_size_mb', 50)

        self._last_cleanup_time = 0
        self._last_vacuum_time = 0

        self._db_file_path = self._extract_db_path()

    def _extract_db_path(self) -> str:
        if DATABASE_URL.startswith("sqlite:///"):
            return DATABASE_URL.replace("sqlite:///", "")
        return "./missions.db"

    def set_event_callback(self, callback: Callable):
        self._event_callback = callback

    async def start(self):
        """异步启动数据保留管理器"""
        if self._running:
            return

        await init_database_async()
        self._running = True
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"DataRetentionManager started with config: "
                    f"telemetry={self._telemetry_hours}h, "
                    f"operation={self._operation_days}d, "
                    f"alarm={self._alarm_days}d, "
                    f"media_uploaded={self._media_uploaded_days}d")

    async def stop(self):
        """异步停止数据保留管理器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("DataRetentionManager stopped")

    def stop_sync(self):
        """同步停止数据保留管理器（用于信号处理器等非异步上下文）"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("DataRetentionManager stopped (sync)")

    async def _cleanup_loop(self):
        """异步清理循环"""
        while self._running:
            try:
                current_time = time.time()

                await self._check_db_size()

                if current_time - self._last_cleanup_time >= self._cleanup_interval_hours * 3600:
                    self._last_cleanup_time = current_time
                    await self._clean_expired_data()

                if current_time - self._last_vacuum_time >= self._vacuum_interval_hours * 3600:
                    self._last_vacuum_time = current_time
                    await self._run_vacuum()

                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Data retention loop error: {e}")
                await asyncio.sleep(60)

    async def _check_db_size(self):
        """异步检查数据库文件大小"""
        try:
            if os.path.exists(self._db_file_path):
                file_size_mb = os.path.getsize(self._db_file_path) / (1024 * 1024)
                if file_size_mb > self._max_db_size_mb:
                    logger.warning(f"Database file size {file_size_mb:.1f}MB exceeds threshold {self._max_db_size_mb}MB")

                    if self._event_callback:
                        self._event_callback({
                            'type': 'db_size_warning',
                            'data': {
                                'file_size_mb': file_size_mb,
                                'threshold_mb': self._max_db_size_mb,
                                'timestamp': int(time.time() * 1000)
                            }
                        })
        except Exception as e:
            logger.error(f"Check DB size error: {e}")

    async def _clean_expired_data(self):
        """异步清理过期数据"""
        from sqlalchemy import delete
        AsyncSessionLocal = get_async_session_local()
        async with AsyncSessionLocal() as session:
            deleted_counts = {}

            if self._telemetry_hours > 0:
                from src.database.models import TelemetryData
                result = await session.execute(
                    delete(TelemetryData)
                    .where(expired_timestamp_condition(TelemetryData.timestamp, self._telemetry_hours * 3600))
                )
                deleted_counts['telemetry'] = result.rowcount if hasattr(result, 'rowcount') else 0

            if self._operation_days > 0:
                from src.database.models import OperationRecord
                result = await session.execute(
                    delete(OperationRecord)
                    .where(OperationRecord.end_time.isnot(None))
                    .where(expired_timestamp_condition(OperationRecord.end_time, self._operation_days * 24 * 3600))
                )
                deleted_counts['operation'] = result.rowcount if hasattr(result, 'rowcount') else 0

            if self._alarm_days > 0:
                from src.database.models import Alarm
                result = await session.execute(
                    delete(Alarm)
                    .where(expired_timestamp_condition(Alarm.create_time, self._alarm_days * 24 * 3600))
                )
                deleted_counts['alarm'] = result.rowcount if hasattr(result, 'rowcount') else 0

            if self._media_uploaded_days > 0:
                from src.database.models import MediaFile
                result = await session.execute(
                    delete(MediaFile)
                    .where(MediaFile.upload_status == 'completed')
                    .where(expired_timestamp_condition(MediaFile.created_at, self._media_uploaded_days * 24 * 3600))
                )
                deleted_counts['media_uploaded'] = result.rowcount if hasattr(result, 'rowcount') else 0

            await session.commit()

            if any(v > 0 for v in deleted_counts.values()):
                logger.info(f"Data retention cleanup: {deleted_counts}")

    async def _run_vacuum(self):
        """异步执行 SQLite VACUUM 操作"""
        start_time = time.time()
        try:
            from sqlalchemy import text
            async with async_engine.begin() as conn:
                await conn.execute(text("VACUUM"))

            elapsed = time.time() - start_time
            logger.info(f"SQLite VACUUM completed in {elapsed:.2f} seconds")
        except Exception as e:
            logger.error(f"VACUUM execution error: {e}")

    async def manual_cleanup(self):
        """异步手动触发一次清理"""
        logger.info("Manual cleanup triggered")
        await self._clean_expired_data()

    def get_status(self) -> dict:
        return {
            'running': self._running,
            'config': {
                'telemetry_hours': self._telemetry_hours,
                'operation_days': self._operation_days,
                'alarm_days': self._alarm_days,
                'media_uploaded_days': self._media_uploaded_days,
                'cleanup_interval_hours': self._cleanup_interval_hours,
                'vacuum_interval_hours': self._vacuum_interval_hours,
                'max_db_size_mb': self._max_db_size_mb
            },
            'last_cleanup_time': self._last_cleanup_time,
            'last_vacuum_time': self._last_vacuum_time,
            'db_file_path': self._db_file_path
        }
