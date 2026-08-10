"""运行日志保留、压缩和磁盘兜底清理工具。"""
from __future__ import annotations

import gzip
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


LOG_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
MONTH_DIR_RE = re.compile(r"^\d{4}-\d{2}$")
DEFAULT_DEBUG_CATEGORIES = {
    "mqtt_trace",
    "gateway_traffic",
    "nest_raw",
    "drone_report",
    "mavlink_raw",
}
DEFAULT_IMPORTANT_CATEGORIES = {"alarm", "important"}


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _resolve_log_dir(config: dict) -> Path:
    base_dir = config.get("dir") or config.get("base_dir")
    if not base_dir:
        legacy_file = config.get("file")
        if legacy_file:
            legacy_path = Path(legacy_file)
            base_dir = legacy_path.parent / (legacy_path.stem or "edge_gateway")
        else:
            base_dir = "logs"
    return Path(str(base_dir)).expanduser()


def _retention_config(config: dict) -> dict:
    retention = config.get("retention", {}) or {}
    debug_categories = retention.get("debug_categories", sorted(DEFAULT_DEBUG_CATEGORIES))
    important_categories = retention.get("important_categories", sorted(DEFAULT_IMPORTANT_CATEGORIES))
    return {
        "enabled": _parse_bool(retention.get("enabled"), True),
        "normal_days": int(retention.get("normal_days", 7) or 7),
        "important_days": int(retention.get("important_days", 30) or 30),
        "debug_days": int(retention.get("debug_days", 3) or 3),
        "compress_after_days": int(retention.get("compress_after_days", 1) or 1),
        "disk_cleanup_enabled": _parse_bool(retention.get("disk_cleanup_enabled"), True),
        "disk_high_watermark_percent": float(retention.get("disk_high_watermark_percent", 80) or 80),
        "disk_target_percent": float(retention.get("disk_target_percent", 75) or 75),
        "debug_categories": {str(item) for item in debug_categories},
        "important_categories": {str(item) for item in important_categories},
    }


def _file_date(path: Path) -> datetime.date | None:
    match = LOG_DATE_RE.search(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _category_for_path(base_dir: Path, path: Path) -> str:
    try:
        relative = path.relative_to(base_dir)
    except ValueError:
        return "unknown"
    if not relative.parts:
        return "unknown"
    first = relative.parts[0]
    if MONTH_DIR_RE.match(first):
        return "gateway"
    return first


def _retention_days_for_category(category: str, policy: dict) -> int:
    if category in policy["important_categories"]:
        return policy["important_days"]
    if category in policy["debug_categories"]:
        return policy["debug_days"]
    return policy["normal_days"]


def _iter_log_files(base_dir: Path):
    if not base_dir.exists():
        return []
    return [
        path
        for path in base_dir.rglob("*")
        if path.is_file() and (path.name.endswith(".log") or path.name.endswith(".log.gz"))
    ]


def _compress_log_file(path: Path) -> bool:
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


def _disk_usage_percent(path: Path) -> float:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(str(probe))
    return (usage.used / usage.total) * 100 if usage.total else 0.0


def _disk_pressure_cleanup(base_dir: Path, policy: dict) -> tuple[int, int]:
    if not policy["disk_cleanup_enabled"] or not base_dir.exists():
        return 0, 0
    if _disk_usage_percent(base_dir) < policy["disk_high_watermark_percent"]:
        return 0, 0

    candidates = []
    for path in _iter_log_files(base_dir):
        category = _category_for_path(base_dir, path)
        if category in policy["important_categories"]:
            priority = 2
        elif category in policy["debug_categories"]:
            priority = 0
        else:
            priority = 1
        try:
            stat = path.stat()
        except OSError:
            continue
        candidates.append((priority, stat.st_mtime, path, stat.st_size))

    deleted = 0
    freed = 0
    for _priority, _mtime, path, size in sorted(candidates, key=lambda item: (item[0], item[1])):
        if _disk_usage_percent(base_dir) <= policy["disk_target_percent"]:
            break
        try:
            path.unlink()
            deleted += 1
            freed += size
        except OSError:
            continue
    return deleted, freed


def cleanup_log_files(config: dict | None = None) -> dict[str, int]:
    """清理运行日志，返回删除/压缩数量。"""
    config = config or {}
    policy = _retention_config(config)
    if not policy["enabled"]:
        return {
            "log_deleted": 0,
            "log_compressed": 0,
            "log_disk_pressure_deleted": 0,
            "log_disk_pressure_freed_bytes": 0,
        }

    base_dir = _resolve_log_dir(config)
    today = datetime.now().date()
    deleted = 0
    compressed = 0
    for path in _iter_log_files(base_dir):
        file_date = _file_date(path)
        if not file_date:
            continue
        category = _category_for_path(base_dir, path)
        retention_days = _retention_days_for_category(category, policy)
        if file_date < today - timedelta(days=retention_days):
            try:
                path.unlink()
                deleted += 1
            except OSError:
                pass
            continue
        if (
            path.name.endswith(".log")
            and file_date < today - timedelta(days=policy["compress_after_days"])
            and _compress_log_file(path)
        ):
            compressed += 1

    pressure_deleted, pressure_freed = _disk_pressure_cleanup(base_dir, policy)
    return {
        "log_deleted": deleted,
        "log_compressed": compressed,
        "log_disk_pressure_deleted": pressure_deleted,
        "log_disk_pressure_freed_bytes": pressure_freed,
    }
