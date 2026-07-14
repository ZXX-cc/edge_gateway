#!/usr/bin/env python3
"""Audit the global command-admission rules without MQTT/SITL services."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.state_machine.command_admission import CommandAdmissionEngine
from src.state_machine.global_state import GatewayStateSnapshot


class StaticReducer:
    def __init__(self, snapshot: GatewayStateSnapshot):
        self._snapshot = snapshot

    def snapshot(self) -> GatewayStateSnapshot:
        return self._snapshot


def build_snapshot(**overrides) -> GatewayStateSnapshot:
    base = {
        "gateway_phase": "READY",
        "cloud": "online",
        "device": "online",
        "dock": "ready",
        "aircraft": "online",
        "mission": "idle",
        "drc": "idle",
        "ota": "idle",
        "safety": "normal",
        "details": {},
    }
    base.update(overrides)
    return GatewayStateSnapshot(**base)


def run_case(name: str, method: str, snapshot: GatewayStateSnapshot, expected_accept: bool, expected_reason: str = "") -> dict:
    engine = CommandAdmissionEngine(StaticReducer(snapshot))
    request = {
        "tid": f"audit-{name}",
        "bid": f"audit-{name}",
        "gateway": "EDGEGW001",
        "method": method,
        "need_reply": 1,
        "data": {},
    }
    decision = engine.check(method, request, "thing/product/EDGEGW001/services")
    passed = decision.accepted == expected_accept
    if expected_reason:
        passed = passed and decision.reason == expected_reason
    return {
        "name": name,
        "method": method,
        "expected_accept": expected_accept,
        "actual_accept": decision.accepted,
        "expected_reason": expected_reason,
        "actual_reason": decision.reason,
        "gateway_phase": decision.snapshot.gateway_phase if decision.snapshot else "",
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Global state machine admission audit")
    parser.add_argument(
        "--output",
        default="logs/acceptance_evidence/global_state_machine_admission_summary.json",
        help="summary JSON output path",
    )
    args = parser.parse_args()

    cases = [
        ("ready_flighttask_prepare", "flighttask_prepare", build_snapshot(), True, ""),
        ("unbound_execute", "flighttask_execute", build_snapshot(device="unbound", gateway_phase="DEVICE_UNBOUND"), False, "device_unbound"),
        ("aircraft_offline_drc", "drc_mode_enter", build_snapshot(aircraft="offline"), False, "aircraft_offline"),
        ("estop_blocks_cover", "cover_open", build_snapshot(dock="emergency_stop", safety="emergency_stop", gateway_phase="EMERGENCY_STOP"), False, "emergency_stop_active"),
        ("estop_allows_hms_clear", "hms_clear", build_snapshot(dock="emergency_stop", safety="emergency_stop", gateway_phase="EMERGENCY_STOP"), True, ""),
        ("mission_blocks_ota", "ota_create", build_snapshot(mission="running", gateway_phase="BUSY_MISSION"), False, "mission_busy"),
        ("drc_blocks_mission", "flighttask_execute", build_snapshot(drc="active", gateway_phase="BUSY_DRC"), False, "drc_active"),
        ("paused_resume_ok", "flighttask_resume", build_snapshot(mission="paused", gateway_phase="BUSY_MISSION"), True, ""),
        ("idle_resume_rejected", "flighttask_resume", build_snapshot(mission="idle"), False, "invalid_state_transition"),
        ("idle_pause_rejected", "flighttask_pause", build_snapshot(mission="idle"), False, "invalid_state_transition"),
        ("ota_blocks_drc", "drc_mode_enter", build_snapshot(ota="upgrading", gateway_phase="BUSY_OTA"), False, "ota_in_progress"),
    ]

    results = [
        run_case(name, method, snapshot, expected_accept, expected_reason)
        for name, method, snapshot, expected_accept, expected_reason in cases
    ]
    passed = sum(1 for item in results if item["passed"])
    summary = {
        "status": "passed" if passed == len(results) else "failed",
        "generated_at": int(time.time() * 1000),
        "passed": passed,
        "total": len(results),
        "results": results,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"summary -> {output}")
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
