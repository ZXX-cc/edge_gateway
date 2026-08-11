"""
遥测本地 I/O 后台写入器。

OSD 上报线程只负责构建和发布实时数据，数据库落库与本地历史 JSONL
写入放到这里异步处理，避免磁盘或数据库抖动拉长 OSD 上报间隔。
"""
from __future__ import annotations

import copy
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from src.database.telemetry_db import TelemetryDB
from src.utils.field_labels import format_with_labels
from src.utils.logger import get_logger


logger = get_logger("telemetry.io")
drone_report_logger = get_logger("drone.report")


@dataclass
class TelemetryIoTask:
    device_sn: str
    osd_data: dict
    persist_database: bool = True
    history_source: Optional[str] = None
    osd_log_source: Optional[str] = None


class TelemetryIoWorker:
    """把高频 OSD 本地持久化从实时上报线程中剥离出来。"""

    def __init__(
        self,
        session_factory: Callable,
        history_logger=None,
        *,
        max_queue_size: int = 1000,
    ):
        self._session_factory = session_factory
        self._history_logger = history_logger
        self._queue: queue.Queue[TelemetryIoTask | None] = queue.Queue(
            maxsize=max(1, int(max_queue_size or 1000))
        )
        self._running = False
        self._thread: threading.Thread | None = None
        self._dropped_count = 0
        self._drop_log_threshold = 1

    def set_history_logger(self, history_logger) -> None:
        self._history_logger = history_logger

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name="telemetry-io-writer",
            daemon=True,
        )
        self._thread.start()
        logger.info("遥测本地 I/O 后台写入线程已启动: max_queue=%s", self._queue.maxsize)

    def stop(self, timeout: float = 5.0) -> None:
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info(
            "遥测本地 I/O 后台写入线程已停止: pending=%s dropped=%s",
            self._queue.qsize(),
            self._dropped_count,
        )

    def enqueue_telemetry(
        self,
        device_sn: str,
        osd_data: dict,
        *,
        history_source: Optional[str] = None,
        persist_database: bool = True,
    ) -> bool:
        return self._enqueue_task(TelemetryIoTask(
            device_sn=device_sn,
            osd_data=copy.deepcopy(osd_data or {}),
            persist_database=persist_database,
            history_source=history_source,
        ))

    def enqueue_aircraft_osd_log(self, osd_data: dict, *, source: str = "flight_controller") -> bool:
        return self._enqueue_task(TelemetryIoTask(
            device_sn="",
            osd_data=copy.deepcopy(osd_data or {}),
            persist_database=False,
            history_source=None,
            osd_log_source=source,
        ))

    def _run(self) -> None:
        while self._running or not self._queue.empty():
            try:
                task = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if task is None:
                self._queue.task_done()
                continue
            try:
                self._write_task(task)
            finally:
                self._queue.task_done()

    def _write_task(self, task: TelemetryIoTask) -> None:
        if task.osd_log_source:
            drone_report_logger.info(
                "aircraft_osd source=%s payload=%s",
                task.osd_log_source,
                format_with_labels(task.osd_data, max_length=5000),
                extra={"console_silent": True},
            )

        if task.persist_database:
            try:
                with self._session_factory() as session:
                    TelemetryDB.save_telemetry_sync(session, task.device_sn, task.osd_data)
                    session.commit()
            except Exception as exc:
                logger.warning(
                    "遥测数据库异步写入失败: device_sn=%s, error=%s",
                    task.device_sn,
                    exc,
                    exc_info=True,
                )

        if task.history_source and self._history_logger:
            try:
                self._history_logger.record_flight_telemetry(
                    task.device_sn,
                    task.osd_data,
                    source=task.history_source,
                )
            except Exception as exc:
                logger.warning(
                    "遥测历史日志异步写入失败: device_sn=%s, source=%s, error=%s",
                    task.device_sn,
                    task.history_source,
                    exc,
                    exc_info=True,
                )

    def _enqueue_task(self, task: TelemetryIoTask) -> bool:
        if not self._running:
            self._write_task(task)
            return True
        try:
            self._queue.put_nowait(task)
            return True
        except queue.Full:
            self._drop_oldest_pending()
            try:
                self._queue.put_nowait(task)
                return True
            except queue.Full:
                self._record_drop()
                return False

    def _drop_oldest_pending(self) -> None:
        try:
            dropped = self._queue.get_nowait()
            self._queue.task_done()
            if dropped is not None:
                self._record_drop()
        except queue.Empty:
            return

    def _record_drop(self) -> None:
        self._dropped_count += 1
        if self._dropped_count >= self._drop_log_threshold:
            logger.warning(
                "遥测本地 I/O 队列已满，丢弃最旧待写入记录: dropped=%s max_queue=%s",
                self._dropped_count,
                self._queue.maxsize,
            )
            self._drop_log_threshold *= 2
