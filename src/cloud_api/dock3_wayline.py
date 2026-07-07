"""
DJI Dock 3 wayline / flight task helpers.

The current project only targets a server-side simulation loop.  This module
keeps Dock3 flighttask validation and reply/event payload shape separate from
MissionManager's MAVLink execution details.
"""
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree


STATUS_CANCELED = "canceled"
STATUS_FAILED = "failed"
STATUS_IN_PROGRESS = "in_progress"
STATUS_OK = "ok"
STATUS_PARTIALLY_DONE = "partially_done"
STATUS_PAUSED = "paused"
STATUS_REJECTED = "rejected"
STATUS_SENT = "sent"
STATUS_TIMEOUT = "timeout"

TASK_STATUSES = {
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_OK,
    STATUS_PARTIALLY_DONE,
    STATUS_PAUSED,
    STATUS_REJECTED,
    STATUS_SENT,
    STATUS_TIMEOUT,
}

VALID_STEPS = set(range(49)) | {65533, 65534, 65535}
VALID_TASK_TYPES = {0, 1, 2}
VALID_OUT_OF_CONTROL_ACTIONS = {0, 1, 2}
VALID_EXIT_WAYLINE_WHEN_RC_LOST = {0, 1}
VALID_RETURN_TYPES = {0, 1}
VALID_BREAKPOINT_STATES = {0, 1}
WAYLINE_FILE_EXTENSIONS = {".kmz", ".kml", ".wpml", ".xml"}
MAX_WAYLINE_XML_BYTES = 2 * 1024 * 1024


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_millis_timestamp(value: Any) -> bool:
    return _is_int(value) and len(str(value)) == 13


def _range_error(name: str, value: Any, minimum: float, maximum: float, errors: List[str]) -> None:
    if not _is_number(value) or not minimum <= float(value) <= maximum:
        errors.append(f"{name} must be number in range {minimum}..{maximum}")


def flight_id_from(data: Dict[str, Any]) -> str:
    """Return official ``flight_id`` or accepted legacy alias."""
    value = data.get("flight_id", data.get("flighttask_id", data.get("task_id", "")))
    return value if isinstance(value, str) else ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _local_wayline_path(file_info: Dict[str, Any]) -> str:
    """Resolve a local path from Dock3 file metadata.

    Real cloud deployments download ``file.url`` before parsing.  The server
    demo and Ubuntu smoke tests use a local mock KMZ, so this helper accepts
    ``file:///...`` URLs and direct local paths without adding network I/O.
    """
    for key in ("local_path", "path", "url"):
        value = file_info.get(key)
        if not isinstance(value, str) or not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme == "file":
            return unquote(parsed.path)
        if parsed.scheme in ("http", "https", "oss", "s3"):
            continue
        if parsed.scheme:
            continue
        return value
    return ""


def _empty_wayline_summary(file_info: Dict[str, Any]) -> Dict[str, Any]:
    source = ""
    if isinstance(file_info, dict):
        source = str(file_info.get("url") or file_info.get("local_path") or file_info.get("path") or "")
    return {
        "source": source,
        "format": "",
        "available": False,
        "parsed": False,
        "waypoint_count": 0,
        "files": [],
        "warnings": [],
    }


def _extract_waypoints_from_xml(xml_text: str) -> List[dict]:
    waypoints: List[dict] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    for elem in root.iter():
        if _local_name(elem.tag) != "coordinates" or not elem.text:
            continue
        for token in re.split(r"\s+", elem.text.strip()):
            if not token:
                continue
            parts = token.split(",")
            if len(parts) < 2:
                continue
            try:
                lon = float(parts[0])
                lat = float(parts[1])
                alt = float(parts[2]) if len(parts) >= 3 and parts[2] != "" else 50.0
            except ValueError:
                continue
            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                continue
            waypoints.append({
                "seq": len(waypoints),
                "latitude": lat,
                "longitude": lon,
                "altitude": alt,
                "command": 16,
                "frame": 3,
                "param1": 0.0,
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "autocontinue": 1,
                "action_type": "waypoint",
            })
    return waypoints


