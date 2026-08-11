"""
DJI Dock 3 wayline / flight task helpers.

The current project only targets a server-side simulation loop.  This module
keeps Dock3 flighttask validation and reply/event payload shape separate from
MissionManager's MAVLink execution details.
"""
import os
import re
import hashlib
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse
from urllib.request import Request, url2pathname, urlopen
from xml.etree import ElementTree

from src.utils.config import Config


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
WAYLINE_FILE_EXTENSIONS = {".kmz", ".kml", ".wpml", ".xml", ".waypoints"}
MAX_WAYLINE_XML_BYTES = 2 * 1024 * 1024
MAX_WAYLINE_FILE_BYTES = 50 * 1024 * 1024
MIN_WAYPOINT_ALTITUDE_METERS = 120.0
POSITION_WAYPOINT_COMMANDS = {16, 17, 18, 19, 20, 21, 22, 82, 84, 85}
MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_LOITER_TIME = 19
MAV_CMD_NAV_VTOL_TAKEOFF = 84
MAV_FRAME_GLOBAL_RELATIVE_ALT = 3
DEFAULT_KMZ_TAKEOFF_ALT_OFFSET_METERS = 40.0
KMZ_NEST_TAKEOFF_CONFIG_PREFIX = "mission.wayline.nest_takeoff_point"


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


def _child_text(elem: ElementTree.Element, names: set[str]) -> str:
    for child in elem.iter():
        if _local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def _direct_child_text(elem: ElementTree.Element, name: str) -> str:
    for child in list(elem):
        if _local_name(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def _iter_by_name(root: ElementTree.Element, name: str) -> List[ElementTree.Element]:
    return [elem for elem in root.iter() if _local_name(elem.tag) == name]


def _first_text(root: ElementTree.Element, name: str, required: bool = True) -> str:
    for elem in root.iter():
        if _local_name(elem.tag) == name and elem.text:
            text = elem.text.strip()
            if text:
                return text
    if required:
        raise ValueError(f"缺少必需字段: {name}")
    return ""


def _parse_coordinate_token(token: str) -> Optional[Tuple[float, float, float]]:
    parts = token.split(",")
    if len(parts) < 2:
        return None
    try:
        lon = float(parts[0])
        lat = float(parts[1])
        alt = float(parts[2]) if len(parts) >= 3 and parts[2] != "" else 50.0
    except ValueError:
        return None
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    return lat, lon, alt


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _action_from_func(func: str, action_elem: ElementTree.Element) -> Optional[dict]:
    action = func.strip()
    action_lower = action.lower()
    if not action:
        return None

    if action_lower in {"takephoto", "take_photo", "photo"}:
        photo_action = {"action_type": "photo", "func": action}
        for output_key, names in {
            "payload_index": {"payloadIndex", "payloadPositionIndex", "payload_index", "cameraIndex"},
            "camera_index": {"camera_index", "cameraIndex"},
            "camera_type": {"cameraType", "camera_type", "payloadType"},
        }.items():
            value = _child_text(action_elem, names)
            if value != "":
                photo_action[output_key] = value
        return photo_action
    if action_lower in {"hover", "wait", "delay"}:
        hover_time = _child_text(action_elem, {"hoverTime", "duration", "delayTime", "waitTime"})
        return {"action_type": "hover", "func": action, "param": hover_time or 0}
    if action_lower in {"gimbalrotate", "gimbal_rotate", "gimbal"}:
        return {
            "action_type": "gimbal",
            "func": action,
            "pitch": _child_text(action_elem, {"gimbalPitchRotateAngle", "pitch", "pitchAngle"}),
            "roll": _child_text(action_elem, {"gimbalRollRotateAngle", "roll", "rollAngle"}),
            "yaw": _child_text(action_elem, {"gimbalYawRotateAngle", "yaw", "yawAngle"}),
        }
    if action_lower in {"changespeed", "change_speed", "speed"}:
        speed = _child_text(action_elem, {"speed", "waypointSpeed", "flightSpeed"})
        return {"action_type": "speed", "func": action, "param": speed or 0}
    return {"action_type": action_lower, "func": action}


def _extract_actions(elem: ElementTree.Element) -> List[dict]:
    actions: List[dict] = []
    for action_elem in elem.iter():
        if _local_name(action_elem.tag) != "action":
            continue
        func = _child_text(action_elem, {"actionActuatorFunc", "action_type", "actionType"})
        action = _action_from_func(func, action_elem)
        if action:
            actions.append(action)
    return actions


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
        if os.path.exists(os.path.expanduser(value)):
            return value
        parsed = urlparse(value)
        if parsed.scheme == "file":
            path = url2pathname(unquote(parsed.path))
            if os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
                path = path[1:]
            return path
        if parsed.scheme in ("http", "https", "oss", "s3"):
            continue
        if parsed.scheme:
            continue
        return value
    return ""


def _remote_wayline_url(file_info: Dict[str, Any]) -> str:
    value = file_info.get("url") if isinstance(file_info, dict) else ""
    if not isinstance(value, str) or not value:
        return ""
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} else ""


