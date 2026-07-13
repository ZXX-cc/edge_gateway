#!/usr/bin/env python3
"""Full Dock 3 property coverage acceptance for simulator-only deployments."""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_dock3_osd_state_test import Dock3OsdStateClient, validate_dock_osd
from mqtt_test_client import property_result
from tool_config import add_config_argument, resolve_mqtt_settings
from src.cloud_api.dock3_properties import (
    DOCK3_PROPERTY_SPECS,
    REAL_DEVICE_CONFIRMATION_PROPERTIES,
    official_property_names,
)


STATE_CASES: List[Tuple[str, Any, Any]] = [
    ("silent_mode", 1, 0),
    ("air_transfer_enable", True, False),
    ("user_experience_improvement", 2, 1),
]


def legacy_competing_property_reply_count(client: Dock3OsdStateClient) -> int:
    """Count late replies emitted by a legacy simulator on the shared broker.

    Some Dock simulators subscribe to the same property/set topic as the
    gateway and send a second reply without the required ``method`` field. The
    current gateway reply and state are already validated independently, so
    these legacy competing replies are evidence warnings rather than failures.
    Current-format duplicate replies remain strict failures.
    """
    expected_topic = f"thing/product/{client.sn}/property/set_reply"
    return sum(
        1 for sample in client.stale_samples
        if sample.get("category") == "duplicate"
        and sample.get("topic") == expected_topic
        and not sample.get("method")
    )


def stale_breakdown(client: Dock3OsdStateClient) -> Dict[str, int]:
    """Split strict broker noise into competing replies and legacy reports."""
    legacy_competing_replies = legacy_competing_property_reply_count(client)
    reply_strict_total = sum(
        int(client.stats.get(name, 0))
        for name in (
            "ignored_mismatched",
            "ignored_legacy",
            "ignored_duplicate",
            "ignored_wrong_topic",
        )
    )
    legacy_observed_total = int(client.stats.get("ignored_observed_legacy", 0))
    current_competing_reply_total = max(0, reply_strict_total - legacy_competing_replies)
    strict_total = current_competing_reply_total + legacy_observed_total
    return {
        "total": strict_total + legacy_competing_replies,
        "strict_total": strict_total,
        "current_competing_reply_total": current_competing_reply_total,
        "legacy_observed_total": legacy_observed_total,
        "legacy_competing_property_replies": legacy_competing_replies,
        "unrelated_reply_total": int(client.stats.get("ignored_unknown", 0)),
    }


def value_error(name: str, value: Any) -> str:
    spec = DOCK3_PROPERTY_SPECS[name]
    if spec.value_type == "bool":
        valid_type = isinstance(value, bool)
    elif spec.value_type in {"enum_int", "int"}:
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif spec.value_type in {"float", "double"}:
        valid_type = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif spec.value_type == "text":
        valid_type = isinstance(value, str)
    elif spec.value_type == "struct":
        valid_type = isinstance(value, dict)
    elif spec.value_type == "array":
        valid_type = isinstance(value, list)
    else:
        return f"{name}: unsupported catalog type={spec.value_type}"

    if not valid_type:
        return f"{name}: expected={spec.value_type}, actual={type(value).__name__}"
    if spec.enum_values is not None and value not in spec.enum_values:
        return f"{name}: enum out of range, actual={value!r}"
    if spec.min_value is not None and value < spec.min_value:
        return f"{name}: below min={spec.min_value}, actual={value!r}"
    if spec.max_value is not None and value > spec.max_value:
        return f"{name}: above max={spec.max_value}, actual={value!r}"
    return ""


def validate_property_snapshot(data: Dict[str, Any], sn: str) -> List[str]:
    official = official_property_names()
    errors = [f"property_get missing {name}" for name in sorted(official - set(data))]
    for name in sorted(official.intersection(data)):
        error = value_error(name, data[name])
        if error:
            errors.append(error)

    # Reuse the strict OSD nested-field validator against the pushMode=0
    # portion of property_get. This verifies both top-level coverage and the
    # simulator structures used by the periodic report.
    osd_data = {name: data[name] for name in official_property_names(0) if name in data}
    errors.extend(validate_dock_osd({
        "tid": "property-coverage",
        "bid": "property-coverage",
        "gateway": sn,
        "timestamp": int(time.time() * 1000),
        "data": osd_data,
    }, sn))
    return errors


