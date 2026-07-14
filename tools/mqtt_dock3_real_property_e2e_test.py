#!/usr/bin/env python3
"""DJI Dock 3 device-property full-chain MQTT acceptance.

This tool is a cloud-side test client for the real-chain startup profile in
docs/REAL_CHAIN_STARTUP.md. It assumes the broker and Edge Gateway are already
running, then validates the Dock 3 device-property path end to end:

* property/set -> property/set_reply
* property_get -> services_reply
* property changes -> state report
* official 49-property snapshot shape from the Dock 3 properties catalog
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_dock3_osd_state_test import validate_state
from mqtt_test_client import MqttRequestClient, property_result
from tool_config import add_config_argument, resolve_mqtt_settings
from src.cloud_api.dock3_properties import (
    DOCK3_PROPERTY_SPECS,
    REAL_DEVICE_CONFIRMATION_PROPERTIES,
    official_property_names,
    writable_property_names,
)


WRITABLE_CASES: List[str] = [
    "silent_mode",
    "air_transfer_enable",
    "user_experience_improvement",
]

REJECT_CASES: List[Tuple[str, Any]] = [
    ("battery_store_mode", 2),
    ("alarm_state", 1),
    ("drc_state", 2),
    ("silent_mode", 9),
    ("air_transfer_enable", 1),
    ("unknown_property", 1),
]


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def type_error(name: str, value: Any) -> str:
    spec = DOCK3_PROPERTY_SPECS[name]
    if spec.value_type == "bool":
        valid = isinstance(value, bool)
    elif spec.value_type in {"enum_int", "int"}:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif spec.value_type in {"float", "double"}:
        valid = is_number(value)
    elif spec.value_type == "text":
        valid = isinstance(value, str)
    elif spec.value_type == "struct":
        valid = isinstance(value, dict)
    elif spec.value_type == "array":
        valid = isinstance(value, list)
    else:
        return f"{name}: unsupported value_type={spec.value_type}"

    if not valid:
        return f"{name}: expected={spec.value_type}, actual={type(value).__name__}"
    if spec.enum_values is not None and value not in spec.enum_values:
        return f"{name}: enum out of range, actual={value!r}"
    if spec.min_value is not None and value < spec.min_value:
        return f"{name}: below min={spec.min_value}, actual={value!r}"
    if spec.max_value is not None and value > spec.max_value:
        return f"{name}: above max={spec.max_value}, actual={value!r}"
    return ""


def validate_snapshot(data: Dict[str, Any], *, strict_all: bool) -> List[str]:
    official = official_property_names()
    present = official.intersection(data)
    errors: List[str] = []
    if strict_all:
        for name in sorted(official - set(data)):
            errors.append(f"property_get missing official property: {name}")
    elif not present:
        errors.append("property_get did not return any official Dock3 property")

    for name in sorted(present):
        error = type_error(name, data[name])
        if error:
            errors.append(error)
    return errors


class PropertyE2EClient(MqttRequestClient):
    def __init__(self, *args, expected_state_properties: Optional[Iterable[str]] = None, **kwargs):
        self.state_topic = ""
        self.expected_state_properties = set(expected_state_properties or ())
        self.valid_state: List[Dict[str, Any]] = []
        self.expected_state: Dict[str, List[Dict[str, Any]]] = {
            name: [] for name in self.expected_state_properties
        }
        self.invalid_state: List[str] = []
        super().__init__(*args, observe_topics=[], **kwargs)
        self.state_topic = f"thing/product/{self.sn}/state"
        self.observed_topics.add(self.state_topic)

    def _handle_observed_message(self, topic: str, payload: Any) -> None:
        super()._handle_observed_message(topic, payload)
        if topic != self.state_topic:
            return
        if not isinstance(payload, dict):
            self.invalid_state.append(f"{topic}: payload must be object")
            return

        errors = validate_state(payload, self.sn, "")
        if errors:
            preview = json.dumps(payload, ensure_ascii=False)[:500]
            self.invalid_state.append(f"{topic}: {'; '.join(errors)}; payload={preview}")
            return

        self.valid_state.append(payload)
        properties = payload.get("data", {}).get("property", {})
        if not isinstance(properties, dict):
            return
        for name in self.expected_state_properties.intersection(properties):
            self.expected_state.setdefault(name, []).append(payload)

    def wait_state_property(self, name: str, previous_count: int, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.expected_state.get(name, [])) > previous_count:
                return True
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            self.observed_signal.wait(max(0.05, min(0.5, remaining)))
            self.observed_signal.clear()
        return len(self.expected_state.get(name, [])) > previous_count


def require_property_result(reply: Optional[dict], name: str, expected: int) -> bool:
    actual = property_result(reply, name)
    if actual != expected:
        print(
            f"property_set {name} unexpected state.result: "
            f"expected={expected}, actual={actual}",
            file=sys.stderr,
        )
        if isinstance(reply, dict):
            print(json.dumps(reply, ensure_ascii=False, indent=2), file=sys.stderr)
        return False
    return True


def next_writable_value(name: str, current: Any) -> Any:
    if name == "silent_mode":
        return 0 if current == 1 else 1
    if name == "air_transfer_enable":
        return not current if isinstance(current, bool) else True
    if name == "user_experience_improvement":
        for candidate in (0, 1, 2):
            if candidate != current:
                return candidate
        return 2
    raise ValueError(f"unsupported writable property: {name}")


def restore_writable_value(name: str, current: Any) -> Any:
    if name == "silent_mode" and current in (0, 1):
        return current
    if name == "air_transfer_enable" and isinstance(current, bool):
        return current
    if name == "user_experience_improvement" and current in (0, 1, 2):
        return current
    return next_writable_value(name, current)


def read_properties(client: PropertyE2EClient, names: List[str], timeout: float) -> Tuple[int, Dict[str, Any]]:
    rc, reply = client.request(
        "property_get",
        {"properties": names},
        timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc, {}
    data = (reply or {}).get("data", {})
    if not isinstance(data, dict):
        print("property_get data must be object", file=sys.stderr)
        return 30, {}
    missing = [name for name in names if name not in data]
    if missing:
        print(f"property_get missing writable properties: {missing}", file=sys.stderr)
        return 31, data
    return 0, data


def run_writable_checks(client: PropertyE2EClient, args) -> int:
    rc, current_values = read_properties(client, WRITABLE_CASES, args.reply_timeout)
    if rc != 0:
        return rc

    for name in WRITABLE_CASES:
        original_value = current_values.get(name)
        value = next_writable_value(name, original_value)
        restore_value = restore_writable_value(name, original_value)
        before_state = len(client.expected_state.get(name, []))
        rc, reply = client.request("property_set", {name: value}, args.reply_timeout, channel="property")
        if rc != 0:
            return rc
        if not require_property_result(reply, name, 0):
            return 10
        if not args.skip_state and not client.wait_state_property(name, before_state, args.state_timeout):
            print(f"timeout waiting state report for changed property: {name}", file=sys.stderr)
            return 11

        rc, reply = client.request(
            "property_get",
            {"properties": [name]},
            args.reply_timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc
        actual = (reply or {}).get("data", {}).get(name)
        if actual != value:
            print(f"property_get {name} mismatch: expected={value!r}, actual={actual!r}", file=sys.stderr)
            return 12

        if args.restore:
            rc, reply = client.request("property_set", {name: restore_value}, args.reply_timeout, channel="property")
            if rc != 0:
                return rc
            if not require_property_result(reply, name, 0):
                return 13
    return 0


def run_reject_checks(client: PropertyE2EClient, args) -> int:
    if args.skip_rejects:
        return 0
    for name, value in REJECT_CASES:
        rc, reply = client.request("property_set", {name: value}, args.reply_timeout, channel="property")
        if rc != 0:
            return rc
        if not require_property_result(reply, name, 1):
            return 20
    return 0


def run_snapshot_check(client: PropertyE2EClient, args) -> Tuple[int, Dict[str, Any]]:
    names = sorted(official_property_names())
    rc, reply = client.request(
        "property_get",
        {"properties": names},
        args.reply_timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc, {}
    data = (reply or {}).get("data", {})
    if not isinstance(data, dict):
        print("property_get data must be object", file=sys.stderr)
        return 30, {}

    errors = validate_snapshot(data, strict_all=not args.allow_partial_snapshot)
    if errors:
        for error in errors[:30]:
            print(f"property snapshot error: {error}", file=sys.stderr)
        return 31, data
    return 0, data


def write_evidence(args, summary: Dict[str, Any]) -> str:
    evidence_dir = args.evidence_dir
    if not evidence_dir:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        evidence_dir = os.path.join("logs", "acceptance_evidence", f"{timestamp}_dock3_real_properties")
    os.makedirs(evidence_dir, exist_ok=True)
    path = os.path.join(evidence_dir, "dock3_real_property_e2e_summary.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return os.path.abspath(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run real-chain DJI Dock3 device-property MQTT acceptance",
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--reply-timeout", type=float, default=12.0)
    parser.add_argument("--state-timeout", type=float, default=8.0)
    parser.add_argument("--connect-timeout", type=float, default=8.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--skip-state", action="store_true")
    parser.add_argument("--skip-rejects", action="store_true")
    parser.add_argument("--restore", action="store_true", help="Restore writable properties after each set/get check.")
    parser.add_argument(
        "--allow-partial-snapshot",
        action="store_true",
        help="Allow property_get to return a subset of official properties while real-device support is being phased in.",
    )
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--evidence-dir", default="")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    expected_state = [] if args.skip_state else list(WRITABLE_CASES)
    client = PropertyE2EClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_real_property_e2e",
        expected_state_properties=expected_state,
    )
    if not client.connect(args.connect_timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    snapshot: Dict[str, Any] = {}
    try:
        rc = run_writable_checks(client, args)
        if rc != 0:
            return rc

        rc = run_reject_checks(client, args)
        if rc != 0:
            return rc

        rc, snapshot = run_snapshot_check(client, args)
        if rc != 0:
            return rc

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            samples = client.stale_sample_summary(limit=12)
            if samples:
                print("stale_samples=" + json.dumps(samples, ensure_ascii=False), file=sys.stderr)
            return 40

        official = official_property_names()
        summary = {
            "status": "passed",
            "gateway_sn": args.sn,
            "mqtt": {"host": args.host, "port": args.port, "qos": args.qos},
            "official_source": (
                "https://developer.dji.com/doc/cloud-api-tutorial/cn/"
                "api-reference/dock-to-cloud/mqtt/dock/dock3/properties.html"
            ),
            "official_property_total": len(official),
            "property_get_covered": len(official.intersection(snapshot)),
            "writable_properties": sorted(writable_property_names()),
            "writable_set_get_verified": list(WRITABLE_CASES),
            "readonly_or_invalid_rejects_verified": [] if args.skip_rejects else [name for name, _ in REJECT_CASES],
            "state_verified": [] if args.skip_state else [
                name for name in expected_state if client.expected_state.get(name)
            ],
            "real_device_confirmation_properties": sorted(REAL_DEVICE_CONFIRMATION_PROPERTIES),
            "mqtt_stats": client.stats,
            "invalid_state_samples": client.invalid_state[:5],
            "stale_total": stale_total,
            "timestamp": int(time.time() * 1000),
        }
        evidence_path = write_evidence(args, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"Dock3 real property evidence -> {evidence_path}")
        print("dock3 real property e2e test ok")
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