def _download_remote_wayline_file(url: str) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in WAYLINE_FILE_EXTENSIONS and suffix != ".zip":
        suffix = ".kmz"
    cache_dir = Path(str(Config.get("mission.wayline.download_cache_dir", "./data/waylines")))
    cache_dir = cache_dir.expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    base_name = Path(unquote(parsed.path)).name or "mission.kmz"
    safe_base = re.sub(r"[^A-Za-z0-9_.-]+", "_", base_name).strip("._") or "mission.kmz"
    if not safe_base.lower().endswith(suffix):
        safe_base = f"{safe_base}{suffix}"
    target = cache_dir / f"{digest}_{safe_base}"
    timeout = _to_float(Config.get("mission.wayline.download_timeout_seconds", 20.0), 20.0)
    max_bytes = int(_to_float(Config.get("mission.wayline.max_download_bytes", MAX_WAYLINE_FILE_BYTES), MAX_WAYLINE_FILE_BYTES))
    tmp_target = target.with_suffix(target.suffix + ".tmp")
    request = Request(url, headers={"User-Agent": "edge-gateway-wayline/1.0"})
    total = 0
    try:
        with urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length:
                try:
                    content_length = int(length)
                except (TypeError, ValueError):
                    content_length = 0
                if content_length > max_bytes:
                    raise ValueError(f"remote wayline file exceeds {max_bytes} bytes")
            with open(tmp_target, "wb") as fp:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"remote wayline file exceeds {max_bytes} bytes")
                    fp.write(chunk)
        os.replace(tmp_target, target)
    except Exception:
        try:
            if tmp_target.exists():
                tmp_target.unlink()
        except OSError:
            pass
        raise
    return str(target)


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

    placemarks = [elem for elem in root.iter() if _local_name(elem.tag) == "Placemark"]
    for placemark in placemarks:
        coord_text = _child_text(placemark, {"coordinates"})
        if not coord_text:
            continue
        speed = _child_text(placemark, {"waypointSpeed", "speed", "useGlobalSpeed"})
        hover_time = _child_text(placemark, {"hoverTime", "duration"})
        actions = _extract_actions(placemark)
        for token in re.split(r"\s+", coord_text.strip()):
            if not token:
                continue
            parsed = _parse_coordinate_token(token)
            if not parsed:
                continue
            lat, lon, alt = parsed
            waypoints.append({
                "seq": len(waypoints),
                "latitude": lat,
                "longitude": lon,
                "altitude": alt,
                "speed": _to_float(speed, 5.0),
                "command": 16,
                "frame": 3,
                "param1": _to_float(hover_time, 0.0),
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "autocontinue": 1,
                "action_type": "waypoint",
                "actions": actions,
                "hover_time": _to_float(hover_time, 0.0),
                "photo": any(action.get("action_type") == "photo" for action in actions),
            })

    if waypoints:
        return waypoints

    for elem in root.iter():
        if _local_name(elem.tag) != "coordinates" or not elem.text:
            continue
        for token in re.split(r"\s+", elem.text.strip()):
            if not token:
                continue
            parsed = _parse_coordinate_token(token)
            if not parsed:
                continue
            lat, lon, alt = parsed
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