def _read_text_file(path: str) -> str:
    with open(path, "rb") as fp:
        data = fp.read(MAX_WAYLINE_XML_BYTES + 1)
    if len(data) > MAX_WAYLINE_XML_BYTES:
        raise ValueError("wayline xml file exceeds 2 MiB limit")
    return data.decode("utf-8", errors="replace")


def _parse_kmz(path: str) -> Tuple[Dict[str, Any], List[dict], List[str]]:
    warnings: List[str] = []
    waypoints: List[dict] = []
    parsed_files: List[str] = []
    with zipfile.ZipFile(path) as archive:
        names = [
            name for name in archive.namelist()
            if Path(name).suffix.lower() in {".kml", ".wpml", ".xml"}
            and not name.endswith("/")
        ]
        for name in names:
            info = archive.getinfo(name)
            if info.file_size > MAX_WAYLINE_XML_BYTES:
                warnings.append(f"{name} exceeds 2 MiB limit")
                continue
            xml_text = archive.read(name).decode("utf-8", errors="replace")
            parsed_files.append(name)
            for waypoint in _extract_waypoints_from_xml(xml_text):
                waypoint["seq"] = len(waypoints)
                waypoints.append(waypoint)
    summary = {"format": "kmz", "files": parsed_files}
    if not parsed_files:
        warnings.append("kmz contains no kml/wpml/xml files")
    if not waypoints:
        warnings.append("no waypoint coordinates found")
    return summary, waypoints, warnings


def parse_wayline_file(file_info: Any) -> Tuple[Dict[str, Any], List[dict]]:
    """Parse local KMZ/WPML/KML metadata into a Dock3-friendly summary.

    Returns ``(summary, waypoints)``.  Remote URLs are accepted as metadata but
    intentionally not downloaded here; the caller can still keep operating in
    simulation mode and attach the summary to replies/events.
    """
    if not isinstance(file_info, dict):
        summary = _empty_wayline_summary({})
        summary["warnings"].append("file must be object")
        return summary, []

    summary = _empty_wayline_summary(file_info)
    path = _local_wayline_path(file_info)
    if not path:
        summary["format"] = "remote" if summary["source"] else ""
        if summary["source"]:
            summary["warnings"].append("remote wayline file not downloaded in local simulation")
        return summary, []

    path = os.path.abspath(os.path.expanduser(path))
    summary["source"] = path
    suffix = Path(path).suffix.lower()
    summary["format"] = suffix.lstrip(".")
    if suffix not in WAYLINE_FILE_EXTENSIONS and suffix != ".zip":
        summary["warnings"].append(f"unsupported wayline file extension: {suffix or '<none>'}")
        return summary, []
    if not os.path.exists(path):
        summary["warnings"].append("local wayline file not found")
        return summary, []

    summary["available"] = True
    waypoints: List[dict] = []
    try:
        if suffix in {".kmz", ".zip"}:
            parsed_summary, waypoints, warnings = _parse_kmz(path)
            summary.update(parsed_summary)
            summary["warnings"].extend(warnings)
        else:
            xml_text = _read_text_file(path)
            summary["files"] = [os.path.basename(path)]
            waypoints = _extract_waypoints_from_xml(xml_text)
            if not waypoints:
                summary["warnings"].append("no waypoint coordinates found")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        summary["warnings"].append(str(exc))
        return summary, []

    summary["waypoint_count"] = len(waypoints)
    summary["parsed"] = bool(waypoints)
    return summary, waypoints


