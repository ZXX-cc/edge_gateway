#!/usr/bin/env python3
"""将 DJI KMZ/WPML 航线独立转换为 QGC WPL .waypoints 文件。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_LOITER_TIME = 19
MAV_CMD_NAV_VTOL_TAKEOFF = 84
MAV_FRAME_GLOBAL_RELATIVE_ALT = 3


class ConvertError(Exception):
    """转换输入不符合当前支持规则。"""


@dataclass
class TakeoffRefPoint:
    lat: float
    lon: float
    alt: float
    agl_height: float | None = None


@dataclass
class SourceWaypoint:
    index: int
    lat: float
    lon: float
    alt: float
    command: int
    hover_time: float = 0.0


@dataclass
class WplItem:
    seq: int
    current: int
    frame: int
    command: int
    param1: float
    param2: float
    param3: float
    param4: float
    lat: float
    lon: float
    alt: float
    autocontinue: int = 1

    def to_line(self) -> str:
        values = [
            str(self.seq),
            str(self.current),
            str(self.frame),
            str(self.command),
            format_float(self.param1),
            format_float(self.param2),
            format_float(self.param3),
            format_float(self.param4),
            format_coord(self.lat),
            format_coord(self.lon),
            format_float(self.alt),
            str(self.autocontinue),
        ]
        return "\t".join(values)


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def iter_by_name(root: ET.Element, name: str) -> Iterable[ET.Element]:
    for element in root.iter():
        if local_name(element.tag) == name:
            yield element


def child_text(parent: ET.Element, name: str, default: str = "") -> str:
    for child in list(parent):
        if local_name(child.tag) == name:
            return (child.text or "").strip()
    return default


def first_text(root: ET.Element, name: str, required: bool = True) -> str:
    for element in iter_by_name(root, name):
        text = (element.text or "").strip()
        if text:
            return text
    if required:
        raise ConvertError(f"缺少必需字段: {name}")
    return ""


def parse_float(text: str, field_name: str) -> float:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError) as exc:
        raise ConvertError(f"{field_name} 不是有效数字: {text!r}") from exc


def parse_int(text: str, field_name: str) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError) as exc:
        raise ConvertError(f"{field_name} 不是有效整数: {text!r}") from exc


def format_float(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def format_coord(value: float) -> str:
    return f"{float(value):.8f}".rstrip("0").rstrip(".")


def parse_xml_bytes(data: bytes, label: str) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ConvertError(f"{label} XML 解析失败: {exc}") from exc


def select_member(names: list[str], suffix: str, explicit_name: str = "") -> str:
    if explicit_name:
        if explicit_name not in names:
            raise ConvertError(f"KMZ 中不存在指定文件: {explicit_name}")
        return explicit_name

    matches = [name for name in names if name.lower().endswith(suffix)]
    if not matches:
        raise ConvertError(f"KMZ 中未找到 {suffix} 文件")
    if len(matches) > 1:
        preferred = [name for name in matches if Path(name).name.lower() in {f"template{suffix}", f"waylines{suffix}"}]
        if len(preferred) == 1:
            return preferred[0]
        raise ConvertError(f"KMZ 中存在多个 {suffix} 文件，请用参数明确指定: {matches}")
    return matches[0]


def select_extracted_file(root: Path, suffix: str, explicit_name: str = "") -> Path:
    if explicit_name:
        selected = Path(explicit_name)
        if not selected.is_absolute():
            selected = root / selected
        if not selected.exists():
            raise ConvertError(f"目录中不存在指定文件: {selected}")
        return selected

    matches = [path for path in root.rglob(f"*{suffix}") if path.is_file()]
    if not matches:
        raise ConvertError(f"目录中未找到 {suffix} 文件: {root}")
    if len(matches) > 1:
        preferred_names = {f"template{suffix}", f"waylines{suffix}"}
        preferred = [path for path in matches if path.name.lower() in preferred_names]
        if len(preferred) == 1:
            return preferred[0]
        raise ConvertError(f"目录中存在多个 {suffix} 文件，请用参数明确指定: {[str(path) for path in matches]}")
    return matches[0]


def read_kmz_members(kmz_path: Path, wpml_name: str = "", kml_name: str = "") -> tuple[str, bytes, str, bytes]:
    if not kmz_path.exists():
        raise ConvertError(f"输入 KMZ 不存在: {kmz_path}")
    if not zipfile.is_zipfile(kmz_path):
        raise ConvertError(f"输入文件不是有效 KMZ/ZIP: {kmz_path}")

    with zipfile.ZipFile(kmz_path) as zf:
        names = [name for name in zf.namelist() if not name.endswith("/")]
        selected_wpml = select_member(names, ".wpml", wpml_name)
        selected_kml = select_member(names, ".kml", kml_name)
        return selected_wpml, zf.read(selected_wpml), selected_kml, zf.read(selected_kml)


def read_wayline_input(input_path: Path, wpml_name: str = "", kml_name: str = "") -> tuple[str, bytes, str, bytes]:
    if input_path.is_dir():
        wpml_path = select_extracted_file(input_path, ".wpml", wpml_name)
        kml_path = select_extracted_file(input_path, ".kml", kml_name)
        return (
            str(wpml_path.relative_to(input_path)),
            wpml_path.read_bytes(),
            str(kml_path.relative_to(input_path)),
            kml_path.read_bytes(),
        )
    return read_kmz_members(input_path, wpml_name, kml_name)


def parse_takeoff_ref_point(text: str, agl_text: str = "") -> TakeoffRefPoint:
    # DJI takeOffRefPoint 按“纬度,经度,高度”给出；不要按 Point.coordinates 的经纬顺序解析。
    parts = [item.strip() for item in text.split(",")]
    if len(parts) < 3:
        raise ConvertError(f"takeOffRefPoint 必须为 纬度,经度,高度: {text!r}")
    ref = TakeoffRefPoint(
        lat=parse_float(parts[0], "takeOffRefPoint.lat"),
        lon=parse_float(parts[1], "takeOffRefPoint.lon"),
        alt=parse_float(parts[2], "takeOffRefPoint.alt"),
    )
    if agl_text:
        ref.agl_height = parse_float(agl_text, "takeOffRefPointAGLHeight")
    if not (-90 <= ref.lat <= 90 and -180 <= ref.lon <= 180):
        raise ConvertError(f"takeOffRefPoint 经纬度超范围: lat={ref.lat}, lon={ref.lon}")
    return ref


def validate_kml(root: ET.Element) -> TakeoffRefPoint:
    coordinate_mode = first_text(root, "coordinateMode")
    height_mode = first_text(root, "heightMode")
    if coordinate_mode != "WGS84":
        raise ConvertError(f".kml coordinateMode 必须等于 WGS84，实际为 {coordinate_mode!r}")
    if height_mode != "relativeToStartPoint":
        raise ConvertError(f".kml heightMode 必须等于 relativeToStartPoint，实际为 {height_mode!r}")

    takeoff_ref = first_text(root, "takeOffRefPoint")
    takeoff_agl = first_text(root, "takeOffRefPointAGLHeight", required=False)
    return parse_takeoff_ref_point(takeoff_ref, takeoff_agl)


def validate_wpml(root: ET.Element) -> tuple[ET.Element, str, str]:
    execute_height_mode = first_text(root, "executeHeightMode")
    if execute_height_mode != "relativeToStartPoint":
        raise ConvertError(
            ".wpml executeHeightMode 必须存在且等于 relativeToStartPoint，"
            f"实际为 {execute_height_mode!r}"
        )

    avoid_mode = first_text(root, "waylineAvoidLimitAreaMode")
    if avoid_mode != "0":
        raise ConvertError(f".wpml waylineAvoidLimitAreaMode 必须为 0，实际为 {avoid_mode!r}")

    folders = list(iter_by_name(root, "Folder"))
    if len(folders) != 1:
        raise ConvertError(f".wpml 只支持一个 Folder，实际数量为 {len(folders)}")
    folder = folders[0]

    template_id = child_text(folder, "templateId")
    wayline_id = child_text(folder, "waylineId")
    if template_id != "0" or wayline_id != "0":
        raise ConvertError(f"只支持 templateId=0 且 waylineId=0，实际为 {template_id}/{wayline_id}")

    distance = child_text(folder, "distance")
    duration = child_text(folder, "duration")
    return folder, distance, duration


def parse_point_coordinates(placemark: ET.Element, index: int) -> tuple[float, float]:
    point = next((item for item in iter_by_name(placemark, "Point")), None)
    if point is None:
        raise ConvertError(f"Placemark index={index} 缺少 Point")
    text = child_text(point, "coordinates")
    if not text:
        raise ConvertError(f"Placemark index={index} 缺少 Point.coordinates")

    first_coord = text.split()[0]
    parts = [item.strip() for item in first_coord.split(",")]
    if len(parts) < 2:
        raise ConvertError(f"Placemark index={index} coordinates 必须为 经度,纬度: {text!r}")
    lon = parse_float(parts[0], f"Placemark[{index}].lon")
    lat = parse_float(parts[1], f"Placemark[{index}].lat")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ConvertError(f"Placemark index={index} 经纬度超范围: lat={lat}, lon={lon}")
    return lat, lon


def hover_time_from_action_groups(placemark: ET.Element, index: int, warnings: list[str]) -> float:
    for group in iter_by_name(placemark, "actionGroup"):
        hover_actions = [
            action for action in iter_by_name(group, "action")
            if child_text(action, "actionActuatorFunc") == "hover"
        ]
        if not hover_actions:
            continue

        start_index = child_text(group, "actionGroupStartIndex")
        end_index = child_text(group, "actionGroupEndIndex")
        trigger_type = first_nested_text(group, "actionTriggerType")
        group_mode = child_text(group, "actionGroupMode")

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
            hover_time = first_nested_text(action, "hoverTime")
            if not hover_time:
                warnings.append(f"Placemark index={index} 的 hover 动作缺少 hoverTime，已按 WAYPOINT 处理")
                continue
            value = parse_float(hover_time, f"Placemark[{index}].hoverTime")
            if value < 0:
                warnings.append(f"Placemark index={index} 的 hoverTime 小于 0，已按 WAYPOINT 处理")
                continue
            return value
    return 0.0


def first_nested_text(root: ET.Element, name: str) -> str:
    for element in iter_by_name(root, name):
        text = (element.text or "").strip()
        if text:
            return text
    return ""


def parse_wpml_waypoints(folder: ET.Element, warnings: list[str]) -> list[SourceWaypoint]:
    placemarks = [item for item in iter_by_name(folder, "Placemark")]
    if not placemarks:
        raise ConvertError(".wpml Folder 中没有 Placemark 航点")

    waypoints: list[SourceWaypoint] = []
    for expected_index, placemark in enumerate(placemarks):
        index_text = child_text(placemark, "index")
        if not index_text:
            raise ConvertError(f"第 {expected_index} 个 Placemark 缺少 wpml:index")
        index = parse_int(index_text, f"Placemark[{expected_index}].index")
        if index != expected_index:
            raise ConvertError(f"Placemark index 必须从 0 连续递增，期望 {expected_index}，实际 {index}")

        lat, lon = parse_point_coordinates(placemark, index)
        height_text = child_text(placemark, "executeHeight")
        if not height_text:
            raise ConvertError(f"Placemark index={index} 缺少 executeHeight")
        alt = parse_float(height_text, f"Placemark[{index}].executeHeight")

        hover_time = hover_time_from_action_groups(placemark, index, warnings)
        command = MAV_CMD_NAV_LOITER_TIME if hover_time > 0 else MAV_CMD_NAV_WAYPOINT
        waypoints.append(SourceWaypoint(index, lat, lon, alt, command, hover_time))
    return waypoints


def build_wpl_items(
    takeoff_ref: TakeoffRefPoint,
    source_waypoints: list[SourceWaypoint],
    takeoff_alt_offset: float,
    home_alt: float,
) -> list[WplItem]:
    if not source_waypoints:
        raise ConvertError("没有可转换的 Placemark 航点")

    first_alt = source_waypoints[0].alt
    takeoff_alt = max(0.0, first_alt - takeoff_alt_offset)
    items = [
        WplItem(
            seq=0,
            current=1,
            frame=MAV_FRAME_GLOBAL_RELATIVE_ALT,
            command=MAV_CMD_NAV_WAYPOINT,
            param1=0,
            param2=0,
            param3=0,
            param4=0,
            lat=takeoff_ref.lat,
            lon=takeoff_ref.lon,
            alt=home_alt,
        ),
        WplItem(
            seq=1,
            current=0,
            frame=MAV_FRAME_GLOBAL_RELATIVE_ALT,
            command=MAV_CMD_NAV_VTOL_TAKEOFF,
            param1=0,
            param2=0,
            param3=0,
            param4=0,
            lat=takeoff_ref.lat,
            lon=takeoff_ref.lon,
            alt=takeoff_alt,
        ),
    ]

    for source in source_waypoints:
        items.append(
            WplItem(
                seq=len(items),
                current=0,
                frame=MAV_FRAME_GLOBAL_RELATIVE_ALT,
                command=source.command,
                param1=source.hover_time if source.command == MAV_CMD_NAV_LOITER_TIME else 0,
                param2=0,
                param3=0,
                param4=0,
                lat=source.lat,
                lon=source.lon,
                alt=source.alt,
            )
        )
    return items


def write_waypoints(path: Path, items: list[WplItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["QGC WPL 110", *(item.to_line() for item in items)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_summary(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def convert(args) -> dict:
    kmz_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    summary_path = Path(args.summary).expanduser().resolve() if args.summary else output_path.with_suffix(".conversion.json")

    wpml_member, wpml_data, kml_member, kml_data = read_wayline_input(kmz_path, args.wpml_name, args.kml_name)
    kml_root = parse_xml_bytes(kml_data, kml_member)
    wpml_root = parse_xml_bytes(wpml_data, wpml_member)

    takeoff_ref = validate_kml(kml_root)
    folder, distance, duration = validate_wpml(wpml_root)

    warnings: list[str] = []
    source_waypoints = parse_wpml_waypoints(folder, warnings)
    items = build_wpl_items(
        takeoff_ref,
        source_waypoints,
        takeoff_alt_offset=args.takeoff_alt_offset,
        home_alt=args.home_alt,
    )

    write_waypoints(output_path, items)
    summary = {
        "input": str(kmz_path),
        "output": str(output_path),
        "wpml_member": wpml_member,
        "kml_member": kml_member,
        "takeoff_ref_point": asdict(takeoff_ref),
        "rules": {
            "frame": MAV_FRAME_GLOBAL_RELATIVE_ALT,
            "execute_height_mode": "relativeToStartPoint",
            "coordinate_mode": "WGS84",
            "height_mode": "relativeToStartPoint",
            "template_id": 0,
            "wayline_id": 0,
            "wayline_avoid_limit_area_mode": 0,
        },
        "wpml_plan_estimate": {
            "distance": parse_float(distance, "distance") if distance else None,
            "duration": parse_float(duration, "duration") if duration else None,
        },
        "home_alt": args.home_alt,
        "takeoff_alt_offset": args.takeoff_alt_offset,
        "first_execute_height": source_waypoints[0].alt,
        "generated_count": len(items),
        "source_placemark_count": len(source_waypoints),
        "hover_count": sum(1 for item in source_waypoints if item.command == MAV_CMD_NAV_LOITER_TIME),
        "warnings": warnings,
        "items": [asdict(item) for item in items],
    }
    write_summary(summary_path, summary)
    summary["summary"] = str(summary_path)
    return summary


def configure_logging(log_file: str = "") -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = Path(log_file).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将 DJI KMZ/WPML 航线转换为 QGC WPL .waypoints")
    parser.add_argument("-i", "--input", required=True, help="输入 KMZ 文件路径")
    parser.add_argument("-o", "--output", required=True, help="输出 .waypoints 文件路径")
    parser.add_argument("--summary", default="", help="输出转换摘要 JSON；默认与 .waypoints 同名 .conversion.json")
    parser.add_argument("--log-file", default="", help="可选日志文件路径")
    parser.add_argument("--wpml-name", default="", help="KMZ 内 .wpml 文件名；多 .wpml 时必须指定")
    parser.add_argument("--kml-name", default="", help="KMZ 内 .kml 文件名；多 .kml 时必须指定")
    parser.add_argument(
        "--takeoff-alt-offset",
        type=float,
        default=40.0,
        help="VTOL_TAKEOFF 高度 = 第一个 executeHeight - 该偏移，默认 40 米",
    )
    parser.add_argument(
        "--home-alt",
        type=float,
        default=0.0,
        help="首行 HOME 任务项相对高度，默认 0 米",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_file)

    try:
        summary = convert(args)
    except ConvertError as exc:
        logging.error("转换失败: %s", exc)
        return 2
    except Exception:
        logging.exception("转换异常")
        return 1

    logging.info("转换成功: %s", summary["output"])
    logging.info("摘要文件: %s", summary["summary"])
    logging.info(
        "生成任务项: %s，源 Placemark: %s，hover: %s",
        summary["generated_count"],
        summary["source_placemark_count"],
        summary["hover_count"],
    )
    if summary["warnings"]:
        logging.warning("转换警告: %s", "；".join(summary["warnings"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
