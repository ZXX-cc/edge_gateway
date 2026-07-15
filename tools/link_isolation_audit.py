#!/usr/bin/env python3
"""Audit gateway/nest/flight-controller link isolation.

This script is intentionally lightweight:
- Static checks verify the default topology and guard against legacy proxy mode.
- Optional runtime checks inspect recent logs after the stack has been started.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def load_config(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except Exception as exc:
        raise RuntimeError(f"PyYAML is required to read {path}: {exc}") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def nested(data: dict[str, Any], *keys: str, default=None):
    value: Any = data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def latest_file(root: Path, pattern: str) -> Path | None:
    if not root.exists():
        return None
    files = [path for path in root.rglob(pattern) if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def file_contains(path: Path | None, patterns: list[str]) -> tuple[bool, str]:
    if not path:
        return False, "log file not found"
    text = read_text(path)
    missing = [pattern for pattern in patterns if pattern not in text]
    if missing:
        return False, f"{path}: missing {missing}"
    return True, str(path)


def regex_contains(path: Path | None, pattern: str) -> tuple[bool, str]:
    if not path:
        return False, "log file not found"
    text = read_text(path)
    if not re.search(pattern, text):
        return False, f"{path}: pattern not found: {pattern}"
    return True, str(path)


def static_checks(config_path: Path) -> list[Check]:
    config = load_config(config_path)
    mav_enabled = bool(nested(config, "mavlink", "enabled", default=True))
    nest_enabled = bool(nested(config, "nest_mavlink", "enabled", default=False))
    mav_port = int(nested(config, "mavlink", "connection", "port", default=14550))
    nest_port = int(nested(config, "nest_mavlink", "connection", "port", default=14551))
    legacy_port = int(nested(config, "nest_mavlink", "sitl", "port", default=14552))

    main_py = read_text(PROJECT_ROOT / "src/main.py")
    mission_py = read_text(PROJECT_ROOT / "src/mission/manager.py")
    telemetry_py = read_text(PROJECT_ROOT / "src/modules/telemetry_module.py")
    nest_script = read_text(PROJECT_ROOT / "scripts/start_nest_simulator.sh")
    sitl_script = read_text(PROJECT_ROOT / "scripts/start_arduplane_sitl.sh")

    checks = [
        Check(
            "config.main_mavlink_enabled",
            mav_enabled,
            f"mavlink.enabled={mav_enabled}; expected true for gateway -> flight-controller direct link",
        ),
        Check(
            "config.nest_mavlink_enabled",
            nest_enabled,
            f"nest_mavlink.enabled={nest_enabled}; expected true for gateway -> nest direct link",
        ),
        Check(
            "config.ports_are_distinct",
            len({mav_port, nest_port, legacy_port}) == 3,
            f"mavlink={mav_port}, nest={nest_port}, legacy_nest_sitl={legacy_port}",
        ),
        Check(
            "main.nest_dispatcher_excludes_fc_when_main_exists",
            "device_side_modules = [] if self.mavlink_conn else" in main_py,
            "nest dispatcher must not host flight-control/telemetry modules when main MAVLink exists",
        ),
        Check(
            "mission.default_wayline_uses_fc",
            "_should_delegate_wayline_to_nest" in mission_py
            and "main_mavlink_disabled" in mission_py
            and "gateway_wayline_uploaded" in mission_py
            and 'set_mode("AUTO")' in mission_py,
            "flighttask_prepare should upload to FC by default; legacy nest delegation only when main MAVLink is disabled",
        ),
        Check(
            "telemetry.field_explainer_log_enabled",
            "[飞控节点] 遥测快照" in telemetry_py
            and "lat=" in telemetry_py
            and "battery=" in telemetry_py
            and "gps_fixed=" in telemetry_py,
            "flight-control log should include explained telemetry fields, not only HEARTBEAT",
        ),
        Check(
            "nest_simulator.legacy_proxy_disabled_by_default",
            'DRONE_PORT="${DRONE_PORT:-0}"' in nest_script,
            "nest simulator should not listen for SITL/FC by default",
        ),
        Check(
            "sitl.outputs_to_gateway_by_default",
            'SITL_GATEWAY_PORT="${SITL_GATEWAY_PORT:-$(mavlink_port)}"' in sitl_script
            and 'SITL_LEGACY_NEST_OUT="${SITL_LEGACY_NEST_OUT:-0}"' in sitl_script,
            "SITL should output to gateway MAVLink port; legacy nest output must be opt-in",
        ),
    ]
    return checks


def runtime_checks(log_root: Path) -> list[Check]:
    flight_log = latest_file(log_root / "flight_control", "flight_control_*.log")
    nest_log = latest_file(log_root / "nest_plc", "nest_plc_*.log")
    raw_log = latest_file(log_root / "mavlink_raw", "mavlink_raw_*.log")
    nest_status = PROJECT_ROOT / "logs/nest_simulator/nest_status.log"

    ok, detail = file_contains(flight_log, ["[飞控节点] 遥测快照", "flight_mode=", "lat=", "battery="])
    checks = [Check("runtime.flight_control_has_explained_telemetry", ok, detail)]

    ok, detail = regex_contains(raw_log, r"connection=udp(in)?:127\.0\.0\.1:14550|connection=udp:127\.0\.0\.1:14550")
    checks.append(Check("runtime.raw_log_has_main_fc_link", ok, detail))

    ok, detail = regex_contains(raw_log, r"connection=udp:127\.0\.0\.1:14551")
    checks.append(Check("runtime.raw_log_has_nest_link", ok, detail))

    ok, detail = file_contains(nest_status if nest_status.exists() else None, ["nest_status", "状态位1", "状态位2"])
    checks.append(Check("runtime.nest_simulator_status_log", ok, detail))

    if nest_log:
        text = read_text(nest_log)
        mixed = "[飞控节点] 遥测快照" in text or "set_mode(\"AUTO\")" in text
        checks.append(Check("runtime.nest_log_has_no_fc_telemetry_snapshot", not mixed, str(nest_log)))
    else:
        checks.append(Check("runtime.nest_log_has_no_fc_telemetry_snapshot", False, "nest_plc log file not found"))

    return checks


def print_checks(checks: list[Check]) -> int:
    failures = 0
    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")
        if not check.ok:
            failures += 1
    if failures:
        print(f"link isolation audit failed: failures={failures}", file=sys.stderr)
        return 1
    print(f"link isolation audit ok (checks={len(checks)})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit gateway/nest/flight-controller link isolation")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config/gateway.yaml"))
    parser.add_argument("--runtime", action="store_true", help="also inspect recent logs after the stack is running")
    parser.add_argument("--log-root", default=str(PROJECT_ROOT / "logs"))
    args = parser.parse_args()

    checks = static_checks(Path(args.config))
    if args.runtime:
        checks.extend(runtime_checks(Path(args.log_root)))
    return print_checks(checks)


if __name__ == "__main__":
    raise SystemExit(main())
