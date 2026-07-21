"""DJI Dock 3 live protocol helpers."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple


TASK_STATUS_FAILED = "failed"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_OK = "ok"
TASK_STATUS_REJECTED = "rejected"

SUPPORTED_URL_TYPES = {0, 1, 3, 4}
SUPPORTED_QUALITIES = {0, 1, 2, 3, 4}
SUPPORTED_VIDEO_TYPES = {"normal", "wide", "zoom", "ir", "fpv", "gimbal"}
SUPPORTED_CAMERA_POSITIONS = {0, 1}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_mock_live_url(url: str) -> bool:
    return str(url or "").startswith("mock://")


def validate_live_start_payload(data: Any) -> Tuple[bool, Dict[str, Any], List[str]]:
    if not isinstance(data, dict):
        return False, {}, ["live_start data must be object"]

    errors: List[str] = []
    url = str(data.get("url", "") or "")
    video_id = str(data.get("video_id", "") or "")
    url_type = data.get("url_type", 4)
    video_quality = data.get("video_quality", data.get("quality", 0))
    video_type = str(data.get("video_type", data.get("lens", "")) or "")

    if not url:
        errors.append("live_start missing url")
    if not video_id:
        errors.append("live_start missing video_id")
    if not _is_int(url_type) or url_type not in SUPPORTED_URL_TYPES:
        errors.append("live_start.url_type must be int enum 0/1/3/4")
    if not _is_int(video_quality) or video_quality not in SUPPORTED_QUALITIES:
        errors.append("live_start.video_quality must be int enum 0..4")
    if video_type and video_type not in SUPPORTED_VIDEO_TYPES:
        errors.append("live_start.video_type unsupported")

    normalized = {
        "url": url,
        "video_id": video_id,
        "url_type": int(url_type) if _is_int(url_type) else 4,
        "video_quality": int(video_quality) if _is_int(video_quality) else 0,
        "video_type": video_type if video_type in SUPPORTED_VIDEO_TYPES else "",
    }
    return not errors, normalized, errors


def validate_live_quality_payload(data: Any) -> Tuple[bool, Dict[str, Any], List[str]]:
    if not isinstance(data, dict):
        return False, {}, ["live_set_quality data must be object"]
    video_id = str(data.get("video_id", "") or "")
    quality = data.get("video_quality", data.get("quality", 0))
    errors: List[str] = []
    if not video_id:
        errors.append("live_set_quality missing video_id")
    if not _is_int(quality) or quality not in SUPPORTED_QUALITIES:
        errors.append("live_set_quality.video_quality must be int enum 0..4")
    return not errors, {
        "video_id": video_id,
        "video_quality": int(quality) if _is_int(quality) else 0,
    }, errors


def validate_live_lens_payload(data: Any) -> Tuple[bool, Dict[str, Any], List[str]]:
    if not isinstance(data, dict):
        return False, {}, ["live_lens_change data must be object"]
    video_id = str(data.get("video_id", "") or "")
    video_type = str(data.get("video_type", data.get("lens", "normal")) or "normal")
    errors: List[str] = []
    if video_type not in SUPPORTED_VIDEO_TYPES:
        errors.append("live_lens_change.video_type unsupported")
    return not errors, {
        "video_id": video_id,
        "video_type": video_type if video_type in SUPPORTED_VIDEO_TYPES else "normal",
    }, errors


def validate_live_camera_payload(data: Any) -> Tuple[bool, Dict[str, Any], List[str]]:
    if not isinstance(data, dict):
        return False, {}, ["live_camera_change data must be object"]
    video_id = str(data.get("video_id", "") or "")
    camera_position = data.get("camera_position")
    errors: List[str] = []
    if not video_id:
        errors.append("live_camera_change missing video_id")
    if not _is_int(camera_position) or camera_position not in SUPPORTED_CAMERA_POSITIONS:
        errors.append("live_camera_change.camera_position must be int enum 0/1")
    return not errors, {
        "video_id": video_id,
        "camera_position": int(camera_position) if _is_int(camera_position) else 0,
    }, errors


def live_task_output(status: str = TASK_STATUS_OK, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data: Dict[str, Any] = {"output": {"status": status}}
    if extra:
        data.update(extra)
    return data


def live_status_event_data(
    *,
    status: str,
    video_id: str,
    session_id: str = "",
    url_type: int = 4,
    video_quality: int = 0,
    video_type: str = "normal",
    url: str = "",
    reason: str = "",
    source: str = "",
) -> Dict[str, Any]:
    data = live_task_output(status, {
        "video_id": video_id,
        "session_id": session_id,
        "url_type": url_type,
        "video_quality": video_quality,
        "video_type": video_type,
        "timestamp": int(time.time() * 1000),
    })
    if url:
        data["url"] = url
    if reason:
        data["reason"] = reason
    if source:
        data["source"] = source
    data["result"] = 0 if status in {TASK_STATUS_OK, TASK_STATUS_IN_PROGRESS} else 1
    return data


def live_capability_data() -> Dict[str, Any]:
    return live_task_output(TASK_STATUS_OK, {
        "supported_protocols": ["webrtc", "rtmp", "rtsp"],
        "supported_url_types": sorted(SUPPORTED_URL_TYPES),
        "supported_resolutions": ["720p", "1080p", "4k"],
        "supported_video_sources": sorted(SUPPORTED_VIDEO_TYPES),
        "supported_qualities": sorted(SUPPORTED_QUALITIES),
        "max_bitrate": 8000,
        "max_framerate": 60,
    })