def _parse_qgc_wpl(text: str) -> List[dict]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("QGC WPL"):
        return []

    waypoints: List[dict] = []
    for line in lines[1:]:
        parts = re.split(r"\s+", line)
        if len(parts) < 12:
            continue
        try:
            waypoints.append({
                "seq": int(parts[0]),
                "current": int(parts[1]),
                "frame": int(parts[2]),
                "command": int(parts[3]),
                "param1": _to_float(parts[4], 0.0),
                "param2": _to_float(parts[5], 0.0),
                "param3": _to_float(parts[6], 0.0),
                "param4": _to_float(parts[7], 0.0),
                "latitude": _to_float(parts[8], 0.0),
                "longitude": _to_float(parts[9], 0.0),
                "altitude": _to_float(parts[10], 0.0),
                "autocontinue": int(parts[11]),
                "action_type": "qgc_wpl",
            })
        except (TypeError, ValueError):
            continue
    return waypoints


def _parse_strict_float(value: Any, field_name: str) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 不是有效数字: {value!r}") from exc


def _parse_strict_int(value: Any, field_name: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 不是有效整数: {value!r}") from exc


def _parse_takeoff_ref_point(text: str) -> Dict[str, float]:
    parts = [item.strip() for item in text.split(",")]
    if len(parts) < 3:
        raise ValueError(f"takeOffRefPoint 必须为 纬度,经度,高度: {text!r}")
    lat = _parse_strict_float(parts[0], "takeOffRefPoint.lat")
    lon = _parse_strict_float(parts[1], "takeOffRefPoint.lon")
    alt = _parse_strict_float(parts[2], "takeOffRefPoint.alt")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(f"takeOffRefPoint 经纬度超范围: lat={lat}, lon={lon}")
    return {"latitude": lat, "longitude": lon, "altitude": alt}


def _configured_kmz_nest_takeoff_point() -> Dict[str, float]:
    lat_key = f"{KMZ_NEST_TAKEOFF_CONFIG_PREFIX}.latitude"
    lon_key = f"{KMZ_NEST_TAKEOFF_CONFIG_PREFIX}.longitude"
    alt_key = f"{KMZ_NEST_TAKEOFF_CONFIG_PREFIX}.altitude"
    offset_key = f"{KMZ_NEST_TAKEOFF_CONFIG_PREFIX}.takeoff_alt_offset"
    lat = Config.get(lat_key)
    lon = Config.get(lon_key)
    if lat in (None, "") or lon in (None, ""):
        raise ValueError(f"KMZ 转换缺少固定机巢经纬度配置: {lat_key}, {lon_key}")
    lat_f = _parse_strict_float(lat, lat_key)
    lon_f = _parse_strict_float(lon, lon_key)
    if not -90 <= lat_f <= 90 or not -180 <= lon_f <= 180:
        raise ValueError(f"KMZ 固定机巢经纬度超范围: latitude={lat_f}, longitude={lon_f}")
    if lat_f == 0.0 and lon_f == 0.0:
        raise ValueError("KMZ 固定机巢经纬度不能同时为 0")
    return {
        "latitude": lat_f,
        "longitude": lon_f,
        "altitude": _to_float(Config.get(alt_key, 0.0), 0.0),
        "takeoff_alt_offset": _to_float(
            Config.get(offset_key, DEFAULT_KMZ_TAKEOFF_ALT_OFFSET_METERS),
            DEFAULT_KMZ_TAKEOFF_ALT_OFFSET_METERS,
        ),
    }


def _select_kmz_member(names: List[str], suffix: str, preferred_names: set[str]) -> str:
    matches = [name for name in names if name.lower().endswith(suffix)]
    if not matches:
        raise ValueError(f"KMZ 中未找到 {suffix} 文件")
    if len(matches) == 1:
        return matches[0]
    preferred = [name for name in matches if Path(name).name.lower() in preferred_names]
    if len(preferred) == 1:
        return preferred[0]
    raise ValueError(f"KMZ 中存在多个 {suffix} 文件，暂不解析: {matches}")


def _validate_dji_kml(root: ElementTree.Element) -> Dict[str, float]:
    coordinate_mode = _first_text(root, "coordinateMode")
    height_mode = _first_text(root, "heightMode")
    if coordinate_mode != "WGS84":
        raise ValueError(f".kml coordinateMode 必须等于 WGS84，实际为 {coordinate_mode!r}")
    if height_mode != "relativeToStartPoint":
        raise ValueError(f".kml heightMode 必须等于 relativeToStartPoint，实际为 {height_mode!r}")
    takeoff_ref = _first_text(root, "takeOffRefPoint")
    return _parse_takeoff_ref_point(takeoff_ref)


def _validate_dji_wpml(root: ElementTree.Element) -> Tuple[ElementTree.Element, str, str]:
    execute_height_mode = _first_text(root, "executeHeightMode")
    if execute_height_mode != "relativeToStartPoint":
        raise ValueError(
            ".wpml executeHeightMode 必须存在且等于 relativeToStartPoint，"
            f"实际为 {execute_height_mode!r}"
        )
    avoid_mode = _first_text(root, "waylineAvoidLimitAreaMode")
    if avoid_mode != "0":
        raise ValueError(f".wpml waylineAvoidLimitAreaMode 必须为 0，实际为 {avoid_mode!r}")
    folders = _iter_by_name(root, "Folder")
    if len(folders) != 1:
        raise ValueError(f".wpml 只支持一个 Folder，实际数量为 {len(folders)}")
    folder = folders[0]
    template_id = _direct_child_text(folder, "templateId")
    wayline_id = _direct_child_text(folder, "waylineId")
    if template_id != "0" or wayline_id != "0":
        raise ValueError(f"只支持 templateId=0 且 waylineId=0，实际为 {template_id}/{wayline_id}")
    return folder, _direct_child_text(folder, "distance"), _direct_child_text(folder, "duration")


def _parse_wpml_point_coordinates(placemark: ElementTree.Element, index: int) -> Tuple[float, float]:
    point = next((item for item in _iter_by_name(placemark, "Point")), None)
    if point is None:
        raise ValueError(f"Placemark index={index} 缺少 Point")
    text = _direct_child_text(point, "coordinates")
    if not text:
        raise ValueError(f"Placemark index={index} 缺少 Point.coordinates")
    parts = [item.strip() for item in text.split()[0].split(",")]
    if len(parts) < 2:
        raise ValueError(f"Placemark index={index} coordinates 必须为 经度,纬度: {text!r}")
    lon = _parse_strict_float(parts[0], f"Placemark[{index}].lon")
    lat = _parse_strict_float(parts[1], f"Placemark[{index}].lat")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(f"Placemark index={index} 经纬度超范围: lat={lat}, lon={lon}")
    return lat, lon


def _wpml_hover_time(placemark: ElementTree.Element, index: int, warnings: List[str]) -> float:
    for group in _iter_by_name(placemark, "actionGroup"):
        hover_actions = [
            action for action in _iter_by_name(group, "action")
            if _direct_child_text(action, "actionActuatorFunc") == "hover"
        ]
        if not hover_actions:
            continue
        start_index = _direct_child_text(group, "actionGroupStartIndex")
        end_index = _direct_child_text(group, "actionGroupEndIndex")
        group_mode = _direct_child_text(group, "actionGroupMode")
        trigger_type = _first_text(group, "actionTriggerType", required=False)
        if start_index != str(index) or end_index != str(index):
            warnings.append(f"Placemark index={index} 的 hover 动作组起止索引不匹配，已按 WAYPOINT 处理")
            continue
        if group_mode and group_mode != "sequence":
            warnings.append(f"Placemark index={index} 的 actionGroupMode 不是 sequence，已按 WAYPOINT 处理")
            continue
        if trigger_type != "reachPoint":
            warnings.append(f"Placemark index={index} 的 actionTriggerType 不是 reachPoint，已按 WAYPOINT 处理")
            continue
        for action in hover_actions:
            hover_time = _first_text(action, "hoverTime", required=False)
            if not hover_time:
                warnings.append(f"Placemark index={index} 的 hover 动作缺少 hoverTime，已按 WAYPOINT 处理")
                continue
            value = _parse_strict_float(hover_time, f"Placemark[{index}].hoverTime")
            if value < 0:
                warnings.append(f"Placemark index={index} 的 hoverTime 小于 0，已按 WAYPOINT 处理")
                continue
            return value
    return 0.0


def _parse_wpml_source_waypoints(folder: ElementTree.Element, warnings: List[str]) -> List[dict]:
    placemarks = _iter_by_name(folder, "Placemark")
    if not placemarks:
        raise ValueError(".wpml Folder 中没有 Placemark 航点")
    source_waypoints: List[dict] = []
    for expected_index, placemark in enumerate(placemarks):
        index_text = _direct_child_text(placemark, "index")
        if not index_text:
            raise ValueError(f"第 {expected_index} 个 Placemark 缺少 wpml:index")
        index = _parse_strict_int(index_text, f"Placemark[{expected_index}].index")
        if index != expected_index:
            raise ValueError(f"Placemark index 必须从 0 连续递增，期望 {expected_index}，实际 {index}")
        lat, lon = _parse_wpml_point_coordinates(placemark, index)
        height_text = _direct_child_text(placemark, "executeHeight")
        if not height_text:
            raise ValueError(f"Placemark index={index} 缺少 executeHeight")
        altitude = _parse_strict_float(height_text, f"Placemark[{index}].executeHeight")
        hover_time = _wpml_hover_time(placemark, index, warnings)
        command = MAV_CMD_NAV_LOITER_TIME if hover_time > 0 else MAV_CMD_NAV_WAYPOINT
        source_waypoints.append({
            "source_index": index,
            "latitude": lat,
            "longitude": lon,
            "altitude": altitude,
            "command": command,
            "hover_time": hover_time,
        })
    return source_waypoints


def _build_dji_kmz_waypoints(source_waypoints: List[dict], nest_point: Dict[str, float]) -> List[dict]:
    if not source_waypoints:
        raise ValueError("没有可转换的 Placemark 航点")
    first_alt = _to_float(source_waypoints[0].get("altitude"), 0.0)
    takeoff_alt = max(0.0, first_alt - _to_float(
        nest_point.get("takeoff_alt_offset"),
        DEFAULT_KMZ_TAKEOFF_ALT_OFFSET_METERS,
    ))
    waypoints: List[dict] = [
        {
            "seq": 0,
            "current": 1,
            "latitude": nest_point["latitude"],
            "longitude": nest_point["longitude"],
            "altitude": _to_float(nest_point.get("altitude"), 0.0),
            "command": MAV_CMD_NAV_WAYPOINT,
            "frame": MAV_FRAME_GLOBAL_RELATIVE_ALT,
            "param1": 0.0,
            "param2": 0.0,
            "param3": 0.0,
            "param4": 0.0,
            "autocontinue": 1,
            "action_type": "kmz_home",
            "inserted_by_gateway": "kmz_home",
        },
        {
            "seq": 1,
            "current": 0,
            "latitude": nest_point["latitude"],
            "longitude": nest_point["longitude"],
            "altitude": takeoff_alt,
            "command": MAV_CMD_NAV_VTOL_TAKEOFF,
            "frame": MAV_FRAME_GLOBAL_RELATIVE_ALT,
            "param1": 0.0,
            "param2": 0.0,
            "param3": 0.0,
            "param4": 0.0,
            "autocontinue": 1,
            "action_type": "kmz_vtol_takeoff",
            "inserted_by_gateway": "kmz_vtol_takeoff",
        },
    ]
    for source in source_waypoints:
        command = int(source["command"])
        waypoints.append({
            "seq": len(waypoints),
            "current": 0,
            "latitude": source["latitude"],
            "longitude": source["longitude"],
            "altitude": source["altitude"],
            "command": command,
            "frame": MAV_FRAME_GLOBAL_RELATIVE_ALT,
            "param1": source["hover_time"] if command == MAV_CMD_NAV_LOITER_TIME else 0.0,
            "param2": 0.0,
            "param3": 0.0,
            "param4": 0.0,
            "autocontinue": 1,
            "action_type": "loiter_time" if command == MAV_CMD_NAV_LOITER_TIME else "waypoint",
            "source_index": source["source_index"],
            "hover_time": source["hover_time"],
        })
    return waypoints


def _waypoint_requires_min_altitude(waypoint: dict) -> bool:
    if waypoint.get("inserted_by_gateway") or waypoint.get("adjusted_by_gateway"):
        return False
    command = int(waypoint.get("command", 16) or 0)
    if command not in POSITION_WAYPOINT_COMMANDS:
        return False
    lat = _to_float(waypoint.get("latitude"), 0.0)
    lon = _to_float(waypoint.get("longitude"), 0.0)
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0.0 and lon == 0.0)