def wait_for_state_property(
    client: Dock3OsdStateClient,
    name: str,
    start_index: int,
    timeout: float,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with client.lock:
            samples = list(client.valid_state[start_index:])
        for payload in samples:
            properties = payload.get("data", {}).get("property", {})
            if isinstance(properties, dict) and name in properties:
                return True
        client.changed.wait(max(0.05, min(0.5, deadline - time.time())))
        client.changed.clear()
    return False


def trigger_state_property(client, name: str, first: Any, second: Any, timeout: float) -> int:
    with client.lock:
        start_index = len(client.valid_state)
    for value in (first, second):
        rc, reply = client.request("property_set", {name: value}, timeout, channel="property")
        if rc != 0:
            return rc
        if property_result(reply, name) != 0:
            print(f"property_set {name} rejected value={value!r}", file=sys.stderr)
            return 5
        if wait_for_state_property(client, name, start_index, 2.0):
            return 0
    print(f"state did not report changed property: {name}", file=sys.stderr)
    return 6


def write_evidence(args, summary: Dict[str, Any]) -> str:
    evidence_dir = args.evidence_dir
    if not evidence_dir:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        evidence_dir = os.path.join("logs", "acceptance_evidence", timestamp)
    os.makedirs(evidence_dir, exist_ok=True)
    path = os.path.join(evidence_dir, "dock3_property_coverage_summary.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return os.path.abspath(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate all official Dock 3 properties in a simulator-only gateway",
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--drone-sn", default="DRONESIM001")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--evidence-dir", default="")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = Dock3OsdStateClient(
        args.host,
        args.port,
        args.sn,
        args.drone_sn,
        args.qos,
        expected_state_property="",
        quiet_stale=args.quiet_stale,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        names = sorted(official_property_names())
        rc, reply = client.request(
            "property_get",
            {"properties": names},
            args.timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0 or not isinstance(reply, dict):
            return rc or 3

        snapshot = reply.get("data", {})
        if not isinstance(snapshot, dict):
            print("property_get data must be object", file=sys.stderr)
            return 4
        errors = validate_property_snapshot(snapshot, args.sn)
        if errors:
            for error in errors[:30]:
                print(f"property coverage error: {error}", file=sys.stderr)
            return 7

        if not client.wait_for_counts(1, 0, 0, args.timeout):
            print("timeout waiting for full Dock 3 OSD", file=sys.stderr)
            for sample in client.invalid_samples[:5]:
                print(f"invalid sample: {sample}", file=sys.stderr)
            return 8

        covered_state = []
        for name, first, second in STATE_CASES:
            rc = trigger_state_property(client, name, first, second, args.timeout)
            if rc != 0:
                return rc
            covered_state.append(name)

        stale = stale_breakdown(client)
        stale_total = stale["total"]
        legacy_competing_replies = stale["legacy_competing_property_replies"]
        strict_stale_total = stale["strict_total"]
        if legacy_competing_replies:
            print(
                "warning: legacy competing property replies observed "
                f"(count={legacy_competing_replies}); gateway replies and state were valid",
                file=sys.stderr,
            )
        if args.fail_on_stale and strict_stale_total:
            print(
                "strict stale replies or reports observed "
                f"(strict={strict_stale_total}, total={stale_total})",
                file=sys.stderr,
            )
            print("stale_breakdown=" + json.dumps(stale, ensure_ascii=False), file=sys.stderr)
            samples = client.stale_sample_summary(limit=12)
            if samples:
                print("reply_samples=" + json.dumps(samples, ensure_ascii=False), file=sys.stderr)
            if client.ignored_legacy_samples:
                print(
                    "legacy_observed_samples="
                    + json.dumps(client.ignored_legacy_samples[-12:], ensure_ascii=False),
                    file=sys.stderr,
                )
            evidence_path = write_evidence(
                args,
                {
                    "status": "failed",
                    "failure": "strict_stale_sources",
                    "gateway_sn": args.sn,
                    "mqtt": {"host": args.host, "port": args.port},
                    "property_get_covered": len(official_property_names().intersection(snapshot)),
                    "writable_state_verified": sorted(covered_state),
                    "stale": stale,
                    "reply_samples": samples,
                    "legacy_observed_samples": client.ignored_legacy_samples[-12:],
                    "timestamp": int(time.time() * 1000),
                },
            )
            print(f"Dock3 property failure evidence -> {evidence_path}", file=sys.stderr)
            return 9

        official = official_property_names()
        hardware = set(REAL_DEVICE_CONFIRMATION_PROPERTIES)
        summary = {
            "status": "passed",
            "gateway_sn": args.sn,
            "official_source": (
                "https://developer.dji.com/doc/cloud-api-tutorial/cn/"
                "api-reference/dock-to-cloud/mqtt/dock/dock3/properties.html"
            ),
            "official_top_level": len(official),
            "property_get_covered": len(official.intersection(snapshot)),
            "osd_push_mode_0_covered": len(official_property_names(0)),
            "state_push_mode_1_catalogued": len(official_property_names(1)),
            "writable_state_verified": sorted(covered_state),
            "simulator_semantic_coverage": len(official - hardware),
            "real_device_confirmation_count": len(hardware),
            "real_device_confirmation_properties": sorted(hardware),
            "stale_total": stale_total,
            "strict_stale_total": strict_stale_total,
            "legacy_competing_property_replies": legacy_competing_replies,
            "stale": stale,
            "timestamp": int(time.time() * 1000),
        }
        evidence_path = write_evidence(args, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"Dock3 property evidence -> {evidence_path}")
        print("dock3 full property coverage test ok")
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
