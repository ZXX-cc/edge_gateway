#!/usr/bin/env python3
"""审计网关八类日志是否独立落盘。"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.logger import _shutdown_logging, get_logger, init_logger


CATEGORY_MARKERS = {
    "mqtt": ("mqtt", "AUDIT_MQTT"),
    "mavlink_raw": ("mavlink.raw", "AUDIT_MAVLINK_RAW"),
    "mission": ("mission", "AUDIT_MISSION"),
    "alarm": ("alarm", "AUDIT_ALARM"),
    "nest_plc": ("nest_plc", "AUDIT_NEST_PLC"),
    "flight_control": ("flight_control", "AUDIT_FLIGHT_CONTROL"),
    "payload": ("payload", "AUDIT_PAYLOAD"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="验证八类日志文件路由")
    parser.add_argument("--output-dir", help="保留审计日志的目录；默认使用临时目录")
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else Path(tempfile.mkdtemp(prefix="edge_log_audit_"))
    init_logger({
        "level": "INFO",
        "dir": str(output_dir),
        "filename_prefix": "edge_gateway",
        "console": False,
        "capture_print": False,
    })
    gateway_marker = "AUDIT_GATEWAY"
    get_logger("main").info(gateway_marker)
    for logger_name, marker in CATEGORY_MARKERS.values():
        get_logger(logger_name).info(marker)
    _shutdown_logging()

    month = datetime.now().strftime("%Y-%m")
    day = datetime.now().strftime("%Y-%m-%d")
    gateway_file = output_dir / month / f"edge_gateway_{day}.log"
    expected_files = {"gateway": gateway_file}
    expected_files.update({
        category: output_dir / category / month / f"{category}_{day}.log"
        for category in CATEGORY_MARKERS
    })

    errors = []
    all_markers = {gateway_marker, *(marker for _, marker in CATEGORY_MARKERS.values())}
    for category, path in expected_files.items():
        if not path.is_file():
            errors.append(f"缺少 {category} 日志: {path}")
            continue
        content = path.read_text(encoding="utf-8")
        if category == "gateway":
            missing = sorted(marker for marker in all_markers if marker not in content)
            if missing:
                errors.append(f"网关总日志缺少标记: {missing}")
            continue
        expected_marker = CATEGORY_MARKERS[category][1]
        if expected_marker not in content:
            errors.append(f"{category} 日志缺少自身标记 {expected_marker}")
        leaked = sorted(marker for marker in all_markers - {expected_marker} if marker in content)
        if leaked:
            errors.append(f"{category} 日志混入其他分类: {leaked}")

    if errors:
        for error in errors:
            print(f"[失败] {error}")
        print(f"log category audit failed; output={output_dir}")
        return 1

    print("log category audit ok (gateway + mqtt + mavlink_raw + mission + alarm + nest_plc + flight_control + payload)")
    print(f"audit logs -> {output_dir}")
    for category, path in expected_files.items():
        print(f"  {category}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
