"""DJI Dock 3 media protocol helpers."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".dng"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
FOLDER_ARCHIVE_EXTENSIONS = {".zip"}


def stable_file_id(path: str) -> str:
    """Build a deterministic local file id without leaking absolute paths."""
    resolved = str(Path(path).resolve())
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:24]


def stable_url_file_id(url: str) -> str:
    """Build a deterministic id for remote media URLs."""
    return hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:24]


def media_type_for_file(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in PHOTO_EXTENSIONS:
        return "photo"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in FOLDER_ARCHIVE_EXTENSIONS:
        return "folder"
    return "unknown"


def normalize_page(data: Any) -> Tuple[int, int]:
    if not isinstance(data, dict):
        return 1, 20
    try:
        page = int(data.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(data.get("page_size", data.get("pageSize", 20)))
    except (TypeError, ValueError):
        page_size = 20
    return max(1, page), max(1, min(200, page_size))


def normalize_time_range(data: Any) -> Tuple[int, int]:
    if not isinstance(data, dict):
        return 0, 0
    start = data.get("start_time", data.get("begin_time", 0))
    end = data.get("end_time", 0)
    try:
        start_ms = int(start or 0)
    except (TypeError, ValueError):
        start_ms = 0
    try:
        end_ms = int(end or 0)
    except (TypeError, ValueError):
        end_ms = 0
    return max(0, start_ms), max(0, end_ms)


def normalize_requested_media_type(data: Any) -> str:
    if not isinstance(data, dict):
        return "all"
    raw = data.get("media_type", data.get("file_type", "all"))
    if isinstance(raw, int):
        return {0: "photo", 1: "video", 2: "all"}.get(raw, "all")
    value = str(raw or "all").lower()
    if value in {"image", "photo", "picture"}:
        return "photo"
    if value in {"video", "movie"}:
        return "video"
    return "all"


def build_media_file(path: str, storage_root: str, device_sn: str) -> Dict[str, Any]:
    stat = os.stat(path)
    file_id = stable_file_id(path)
    filename = os.path.basename(path)
    ext = Path(path).suffix.lower().lstrip(".")
    media_type = media_type_for_file(path)
    create_time = int(stat.st_mtime * 1000)
    relative_path = os.path.relpath(path, storage_root)
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    return {
        "file_id": file_id,
        "fingerprint": file_id,
        "device_sn": device_sn,
        "module": "media",
        "name": filename,
        "file_name": filename,
        "path": relative_path,
        "object_key": relative_path.replace(os.sep, "/"),
        "size": int(stat.st_size),
        "file_size": int(stat.st_size),
        "media_type": media_type,
        "file_type": ext,
        "mime_type": mime_type,
        "create_time": create_time,
        "capture_time": create_time,
        "upload_status": "pending",
    }


def build_remote_media_file(url: str, device_sn: str, *, size: int = 0, create_time: int = 0) -> Dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    filename = urllib.parse.unquote(Path(parsed.path).name) or "remote_media"
    ext = Path(filename).suffix.lower().lstrip(".")
    media_type = media_type_for_file(filename)
    file_id = stable_url_file_id(url)
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    timestamp = int(create_time or time.time() * 1000)

    return {
        "file_id": file_id,
        "fingerprint": file_id,
        "device_sn": device_sn,
        "module": "media",
        "name": filename,
        "file_name": filename,
        "path": url,
        "object_key": parsed.path.lstrip("/"),
        "size": int(size or 0),
        "file_size": int(size or 0),
        "media_type": media_type,
        "file_type": ext,
        "mime_type": mime_type,
        "create_time": timestamp,
        "capture_time": timestamp,
        "upload_status": "remote",
        "download_url": url,
        "source": "remote_http",
    }


def filter_media_files(files: Iterable[Dict[str, Any]], data: Any) -> List[Dict[str, Any]]:
    requested_type = normalize_requested_media_type(data)
    start_ms, end_ms = normalize_time_range(data)
    filtered: List[Dict[str, Any]] = []
    for item in files:
        if requested_type != "all" and item.get("media_type") != requested_type:
            continue
        create_time = item.get("create_time", 0)
        if start_ms and create_time < start_ms:
            continue
        if end_ms and create_time > end_ms:
            continue
        filtered.append(item)
    filtered.sort(key=lambda item: item.get("create_time", 0), reverse=True)
    return filtered


def paginate(items: List[Dict[str, Any]], page: int, page_size: int) -> List[Dict[str, Any]]:
    offset = (page - 1) * page_size
    return items[offset:offset + page_size]


def file_list_data(files: List[Dict[str, Any]], page: int, page_size: int, total: int) -> Dict[str, Any]:
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "files": files,
        "list": files,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
        },
    }


def normalize_delete_ids(data: Any) -> Tuple[List[str], List[str]]:
    if not isinstance(data, dict):
        return [], ["file_delete data must be object"]

    raw_files = data.get("files")
    raw_ids = data.get("file_ids", data.get("file_id", []))
    ids: List[str] = []

    if isinstance(raw_files, list):
        for item in raw_files:
            if isinstance(item, dict):
                value = item.get("file_id") or item.get("fingerprint") or item.get("name")
            else:
                value = item
            if value:
                ids.append(str(value))

    if isinstance(raw_ids, list):
        ids.extend(str(item) for item in raw_ids if item)
    elif raw_ids:
        ids.append(str(raw_ids))

    ids = list(dict.fromkeys(ids))
    if not ids:
        return [], ["file_delete missing file_ids/files"]
    return ids, []


def normalize_priority_items(data: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not isinstance(data, dict):
        return [], ["adjust_upload_priority data must be object"]
    raw_files = data.get("files", [])
    if not isinstance(raw_files, list) or not raw_files:
        return [], ["adjust_upload_priority missing files"]

    normalized: List[Dict[str, Any]] = []
    errors: List[str] = []
    for index, item in enumerate(raw_files):
        if not isinstance(item, dict):
            errors.append(f"files[{index}] must be object")
            continue
        file_id = item.get("file_id") or item.get("fingerprint") or item.get("name")
        priority = item.get("priority", item.get("upload_priority", 0))
        try:
            priority_value = int(priority)
        except (TypeError, ValueError):
            errors.append(f"files[{index}].priority must be int")
            continue
        if not file_id:
            errors.append(f"files[{index}] missing file_id")
            continue
        normalized.append({
            "file_id": str(file_id),
            "priority": max(0, priority_value),
        })
    return normalized, errors


def media_task_output(status: str = "ok", extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data: Dict[str, Any] = {"output": {"status": status}}
    if extra:
        data.update(extra)
    return data


def upload_progress_event_data(
    files: List[Dict[str, Any]],
    status: str = "ok",
    progress: int = 100,
    result: int = 0,
) -> Dict[str, Any]:
    progress = max(0, min(100, int(progress)))
    event_files = []
    now = int(time.time() * 1000)
    for item in files:
        event_files.append({
            "device_sn": item.get("device_sn", ""),
            "module": "media",
            "key": item.get("object_key") or item.get("path") or item.get("name", ""),
            "fingerprint": item.get("fingerprint") or item.get("file_id", ""),
            "size": item.get("size", item.get("file_size", 0)),
            "progress": {
                "progress": progress,
                "finish_time": now if progress >= 100 else 0,
                "upload_rate": 0,
                "status": status,
                "result": result,
            },
        })
    return {
        "result": result,
        "output": {
            "status": status,
            "ext": {"files": event_files},
        },
    }


def storage_config_output(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    config = config or {}
    credentials = config.get("credentials") if isinstance(config.get("credentials"), dict) else {}
    return {
        "bucket": str(config.get("bucket", "") or ""),
        "credentials": {
            "access_key_id": str(credentials.get("access_key_id", config.get("access_key_id", "")) or ""),
            "access_key_secret": str(credentials.get("access_key_secret", config.get("access_key_secret", "")) or ""),
            "expire": int(credentials.get("expire", config.get("expire", 0)) or 0),
            "security_token": str(credentials.get("security_token", config.get("security_token", "")) or ""),
        },
        "endpoint": str(config.get("endpoint", "") or ""),
        "provider": str(config.get("provider", "minio") or "minio"),
        "region": str(config.get("region", "") or ""),
        "object_key_prefix": str(config.get("object_key_prefix", "") or ""),
    }
