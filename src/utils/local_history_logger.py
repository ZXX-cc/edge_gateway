"""本地历史日志落盘工具。"""
from __future__ import annotations

import json
import gzip
import re
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.utils.config import Config
from src.utils.logger import get_logger


logger = get_logger("history")
HISTORY_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[_-](\d{2}))?")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


class LocalHistoryLogger:
    """把原始遥测和设备状态变更保存为本地 JSONL 文件。"""

    def __init__(self):
        self.enabled = bool(Config.get("local_history.enabled", True))
        self.base_dir = Path(Config.get("local_history.dir", "logs/history"))
        self.flight_telemetry_days = int(Config.get("local_history.flight_telemetry_days", 7))
        self.device_state_days = int(Config.get("local_history.device_state_days", 7))
        self.flight_telemetry_granularity = str(
            Config.get("local_history.flight_telemetry_granularity", "hour")
        ).lower()
        self.device_state_granularity = str(
            Config.get("local_history.device_state_granularity", "day")
        ).lower()
        self.compress_after_days = int(Config.get("local_history.compress_after_days", 1))
        self.disk_cleanup_enabled = bool(Config.get("local_history.disk_cleanup_enabled", True))
        self._lock = threading.Lock()

    def record_flight_telemetry(self, device_sn: str, telemetry: dict, *, source: str = "osd") -> None:
        """记录一条飞机原始飞行历史遥测。"""
        if not self.enabled:
            return
        payload = {
            "recorded_at": int(time.time() * 1000),
            "device_sn": device_sn,
            "source": source,
            "telemetry": _json_safe(telemetry or {}),
        }
        self._append_jsonl("flight_telemetry", payload)

    def record_device_state_change(self, device_sn: str, state: dict, *, source: str = "state") -> None:
        """记录一条设备状态变更。"""
        if not self.enabled:
            return
        payload = {
            "recorded_at": int(time.time() * 1000),
            "device_sn": device_sn,
            "source": source,
            "state": _json_safe(state or {}),
        }
        self._append_jsonl("device_state", payload)

    def cleanup_expired(self) -> dict[str, int]:
        """删除超过保留天数的历史日志文件。"""
        if not self.enabled:
            return {
                "flight_telemetry_history": 0,
                "device_state_history": 0,
                "flight_telemetry_history_compressed": 0,
                "device_state_history_compressed": 0,
            }
        flight_deleted, flight_compressed = self._cleanup_category(
            "flight_telemetry", self.flight_telemetry_days
        )
        state_deleted, state_compressed = self._cleanup_category(
            "device_state", self.device_state_days
        )
        return {
            "flight_telemetry_history": flight_deleted,
            "device_state_history": state_deleted,
            "flight_telemetry_history_compressed": flight_compressed,
            "device_state_history_compressed": state_compressed,
        }

    def _append_jsonl(self, category: str, payload: dict) -> None:
        try:
            target = self._history_file_path(category, datetime.now())
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            with self._lock:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8", newline="\n") as file:
                    file.write(line + "\n")
        except Exception as exc:
            logger.warning("本地历史日志写入失败: category=%s, error=%s", category, exc, exc_info=True)

    def cleanup_for_disk_pressure(self, target_usage_percent: float = 75.0) -> dict[str, int]:
        """磁盘水位过高时按旧到新删除历史文件。"""
        if not self.enabled or not self.disk_cleanup_enabled or not self.base_dir.exists():
            return {"history_disk_pressure_deleted": 0, "history_disk_pressure_freed_bytes": 0}
        deleted = 0
        freed = 0
        for path in self._iter_history_files():
            if self._disk_usage_percent() <= target_usage_percent:
                break
            try:
                size = path.stat().st_size
                path.unlink()
                deleted += 1
                freed += size
            except OSError as exc:
                logger.warning("本地历史日志磁盘兜底清理失败: path=%s, error=%s", path, exc)
        return {
            "history_disk_pressure_deleted": deleted,
            "history_disk_pressure_freed_bytes": freed,
        }

    def _cleanup_category(self, category: str, days_to_keep: int) -> tuple[int, int]:
        if days_to_keep <= 0:
            return 0, 0
        root = self._category_dir(category)
        if not root.exists():
            return 0, 0

        cutoff = datetime.now().date() - timedelta(days=days_to_keep)
        compress_cutoff = datetime.now().date() - timedelta(days=self.compress_after_days)
        deleted = 0
        compressed = 0
        for path in self._iter_history_files(root):
            file_date = self._file_date(path)
            if not file_date:
                continue
            if file_date >= cutoff:
                if path.name.endswith(".jsonl") and file_date < compress_cutoff:
                    compressed += 1 if self._compress_jsonl(path) else 0
                continue
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning("本地历史日志清理失败: path=%s, error=%s", path, exc)
        return deleted, compressed

    def _category_dir(self, category: str) -> Path:
        return self.base_dir / category

    def _history_file_path(self, category: str, now: datetime) -> Path:
        granularity = self._granularity_for_category(category)
        if granularity == "hour":
            filename = f"{now:%Y-%m-%d_%H}.jsonl"
        else:
            filename = f"{now:%Y-%m-%d}.jsonl"
        return self._category_dir(category) / f"{now:%Y-%m}" / filename

    def _granularity_for_category(self, category: str) -> str:
        if category == "flight_telemetry":
            return "hour" if self.flight_telemetry_granularity == "hour" else "day"
        if category == "device_state":
            return "hour" if self.device_state_granularity == "hour" else "day"
        return "day"

    def _iter_history_files(self, root: Path | None = None) -> list[Path]:
        root = root or self.base_dir
        files = [
            path
            for path in root.rglob("*")
            if path.is_file() and (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz"))
        ]
        return sorted(files, key=lambda path: path.stat().st_mtime if path.exists() else 0)

    @staticmethod
    def _file_date(path: Path):
        match = HISTORY_DATE_RE.search(path.name)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            return None

    @staticmethod
    def _compress_jsonl(path: Path) -> bool:
        gz_path = path.with_name(path.name + ".gz")
        if gz_path.exists():
            path.unlink(missing_ok=True)
            return False
        try:
            with path.open("rb") as source, gzip.open(gz_path, "wb") as target:
                shutil.copyfileobj(source, target)
            path.unlink()
            return True
        except OSError:
            if gz_path.exists():
                gz_path.unlink(missing_ok=True)
            return False

    def _disk_usage_percent(self) -> float:
        probe = self.base_dir
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        usage = shutil.disk_usage(str(probe))
        return (usage.used / usage.total) * 100 if usage.total else 0.0
