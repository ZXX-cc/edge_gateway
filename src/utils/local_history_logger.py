"""本地历史日志落盘工具。"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.utils.config import Config
from src.utils.logger import get_logger


logger = get_logger("history")


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
            return {"flight_telemetry_history": 0, "device_state_history": 0}
        return {
            "flight_telemetry_history": self._cleanup_category(
                "flight_telemetry", self.flight_telemetry_days
            ),
            "device_state_history": self._cleanup_category(
                "device_state", self.device_state_days
            ),
        }

    def _append_jsonl(self, category: str, payload: dict) -> None:
        try:
            target = self._category_dir(category) / f"{datetime.now():%Y-%m-%d}.jsonl"
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            with self._lock:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as file:
                    file.write(line + "\n")
        except Exception as exc:
            logger.warning("本地历史日志写入失败: category=%s, error=%s", category, exc, exc_info=True)

    def _cleanup_category(self, category: str, days_to_keep: int) -> int:
        if days_to_keep <= 0:
            return 0
        root = self._category_dir(category)
        if not root.exists():
            return 0

        cutoff = datetime.now().date() - timedelta(days=days_to_keep)
        deleted = 0
        for path in root.glob("*.jsonl"):
            try:
                file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_date >= cutoff:
                continue
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning("本地历史日志清理失败: path=%s, error=%s", path, exc)
        return deleted

    def _category_dir(self, category: str) -> Path:
        return self.base_dir / category