def _validate_waypoint_altitudes(waypoints: List[dict]) -> List[str]:
    violations = []
    for index, waypoint in enumerate(waypoints):
        if not _waypoint_requires_min_altitude(waypoint):
            continue
        altitude = _to_float(waypoint.get("altitude"), 0.0)
        if altitude < MIN_WAYPOINT_ALTITUDE_METERS:
            violations.append(f"seq={waypoint.get('seq', index)} altitude={altitude:g}m")
    return violations


def _parse_kmz(path: str) -> Tuple[Dict[str, Any], List[dict], List[str]]:
    warnings: List[str] = []
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        kml_name = _select_kmz_member(names, ".kml", {"template.kml"})
        wpml_name = _select_kmz_member(names, ".wpml", {"waylines.wpml"})
        for name in (kml_name, wpml_name):
            if archive.getinfo(name).file_size > MAX_WAYLINE_XML_BYTES:
                raise ValueError(f"{name} exceeds 2 MiB limit")
        try:
            kml_root = ElementTree.fromstring(archive.read(kml_name))
            wpml_root = ElementTree.fromstring(archive.read(wpml_name))
        except ElementTree.ParseError as exc:
            raise ValueError(f"KMZ XML 解析失败: {exc}") from exc

    takeoff_ref = _validate_dji_kml(kml_root)
    folder, distance, duration = _validate_dji_wpml(wpml_root)
    source_waypoints = _parse_wpml_source_waypoints(folder, warnings)
    nest_point = _configured_kmz_nest_takeoff_point()
    waypoints = _build_dji_kmz_waypoints(source_waypoints, nest_point)
    summary = {
        "format": "kmz",
        "parser": "dji_wpml_strict",
        "generated_waypoints_format": "qgc_wpl_equivalent",
        "files": [kml_name, wpml_name],
        "source_takeoff_ref_point": takeoff_ref,
        "nest_takeoff_point_source": KMZ_NEST_TAKEOFF_CONFIG_PREFIX,
        "configured_nest_takeoff_point": {
            "latitude": nest_point["latitude"],
            "longitude": nest_point["longitude"],
            "altitude": nest_point["altitude"],
        },
        "takeoff_alt_offset": nest_point["takeoff_alt_offset"],
        "first_execute_height": source_waypoints[0]["altitude"],
        "inserted_home": True,
        "inserted_vtol_takeoff": True,
        "source_placemark_count": len(source_waypoints),
        "hover_count": sum(1 for item in source_waypoints if item["command"] == MAV_CMD_NAV_LOITER_TIME),
        "wpml_plan_estimate": {
            "distance": _to_float(distance, 0.0) if distance else None,
            "duration": _to_float(duration, 0.0) if duration else None,
        },
    }
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
        remote_url = _remote_wayline_url(file_info)
        if remote_url:
            try:
                path = _download_remote_wayline_file(remote_url)
                summary["downloaded"] = True
                summary["downloaded_path"] = path
            except Exception as exc:
                summary["format"] = "remote"
                summary["warnings"].append(f"remote wayline file download failed: {exc}")
                return summary, []
        else:
            summary["format"] = "remote" if summary["source"] else ""
            if summary["source"]:
                summary["warnings"].append("remote wayline file not downloaded in local simulation")
            return summary, []

    path = os.path.abspath(os.path.expanduser(path))
    if summary.get("downloaded"):
        summary["remote_source"] = summary.get("source", "")
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
        elif suffix == ".waypoints":
            text = _read_text_file(path)
            summary["files"] = [os.path.basename(path)]
            waypoints = _parse_qgc_wpl(text)
            if not waypoints:
                summary["warnings"].append("no qgc waypoint rows found")
        else:
            xml_text = _read_text_file(path)
            summary["files"] = [os.path.basename(path)]
            waypoints = _extract_waypoints_from_xml(xml_text)
            if not waypoints:
                summary["warnings"].append("no waypoint coordinates found")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        summary["warnings"].append(str(exc))
        return summary, []

    altitude_violations = _validate_waypoint_altitudes(waypoints)
    if altitude_violations:
        summary["warnings"].append(
            "waypoint altitude below minimum "
            f"{MIN_WAYPOINT_ALTITUDE_METERS:g}m: {', '.join(altitude_violations)}"
        )
        summary["waypoint_count"] = 0
        summary["parsed"] = False
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


