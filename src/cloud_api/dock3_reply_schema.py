"""Dock3 MQTT reply schema guardrails.

The business handlers keep rich internal details for logs and debugging, but
platform replies must stay inside the fields documented by DJI Cloud API.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Set, Tuple

from src.cloud_api.dock3_cmd import TASK_STATUSES as CMD_TASK_STATUSES
from src.cloud_api.dock3_cmd import TASK_STATUS_FAILED, OFFICIAL_DOCK3_CMD_METHODS
from src.cloud_api.dock3_properties import official_property_names


PROJECT_CMD_METHODS = frozenset({
    "putter_open",
    "putter_close",
})

CMD_STATUS_REPLY_METHODS = OFFICIAL_DOCK3_CMD_METHODS | PROJECT_CMD_METHODS

STATUS_ONLY_SERVICE_METHODS = frozenset({
    "ota_create",
    "ota_cancel",
    "config_update",
    "config_get",
    "config_rollback",
    "one_key_takeoff_flow",
    "flighttask_prepare",
    "flighttask_execute",
    "flighttask_undo",
    "flighttask_stop",
    "flighttask_progress",
    "flighttask_progress_get",
    "flighttask_pause",
    "flighttask_resume",
    "resume_wayline_task",
    "flight_setup_abort",
    "flighttask_create",
    "flighttask_recovery",
    "return_home",
    "return_home_cancel",
    "return_specific_home",
    "flighttask_resource_get",
    "in_flight_wayline_deliver",
    "in_flight_wayline_stop",
    "in_flight_wayline_recover",
    "in_flight_wayline_cancel",
    "drc_mode_enter",
    "drc_mode_exit",
    "flight_authority_grab",
    "takeoff_to_point",
    "fly_to_point",
    "fly_to_point_stop",
    "fly_to_point_update",
    "fileupload_start",
    "fileupload_update",
    "adjust_upload_priority",
    "file_delete",
    "file_download",
    "upload_flighttask_media_prioritize",
    "highest_priority_upload_flighttask_media",
    "live_start",
    "live_start_push",
    "live_stop",
    "live_stop_push",
    "live_camera_change",
    "live_lens_change",
    "live_set_quality",
})

RESULT_ONLY_SERVICE_METHODS = frozenset({
    "gimbalRotate",
    "camera_screen_drag",
    "drc_light_calibration",
    "drc_light_fine_tuning_set",
    "drc_speaker_tts_set",
    "drc_speaker_play_volume_set",
    "payload_authority_grab",
    "payload_authority_release",
    "psdk_ui_resource_upload_result",
    "psdk_floating_window_text",
    "psdk_widget_value_set",
    "psdk_input_box_text_set",
    "speaker_replay",
    "speaker_play_mode_set",
    "speaker_play_stop",
    "speaker_audio_play_start",
    "speaker_tts_play_start",
    "speaker_play_volume_set",
    "camera_frame_zoom",
    "camera_mode_switch",
    "camera_photo_take",
    "camera_photo_stop",
    "camera_recording_start",
    "camera_recording_stop",
    "camera_focal_length_set",
    "camera_exposure_set",
    "camera_focus_mode_set",
    "camera_focus_value_set",
    "camera_point_focus_action",
    "camera_exposure_mode_set",
    "camera_aim",
    "camera_screen_split",
    "gimbal_reset",
    "camera_look_at",
    "photo_storage_set",
    "video_storage_set",
    "ir_metering_mode_set",
    "ir_metering_point_set",
    "ir_metering_area_set",
    "flight_areas_update",
    "custom_data_transmission_from_esdk",
    "custom_data_transmission_to_esdk",
    "custom_data_transmission_from_psdk",
    "custom_data_transmission_to_psdk",
    "unlock_license_update",
    "unlock_license_switch",
    "hms_clear",
})

TOP_LEVEL_FIELD_ALLOWLIST_BY_METHOD: Dict[str, Set[str]] = {
    "gateway_state_query": {"state", "ready"},
    "fileupload_list": {"total", "page", "page_size", "module_list", "logs", "list"},
    "device_log_list_get": {"total", "page", "page_size", "module_list", "logs", "list"},
    "device_log_upload": {"upload_task_id", "accepted_count", "missing_ids", "module_list"},
    "hms_get": {"alarms", "list", "total", "page", "page_size", "has_more"},
    "file_list_get": {"total", "page", "page_size", "files", "list", "pagination"},
    "file_download": {"files"},
    "live_capability_query": {
        "supported_protocols",
        "supported_url_types",
        "supported_resolutions",
        "supported_video_sources",
        "supported_qualities",
        "max_bitrate",
        "max_framerate",
        "live_capacity",
    },
    "unlock_license_list": {"device_model_domain", "consistence", "licenses"},
}

OUTPUT_FIELD_ALLOWLIST_BY_METHOD: Dict[str, Set[str]] = {
    method: {"status"} for method in TOP_LEVEL_FIELD_ALLOWLIST_BY_METHOD
}


def _result(data: Dict[str, Any]) -> Any:
    return data.get("result", 0)


def _sanitize_status_output(output: Any) -> Dict[str, Any]:
    output = output if isinstance(output, dict) else {}
    status = output.get("status", TASK_STATUS_FAILED)
    if status not in CMD_TASK_STATUSES:
        status = TASK_STATUS_FAILED
    sanitized: Dict[str, Any] = {"status": status}

    progress = output.get("progress")
    if isinstance(progress, dict):
        progress_out: Dict[str, Any] = {}
        if "percent" in progress:
            try:
                progress_out["percent"] = max(0, min(100, int(progress.get("percent"))))
            except (TypeError, ValueError):
                pass
        if isinstance(progress.get("step_key"), str) and progress.get("step_key"):
            progress_out["step_key"] = progress["step_key"]
        if progress_out:
            sanitized["progress"] = progress_out
    return sanitized


def _sanitize_allowlisted_data(
    method: str,
    data: Dict[str, Any],
    top_level_fields: Set[str],
) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {"result": _result(data)}
    output = data.get("output")
    if isinstance(output, dict):
        output_fields = OUTPUT_FIELD_ALLOWLIST_BY_METHOD.get(
            method,
            {"status"},
        )
        sanitized_output = _sanitize_status_output(output)
        for key in output_fields - {"status", "progress"}:
            if key in output:
                sanitized_output[key] = output[key]
        sanitized["output"] = sanitized_output
    for key in top_level_fields:
        if key in data:
            sanitized[key] = data[key]
    return sanitized


def _top_level_allowlist_for_method(method: str) -> Optional[Set[str]]:
    if method == "property_get":
        return set(official_property_names())
    return TOP_LEVEL_FIELD_ALLOWLIST_BY_METHOD.get(method)


def sanitize_services_reply(reply: dict) -> Tuple[dict, List[str]]:
    """Return a copy stripped to the Dock3 services_reply shape for known methods."""
    if not isinstance(reply, dict):
        return reply, []

    method = str(reply.get("method") or "")
    if not method:
        return reply, []

    data = reply.get("data")
    if not isinstance(data, dict):
        return reply, []

    sanitized = deepcopy(reply)
    original_keys = set(data.keys())
    original_output_keys = set(data.get("output", {}).keys()) if isinstance(data.get("output"), dict) else set()

    top_level_allowlist = _top_level_allowlist_for_method(method)

    if method in RESULT_ONLY_SERVICE_METHODS:
        sanitized["data"] = {"result": _result(data)}
    elif method in CMD_STATUS_REPLY_METHODS or method in STATUS_ONLY_SERVICE_METHODS:
        sanitized["data"] = {
            "output": _sanitize_status_output(data.get("output")),
            "result": _result(data),
        }
    elif top_level_allowlist is not None:
        sanitized["data"] = _sanitize_allowlisted_data(method, data, top_level_allowlist)
    else:
        sanitized["data"] = {"result": _result(data)}

    new_data = sanitized.get("data", {})
    removed = sorted(original_keys - set(new_data.keys()))
    new_output = new_data.get("output") if isinstance(new_data, dict) else None
    new_output_keys = set(new_output.keys()) if isinstance(new_output, dict) else set()
    removed.extend(f"output.{key}" for key in sorted(original_output_keys - new_output_keys))
    return sanitized, removed
