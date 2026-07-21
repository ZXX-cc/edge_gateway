"""Dock3 payload property helpers.

DJI Dock3 represents camera/gimbal payloads with a compact
``type-subtype-gimbalIndex`` string.  The rest of the gateway keeps a richer
diagnostic payload state, so this module is the narrow adapter between both
shapes.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping, Optional


DEFAULT_CAMERA_TYPE = 52
DEFAULT_CAMERA_SUBTYPE = 0
DEFAULT_GIMBAL_INDEX = 0
DEFAULT_VIDEO_INDEX = "normal-0"
DEFAULT_VIDEO_TYPE = "normal"


def payload_index_from_config(config: Optional[Mapping[str, Any]] = None) -> str:
    payload = _payload_cloud_config(config)
    camera_type = _int(payload.get("type"), DEFAULT_CAMERA_TYPE)
    camera_subtype = _int(payload.get("subtype"), DEFAULT_CAMERA_SUBTYPE)
    gimbal_index = _int(payload.get("gimbal_index"), DEFAULT_GIMBAL_INDEX)
    return f"{camera_type}-{camera_subtype}-{gimbal_index}"


def build_gimbal_payload_property(
    payload_state: Optional[Mapping[str, Any]] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the official Dock3 camera/gimbal payload property object."""
    state = dict(payload_state or {})
    gimbal = state.get("gimbal") if isinstance(state.get("gimbal"), dict) else {}
    measure = state.get("measure_target") if isinstance(state.get("measure_target"), dict) else {}
    payload_index = str(
        state.get("payload_index")
        or payload_index_from_config(config)
    )
    return {
        "gimbal_pitch": _angle(gimbal.get("pitch", state.get("gimbal_pitch", 0.0))),
        "gimbal_roll": _angle(gimbal.get("roll", state.get("gimbal_roll", 0.0))),
        "gimbal_yaw": _angle(gimbal.get("yaw", state.get("gimbal_yaw", 0.0))),
        "measure_target_longitude": _bounded_float(
            measure.get("longitude", state.get("measure_target_longitude", 0.0)),
            -180.0,
            180.0,
        ),
        "measure_target_latitude": _bounded_float(
            measure.get("latitude", state.get("measure_target_latitude", 0.0)),
            -90.0,
            90.0,
        ),
        "measure_target_altitude": _float(
            measure.get("altitude", state.get("measure_target_altitude", 0.0)),
            0.0,
        ),
        "measure_target_distance": max(
            0.0,
            _float(measure.get("distance", state.get("measure_target_distance", 0.0)), 0.0),
        ),
        # 3 = NO_SIGNAL. It is the honest default until a real laser/rangefinder
        # adapter supplies target data.
        "measure_target_error_state": _int(
            measure.get("error_state", state.get("measure_target_error_state", 3)),
            3,
        ),
        "payload_index": payload_index,
        "version": _int(state.get("version"), 1),
    }


def build_live_capacity(
    *,
    gateway_sn: str,
    config: Optional[Mapping[str, Any]] = None,
    video_endpoint: str = "",
) -> Dict[str, Any]:
    payload_config = _payload_cloud_config(config)
    camera_index = payload_index_from_config(config)
    video_index = str(payload_config.get("video_index") or DEFAULT_VIDEO_INDEX)
    video_type = str(payload_config.get("video_type") or DEFAULT_VIDEO_TYPE)
    switchable = payload_config.get("switchable_video_types")
    if not isinstance(switchable, list) or not switchable:
        switchable = ["normal", "wide", "zoom", "infrared"]

    camera = {
        "camera_index": camera_index,
        "available_video_number": 1,
        "coexist_video_number_max": 1,
        "video_list": [
            {
                "video_index": video_index,
                "video_type": video_type,
                "switchable_video_types": [str(item) for item in switchable],
            }
        ],
    }
    device = {
        "sn": str(payload_config.get("device_sn") or gateway_sn),
        "available_video_number": 1,
        "coexist_video_number_max": 1,
        "camera_list": [camera],
    }
    if video_endpoint:
        camera["video_list"][0]["endpoint"] = video_endpoint

    return {
        "available_video_number": 1,
        "coexist_video_number_max": 1,
        "device_list": [device],
    }


def with_dock3_payload_index(
    payload_state: Mapping[str, Any],
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    state = deepcopy(dict(payload_state or {}))
    state["payload_index"] = str(state.get("payload_index") or payload_index_from_config(config))
    state["type_subtype_gimbalindex"] = build_gimbal_payload_property(state, config)
    return state


def _payload_cloud_config(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(config, Mapping):
        return {}
    cloud_api = config.get("cloud_api")
    return dict(cloud_api) if isinstance(cloud_api, Mapping) else {}


def _angle(value: Any) -> float:
    number = _float(value, 0.0)
    return round(((number + 180.0) % 360.0) - 180.0, 1)


def _bounded_float(value: Any, minimum: float, maximum: float) -> float:
    return round(max(minimum, min(maximum, _float(value, 0.0))), 7)


def _float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