def normalize_in_flight_wayline_payload(
    data: Any,
    method: str,
) -> Tuple[bool, Dict[str, Any], List[str]]:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return False, {}, [f"{method} data must be object"]

    errors: List[str] = []
    normalized = dict(data)
    in_flight_wayline_id = str(data.get("in_flight_wayline_id", "") or "")
    if method != "in_flight_wayline_cancel" and not in_flight_wayline_id:
        errors.append("in_flight_wayline_id must be non-empty string")

    if method == "in_flight_wayline_deliver":
        file_info = data.get("file", {})
        if not isinstance(file_info, dict):
            errors.append("file must be object")
            file_info = {}
        else:
            if not isinstance(file_info.get("url", ""), str) or not file_info.get("url"):
                errors.append("file.url must be non-empty string")
            if not isinstance(file_info.get("fingerprint", ""), str) or not file_info.get("fingerprint"):
                errors.append("file.fingerprint must be non-empty string")

        out_of_control_action = data.get("out_of_control_action", 0)
        if not _is_int(out_of_control_action) or out_of_control_action not in VALID_OUT_OF_CONTROL_ACTIONS:
            errors.append("out_of_control_action must be int enum 0/1/2")

        exit_wayline_when_rc_lost = data.get("exit_wayline_when_rc_lost", 0)
        if (
            not _is_int(exit_wayline_when_rc_lost)
            or exit_wayline_when_rc_lost not in VALID_EXIT_WAYLINE_WHEN_RC_LOST
        ):
            errors.append("exit_wayline_when_rc_lost must be int enum 0/1")

        rth_altitude = data.get("rth_altitude", 100)
        if not _is_number(rth_altitude) or not 20 <= float(rth_altitude) <= 1500:
            errors.append("rth_altitude must be number in range 20..1500")

        rth_mode = data.get("rth_mode", 0)
        if not _is_int(rth_mode) or rth_mode not in {0, 1}:
            errors.append("rth_mode must be int enum 0/1")

        wayline_precision_type = data.get("wayline_precision_type", 0)
        if not _is_int(wayline_precision_type) or wayline_precision_type not in {0, 1}:
            errors.append("wayline_precision_type must be int enum 0/1")

        normalized.update({
            "file": file_info,
            "out_of_control_action": out_of_control_action,
            "exit_wayline_when_rc_lost": exit_wayline_when_rc_lost,
            "rth_altitude": rth_altitude,
            "rth_mode": rth_mode,
            "wayline_precision_type": wayline_precision_type,
        })

    normalized["in_flight_wayline_id"] = in_flight_wayline_id
    return not errors, normalized, errors


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