def normalize_prepare_payload(data: Any) -> Tuple[bool, Dict[str, Any], List[str]]:
    """
    Validate and normalize Dock3 ``flighttask_prepare`` payload.

    ``flighttask_id`` is accepted as a legacy alias for existing tests, but new
    callers should use official ``flight_id``.
    """
    if not isinstance(data, dict):
        return False, {}, ["flighttask_prepare data must be object"]

    errors: List[str] = []
    flight_id = flight_id_from(data)
    if not flight_id:
        errors.append("flight_id must be non-empty string")

    task_type = data.get("task_type", 0)
    if not _is_int(task_type) or task_type not in VALID_TASK_TYPES:
        errors.append("task_type must be int enum 0/1/2")

    execute_time = data.get("execute_time", 0)
    if task_type in (0, 1):
        if not _is_millis_timestamp(execute_time):
            errors.append("execute_time must be 13-digit millisecond timestamp")
    elif execute_time and not _is_millis_timestamp(execute_time):
        errors.append("execute_time must be 13-digit millisecond timestamp")

    file_info = data.get("file", {})
    if file_info is not None and not isinstance(file_info, dict):
        errors.append("file must be object")
        file_info = {}
    if isinstance(file_info, dict):
        for key in ("url", "fingerprint"):
            if key in file_info and not isinstance(file_info.get(key), str):
                errors.append(f"file.{key} must be string")

    ready_conditions = data.get("ready_conditions", {})
    if task_type == 2 and not isinstance(ready_conditions, dict):
        errors.append("ready_conditions must be object for conditional task")
    if isinstance(ready_conditions, dict):
        battery_capacity = ready_conditions.get("battery_capacity")
        if battery_capacity is not None and (
            not _is_int(battery_capacity) or not 0 <= battery_capacity <= 100
        ):
            errors.append("ready_conditions.battery_capacity must be int in range 0..100")
        begin_time = ready_conditions.get("begin_time")
        end_time = ready_conditions.get("end_time")
        if begin_time is not None and not _is_millis_timestamp(begin_time):
            errors.append("ready_conditions.begin_time must be 13-digit millisecond timestamp")
        if end_time is not None and not _is_millis_timestamp(end_time):
            errors.append("ready_conditions.end_time must be 13-digit millisecond timestamp")
        if _is_int(begin_time) and _is_int(end_time) and begin_time >= end_time:
            errors.append("ready_conditions.begin_time must be less than end_time")

    executable_conditions = data.get("executable_conditions", {})
    if executable_conditions is not None and not isinstance(executable_conditions, dict):
        errors.append("executable_conditions must be object")
    elif isinstance(executable_conditions, dict):
        storage_capacity = executable_conditions.get("storage_capacity")
        if storage_capacity is not None and (not _is_int(storage_capacity) or storage_capacity < 0):
            errors.append("executable_conditions.storage_capacity must be non-negative int")

    break_point = data.get("break_point", {})
    if break_point is not None and not isinstance(break_point, dict):
        errors.append("break_point must be object")
    elif isinstance(break_point, dict) and break_point:
        state = break_point.get("state")
        if state is not None and (not _is_int(state) or state not in VALID_BREAKPOINT_STATES):
            errors.append("break_point.state must be int enum 0/1")
        if "progress" in break_point:
            _range_error("break_point.progress", break_point.get("progress"), 0, 1.0, errors)
        if "latitude" in break_point:
            _range_error("break_point.latitude", break_point.get("latitude"), -90, 90, errors)
        if "longitude" in break_point:
            _range_error("break_point.longitude", break_point.get("longitude"), -180, 180, errors)

    rth_altitude = data.get("rth_altitude", 100)
    if not _is_number(rth_altitude) or not 20 <= float(rth_altitude) <= 1500:
        errors.append("rth_altitude must be number in range 20..1500")

    out_of_control_action = data.get("out_of_control_action", 0)
    if not _is_int(out_of_control_action) or out_of_control_action not in VALID_OUT_OF_CONTROL_ACTIONS:
        errors.append("out_of_control_action must be int enum 0/1/2")

    exit_wayline_when_rc_lost = data.get("exit_wayline_when_rc_lost", 0)
    if (
        not _is_int(exit_wayline_when_rc_lost)
        or exit_wayline_when_rc_lost not in VALID_EXIT_WAYLINE_WHEN_RC_LOST
    ):
        errors.append("exit_wayline_when_rc_lost must be int enum 0/1")

    simulate_mission = data.get("simulate_mission", {})
    if simulate_mission is not None and not isinstance(simulate_mission, dict):
        errors.append("simulate_mission must be object")
    elif isinstance(simulate_mission, dict) and simulate_mission:
        is_enable = simulate_mission.get("is_enable")
        if is_enable is not None and (not _is_int(is_enable) or is_enable not in {0, 1}):
            errors.append("simulate_mission.is_enable must be int enum 0/1")
        if "latitude" in simulate_mission:
            _range_error("simulate_mission.latitude", simulate_mission.get("latitude"), -90, 90, errors)
        if "longitude" in simulate_mission:
            _range_error("simulate_mission.longitude", simulate_mission.get("longitude"), -180, 180, errors)

    normalized = dict(data)
    normalized["flight_id"] = flight_id
    normalized["task_type"] = task_type
    normalized["execute_time"] = execute_time
    normalized["file"] = file_info or {}
    normalized["ready_conditions"] = ready_conditions or {}
    normalized["executable_conditions"] = executable_conditions or {}
    normalized["break_point"] = break_point or {}
    normalized["rth_altitude"] = rth_altitude
    normalized["out_of_control_action"] = out_of_control_action
    normalized["exit_wayline_when_rc_lost"] = exit_wayline_when_rc_lost
    normalized["simulate_mission"] = simulate_mission or {}
    return not errors, normalized, errors


