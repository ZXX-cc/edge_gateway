"""
Local JSONL history logger for raw telemetry and device state snapshots.

The gateway keeps this lightweight file history alongside the database so
field troubleshooting can inspect the original values even after DB retention
cleanup has compacted runtime records.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.utils.config import Config
from src.utils.logger import get_logger


logger = get_logger("local_history")


class LocalHistoryLogger:
    """Append local telemetry/state history records and clean expired files."""

    def __init__(self) -> None:
        self.enabled = bool(Config.get("local_history.enabled", True))
        self.root_dir = Path(str(Config.get("local_history.dir", "logs/history") or "logs/history"))
        self.flight_telemetry_days = int(Config.get("local_history.flight_telemetry_days", 7) or 7)
        self.device_state_days = int(Config.get("local_history.device_state_days", 7) or 7)

    def record_flight_telemetry(self, device_sn: str, telemetry: dict[str, Any], *, source: str = "") -> None:
        """Append one raw flight telemetry sample."""
        if not self.enabled:
            return
        record = {
            "timestamp": int(time.time() * 1000),
            "device_sn": str(device_sn),
            "source": str(source or ""),
            "telemetry": self._json_safe(telemetry),
        }
        self._append_jsonl("flight_telemetry", record)

    def record_device_state_change(self, device_sn: str, state: dict[str, Any], *, source: str = "") -> None:
        """Append one device state snapshot/change record."""
        if not self.enabled:
            return
        record = {
            "timestamp": int(time.time() * 1000),
            "device_sn": str(device_sn),
            "source": str(source or ""),
            "state": self._json_safe(state),
        }
        self._append_jsonl("device_state", record)

    def cleanup_expired(self) -> dict[str, int]:
        """Delete expired daily JSONL files and return deletion counts."""
        return {
            "flight_telemetry_history": self._cleanup_category(
                "flight_telemetry", self.flight_telemetry_days
            ),
            "device_state_history": self._cleanup_category(
                "device_state", self.device_state_days
            ),
        }

    def _append_jsonl(self, category: str, record: dict[str, Any]) -> None:
        try:
            path = self.root_dir / category / f"{datetime.now():%Y-%m-%d}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception as exc:
            logger.warning("Failed to append local history: category=%s error=%s", category, exc, exc_info=True)

    def _cleanup_category(self, category: str, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        directory = self.root_dir / category
        if not directory.exists():
            return 0

        cutoff = datetime.now().date() - timedelta(days=retention_days)
        deleted = 0
        for path in directory.glob("*.jsonl"):
            file_date = self._date_from_filename(path)
            if file_date is None or file_date >= cutoff:
                continue
            try:
                path.unlink()
                deleted += 1
            except Exception as exc:
                logger.warning("Failed to delete expired local history file: path=%s error=%s", path, exc)
        return deleted

    @staticmethod
    def _date_from_filename(path: Path):
        try:
            return datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            return None

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._json_safe(item) for item in value]
        return str(value)
