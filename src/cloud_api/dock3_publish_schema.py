"""Dock3 upstream MQTT publish schema guardrails."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple

from src.cloud_api.dock3_cmd import TASK_STATUSES
from src.cloud_api.dock3_properties import official_property_names


ENVELOPE_FIELDS = frozenset({"bid", "tid", "timestamp", "gateway", "data", "method", "need_reply"})
DOCK_OSD_FIELDS = frozenset(official_property_names(push_mode=0)) | frozenset({
    # Dock3 payload topology currently uses this camera/gimbal index in OSD.
    "type_subtype_gimbalindex",
})
STATE_FIELDS = frozenset(official_property_names(push_mode=1))
AIRCRAFT_OSD_FIELDS = frozenset({
    "latitude",
    "longitude",
    "height",
    "altitude",
    "horizontal_speed",
    "vertical_speed",
    "attitude_pitch",
    "attitude_roll",
    "attitude_head",
    "wind_speed",
    "course",
    "battery",
    "position_state",
    "wind_direction",
    "gimbal_pitch",
    "gimbal_roll",
    "gimbal_yaw",
    "obstacle_state",
    "gear",
    "flight_time",
    "timestamp",
    "mode_code",
    "height_limit",
    "distance_limit",
    "cameras",
})

EVENT_INTERNAL_FIELDS = frozenset({
    "software_pipeline",
    "viewlink_result",
    "simulated",
    "adapter",
    "internal",
    "debug",
    "raw",
    "reply",
    "ack",
    "ordinary_order_response",
    "takeoff_land_order_response",
    "frame_hex",
    "reply_hex",
})


def sanitize_upstream_publish(topic: str, payload: dict, gateway_sn: str) -> Tuple[dict, List[str]]:
    """Strip OSD/state/events payloads to the Dock3 upstream publish surface."""
    if not isinstance(payload, dict):
        return payload, []
    if topic.endswith("/osd"):
        return sanitize_osd_publish(topic, payload, gateway_sn)
    if topic.endswith("/state"):
        return sanitize_state_publish(payload)
    if topic.endswith("/events"):
        return sanitize_event_publish(payload)
    return payload, []


def sanitize_osd_publish(topic: str, payload: dict, gateway_sn: str) -> Tuple[dict, List[str]]:
    sanitized = _sanitize_envelope(payload, include_method=False)
    data = sanitized.get("data")
    if not isinstance(data, dict):
        sanitized["data"] = {}
        return sanitized, ["data"]

    topic_sn = _topic_device_sn(topic)
    allowlist = DOCK_OSD_FIELDS if topic_sn == gateway_sn else AIRCRAFT_OSD_FIELDS
    sanitized_data, removed = _allowlist_dict(data, allowlist, prefix="data")
    sanitized["data"] = sanitized_data
    removed.extend(_removed_envelope_fields(payload, sanitized))
    return sanitized, sorted(set(removed))


def sanitize_state_publish(payload: dict) -> Tuple[dict, List[str]]:
    sanitized = _sanitize_envelope(payload, include_method=False)
    data = sanitized.get("data")
    if (
        isinstance(data, dict)
        and set(data.keys()) == {"property"}
        and isinstance(data.get("property"), dict)
    ):
        data = data["property"]
    if not isinstance(data, dict):
        sanitized["data"] = {}
        return sanitized, ["data"]

    sanitized_data, removed = _allowlist_dict(data, STATE_FIELDS, prefix="data")
    sanitized["data"] = sanitized_data
    removed.extend(_removed_envelope_fields(payload, sanitized))
    return sanitized, sorted(set(removed))


def sanitize_event_publish(payload: dict) -> Tuple[dict, List[str]]:
    sanitized = _sanitize_envelope(payload, include_method=True)
    data = sanitized.get("data")
    if not isinstance(data, dict):
        sanitized["data"] = {}
        return sanitized, ["data"]

    sanitized_data, removed = _strip_internal_event_fields(data, prefix="data")
    _sanitize_event_status(sanitized_data)
    sanitized["data"] = sanitized_data
    removed.extend(_removed_envelope_fields(payload, sanitized))
    return sanitized, sorted(set(removed))


def _sanitize_envelope(payload: dict, *, include_method: bool) -> Dict[str, Any]:
    allowed = {"bid", "tid", "timestamp", "gateway", "data"}
    if include_method:
        allowed.add("method")
        if payload.get("need_reply") in (0, 1):
            allowed.add("need_reply")
    return {key: deepcopy(payload[key]) for key in allowed if key in payload}


def _allowlist_dict(data: Dict[str, Any], allowlist, *, prefix: str) -> Tuple[Dict[str, Any], List[str]]:
    sanitized: Dict[str, Any] = {}
    removed: List[str] = []
    for key, value in data.items():
        if key in allowlist:
            sanitized[key] = deepcopy(value)
        else:
            removed.append(f"{prefix}.{key}")
    return sanitized, removed


def _strip_internal_event_fields(value: Any, *, prefix: str) -> Tuple[Any, List[str]]:
    if isinstance(value, dict):
        output: Dict[str, Any] = {}
        removed: List[str] = []
        for key, child in value.items():
            child_path = f"{prefix}.{key}"
            if key in EVENT_INTERNAL_FIELDS:
                removed.append(child_path)
                continue
            cleaned, child_removed = _strip_internal_event_fields(child, prefix=child_path)
            output[key] = cleaned
            removed.extend(child_removed)
        return output, removed
    if isinstance(value, list):
        output_list = []
        removed: List[str] = []
        for index, item in enumerate(value):
            cleaned, child_removed = _strip_internal_event_fields(item, prefix=f"{prefix}[{index}]")
            output_list.append(cleaned)
            removed.extend(child_removed)
        return output_list, removed
    return deepcopy(value), []


def _sanitize_event_status(data: Dict[str, Any]) -> None:
    output = data.get("output")
    if not isinstance(output, dict):
        return
    status = output.get("status")
    if status is not None and status not in TASK_STATUSES:
        output["status"] = "failed"


def _removed_envelope_fields(original: dict, sanitized: dict) -> List[str]:
    return [
        key for key in original
        if key not in sanitized and key not in ENVELOPE_FIELDS
    ]


def _topic_device_sn(topic: str) -> str:
    parts = str(topic or "").split("/")
    if len(parts) >= 3 and parts[0] == "thing" and parts[1] == "product":
        return parts[2]
    return ""
