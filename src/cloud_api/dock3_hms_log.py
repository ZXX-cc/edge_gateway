"""DJI Dock 3 HMS and remote-log protocol helpers.

The gateway can validate HMS/log cloud contracts before a real log uploader or
hardware fault source is attached.  These helpers keep payload shape,
pagination, stable ids, and fileupload progress events consistent across
device and log managers.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


TASK_STATUS_CANCELED = "canceled"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_OK = "ok"
TASK_STATUS_REJECTED = "rejected"

DEFAULT_LOG_MODULES = (0, 3)
LOG_EXTENSIONS = {".log", ".jsonl", ".txt", ".out", ".err"}


def task_output(status: str = TASK_STATUS_OK, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data: Dict[str, Any] = {"output": {"status": status}}
    if extra:
        data.update(extra)
    return data


def normalize_page(data: Any, default_size: int = 20, max_size: int = 200) -> Tuple[int, int]:
    if not isinstance(data, dict):
        return 1, default_size
    try:
        page = int(data.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(data.get("page_size", data.get("pageSize", default_size)))
    except (TypeError, ValueError):
        page_size = default_size
    return max(1, page), max(1, min(max_size, page_size))


def paginate(items: List[Dict[str, Any]], page: int, page_size: int) -> List[Dict[str, Any]]:
    start = (page - 1) * page_size
    return items[start:start + page_size]


def normalize_module_list(data: Any, default: Iterable[int] = DEFAULT_LOG_MODULES) -> List[int]:
    if not isinstance(data, dict):
        return list(default)
    raw = data.get("module_list", data.get("modules", data.get("module", list(default))))
    if raw in (None, ""):
        return list(default)
    if not isinstance(raw, list):
        raw = [raw]

    modules: List[int] = []
    for item in raw:
        try:
            module = int(item)
        except (TypeError, ValueError):
            continue
        if module not in modules:
            modules.append(module)
    return modules or list(default)


def stable_id(value: str, length: int = 24) -> str:
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:length]


def normalize_alarm_ids(data: Any) -> List[str]:
    if not isinstance(data, dict):
        return []
    raw = data.get("alarm_ids", data.get("alarms", data.get("ids", [])))
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raw = [raw]

    alarm_ids: List[str] = []
    for item in raw:
        if isinstance(item, dict):
            value = item.get("id") or item.get("alarm_id")
        else:
            value = item
        if value:
            alarm_ids.append(str(value))
    return list(dict.fromkeys(alarm_ids))


def normalize_hms_alarm(raw: Any, device_sn: str, index: int = 0) -> Dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    now_ms = int(time.time() * 1000)
    alarm_id = str(
        item.get("id")
        or item.get("alarm_id")
        or f"hms-{device_sn}-{now_ms}-{index}"
    )
    level = item.get("level", item.get("alarm_level", 2))
    try:
        level_value = int(level)
    except (TypeError, ValueError):
        level_value = 2

    create_time = item.get("create_time", item.get("timestamp", now_ms))
    try:
        create_time_value = int(create_time)
    except (TypeError, ValueError):
        create_time_value = now_ms

    hms_key = str(item.get("hms_key") or item.get("key") or "dock3.hms.simulation.warning")
    content = str(item.get("content") or item.get("message") or "Dock3 simulated HMS alarm")
    module = str(item.get("module") or "dock")
    status = str(item.get("status") or "active")

    update_time = item.get("update_time", now_ms)
    try:
        update_time_value = int(update_time or now_ms)
    except (TypeError, ValueError):
        update_time_value = now_ms

    return {
        "id": alarm_id,
        "alarm_id": alarm_id,
        "device_sn": str(item.get("device_sn") or device_sn),
        "domain": str(item.get("domain", "3")),
        "module": module,
        "type": str(item.get("type") or "hms"),
        "level": max(0, level_value),
        "hms_key": hms_key,
        "content": content,
        "message": content,
        "status": status,
        "create_time": create_time_value,
        "update_time": update_time_value,
    }


def normalize_hms_alarms(raw_alarms: Any, device_sn: str) -> List[Dict[str, Any]]:
    if raw_alarms is None:
        raw_alarms = [{}]
    if not isinstance(raw_alarms, list):
        raw_alarms = [raw_alarms]
    return [normalize_hms_alarm(item, device_sn, index) for index, item in enumerate(raw_alarms)]


def hms_event_data(alarms: List[Dict[str, Any]], status: str = TASK_STATUS_OK) -> Dict[str, Any]:
    data = task_output(status, {
        "result": 0,
        "list": alarms,
        "alarms": alarms,
        "total": len(alarms),
    })
    return data


def hms_list_data(
    alarms: List[Dict[str, Any]],
    *,
    page: int,
    page_size: int,
    total: int,
) -> Dict[str, Any]:
    return task_output(TASK_STATUS_OK, {
        "alarms": alarms,
        "list": alarms,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
    })


def resolve_log_root(configured_path: str = "./logs", project_root: str | Path | None = None) -> str:
    root = Path(configured_path or "./logs").expanduser()
    if not root.is_absolute():
        base = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
        root = base / root
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _is_log_candidate(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith("."):
        return False
    return path.suffix.lower() in LOG_EXTENSIONS or "log" in path.name.lower()


def build_log_file(path: str | Path, log_root: str | Path, device_sn: str, module: int = 3) -> Dict[str, Any]:
    filepath = Path(path)
    root = Path(log_root)
    stat = filepath.stat()
    relative = filepath.relative_to(root) if filepath.is_relative_to(root) else Path(filepath.name)
    relative_key = relative.as_posix()
    file_id = stable_id(str(filepath.resolve()))
    update_time = int(stat.st_mtime * 1000)

    return {
        "file_id": file_id,
        "fingerprint": file_id,
        "device_sn": device_sn,
        "module": str(module),
        "name": filepath.name,
        "file_name": filepath.name,
        "object_key": f"logs/{device_sn}/{relative_key}",
        "path": relative_key,
        "boot_index": file_id,
        "start_time": update_time,
        "end_time": update_time,
        "create_time": int(stat.st_ctime * 1000),
        "update_time": update_time,
        "size": int(stat.st_size),
        "file_size": int(stat.st_size),
    }


def scan_log_files(
    log_root: str | Path,
    device_sn: str,
    module: int = 3,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    root = Path(log_root)
    if not root.exists():
        return []

    files: List[Dict[str, Any]] = []
    for path in root.rglob("*"):
        if not _is_log_candidate(path):
            continue
        try:
            files.append(build_log_file(path, root, device_sn, module=module))
        except OSError:
            continue

    files.sort(key=lambda item: item.get("update_time", 0), reverse=True)
    return files[:limit]


def group_fileupload_files(
    log_files: List[Dict[str, Any]],
    modules: Iterable[int],
    device_sn: str,
) -> List[Dict[str, Any]]:
    grouped: List[Dict[str, Any]] = []
    for module in modules:
        module_files = [
            {**item, "module": str(module)}
            for item in log_files
        ]
        grouped.append({
            "device_sn": device_sn,
            "module": str(module),
            "result": 0,
            "list": module_files,
        })
    return grouped


def normalize_fileupload_start_files(data: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not isinstance(data, dict):
        return [], ["fileupload_start data must be object"]
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    raw_files = params.get("files", data.get("files", []))
    if not isinstance(raw_files, list) or not raw_files:
        return [], ["fileupload_start missing params.files"]

    normalized: List[Dict[str, Any]] = []
    errors: List[str] = []
    for file_index, item in enumerate(raw_files):
        if not isinstance(item, dict):
            errors.append(f"files[{file_index}] must be object")
            continue
        module = str(item.get("module", "3"))
        object_key = str(item.get("object_key") or item.get("key") or f"logs/{module}/{file_index}")
        raw_list = item.get("list", [])
        if not isinstance(raw_list, list) or not raw_list:
            errors.append(f"files[{file_index}].list missing")
            continue
        selected = []
        for log_index, log_item in enumerate(raw_list):
            if not isinstance(log_item, dict):
                errors.append(f"files[{file_index}].list[{log_index}] must be object")
                continue
            boot_index = str(log_item.get("boot_index") or log_item.get("file_id") or "")
            if not boot_index:
                errors.append(f"files[{file_index}].list[{log_index}] missing boot_index")
                continue
            selected.append({**log_item, "boot_index": boot_index})
        if selected:
            normalized.append({
                **item,
                "module": module,
                "object_key": object_key,
                "list": selected,
            })

    return normalized, errors


def upload_progress_event_data(
    files: List[Dict[str, Any]],
    *,
    device_sn: str,
    status: str = TASK_STATUS_IN_PROGRESS,
    progress: int = 0,
    result: int = 0,
) -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)
    progress_value = max(0, min(100, int(progress)))
    event_files: List[Dict[str, Any]] = []
    for item in files:
        event_files.append({
            "device_sn": item.get("device_sn") or device_sn,
            "module": str(item.get("module", "3")),
            "key": item.get("object_key") or item.get("key") or item.get("path") or "",
            "fingerprint": item.get("fingerprint") or item.get("file_id") or item.get("boot_index") or "",
            "size": int(item.get("size", item.get("file_size", 0)) or 0),
            "progress": {
                "progress": progress_value,
                "finish_time": now_ms if progress_value >= 100 or status in {TASK_STATUS_OK, TASK_STATUS_CANCELED} else 0,
                "upload_rate": 0,
                "status": status,
                "result": result,
            },
        })

    data = task_output(status, {"result": result})
    data["output"]["ext"] = {"files": event_files}
    return data