def validate_flight_id_payload(data: Any, method: str) -> Tuple[bool, str, List[str]]:
    if not isinstance(data, dict):
        return False, "", [f"{method} data must be object"]
    flight_id = flight_id_from(data)
    if not flight_id:
        return False, "", ["flight_id must be non-empty string"]
    return True, flight_id, []


def normalize_undo_payload(data: Any) -> Tuple[bool, List[str], List[str]]:
    if not isinstance(data, dict):
        return False, [], ["flighttask_undo data must be object"]
    flight_ids = data.get("flight_ids")
    if flight_ids is None:
        single = flight_id_from(data)
        flight_ids = [single] if single else []
    if not isinstance(flight_ids, list) or not flight_ids:
        return False, [], ["flight_ids must be non-empty array"]
    errors = []
    for index, item in enumerate(flight_ids):
        if not isinstance(item, str) or not item:
            errors.append(f"flight_ids[{index}] must be non-empty string")
    return not errors, flight_ids, errors


def normalize_return_payload(data: Any) -> Tuple[bool, str, int, List[str]]:
    if not isinstance(data, dict):
        return False, "", 0, ["flighttask_return data must be object"]
    flight_id = flight_id_from(data)
    if not flight_id:
        errors = ["flight_id must be non-empty string"]
    else:
        errors = []
    return_type = data.get("return_type", 0)
    if not _is_int(return_type) or return_type not in VALID_RETURN_TYPES:
        errors.append("return_type must be int enum 0/1")
    return not errors, flight_id, return_type, errors


def progress_percent(current: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, int(current / total * 100)))


def output_data(
    status: str,
    current_step: Optional[int] = None,
    percent: Optional[int] = None,
    ext: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_status = status if status in TASK_STATUSES else STATUS_FAILED
    output: Dict[str, Any] = {"status": normalized_status}
    if current_step is not None or percent is not None:
        output["progress"] = {}
        if current_step is not None:
            output["progress"]["current_step"] = current_step
        if percent is not None:
            output["progress"]["percent"] = max(0, min(100, int(percent)))
    if ext:
        output["ext"] = ext
    data = {"output": output}
    if extra:
        data.update(extra)
    return data
