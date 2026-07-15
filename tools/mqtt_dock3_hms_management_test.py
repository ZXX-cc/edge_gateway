#!/usr/bin/env python3
"""Acceptance test for DJI Dock3 HMS management over the real MQTT chain.

Official Dock3 HMS management is device event reporting: the gateway publishes
``thing/product/{gateway_sn}/events`` with method ``hms``.  This tool validates
that event shape and can optionally drive the existing gateway ``hms_get`` /
``hms_clear`` service hooks for local acceptance.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, output_status, result_code  # noqa: E402
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


OFFICIAL_LEVELS = {0, 1, 2}
OFFICIAL_MODULES = {0, 1, 2, 3}
OFFICIAL_BINARY = {0, 1}


def _as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def event_alarms(event: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(event, dict):
        return []
    data = event.get("data")
    if not isinstance(data, dict):
        return []
    alarms = _as_list(data.get("list"))
    if alarms:
        return alarms
    return _as_list(data.get("alarms"))


def validate_alarm(alarm: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    level = alarm.get("level")
    module = alarm.get("module")
    in_the_sky = alarm.get("in_the_sky")
    imminent = alarm.get("imminent")

    if level not in OFFICIAL_LEVELS:
        errors.append(f"level must be one of {sorted(OFFICIAL_LEVELS)}, actual={level!r}")
    if module not in OFFICIAL_MODULES:
        errors.append(f"module must be one of {sorted(OFFICIAL_MODULES)}, actual={module!r}")
    if in_the_sky not in OFFICIAL_BINARY:
        errors.append(f"in_the_sky must be 0 or 1, actual={in_the_sky!r}")
    if imminent not in OFFICIAL_BINARY:
        errors.append(f"imminent must be 0 or 1, actual={imminent!r}")
    if alarm.get("code") in (None, ""):
        errors.append("code is required")
    if alarm.get("device_type") in (None, ""):
        errors.append("device_type is required")

    args = alarm.get("args")
    if not isinstance(args, dict):
        errors.append("args must be object")
    else:
        if "component_index" not in args:
            errors.append("args.component_index is required")
        if "sensor_index" not in args:
            errors.append("args.sensor_index is required")
    return errors


def validate_hms_event(
    event: dict[str, Any] | None,
    *,
    expect_min_alarms: int,
    expect_alarm_id: str = "",
    expect_code: str = "",
    expect_level: int | None = None,
    allow_empty: bool = False,
) -> tuple[int, list[dict[str, Any]]]:
    if not isinstance(event, dict):
        print("missing hms event payload", file=sys.stderr)
        return 20, []
    if event.get("method") != "hms":
        print(f"unexpected event method: {event.get('method')}", file=sys.stderr)
        return 21, []

    data = event.get("data")
    if not isinstance(data, dict):
        print("hms event data must be object", file=sys.stderr)
        return 22, []
    if output_status(event) != "ok":
        print(f"hms event output.status mismatch: actual={output_status(event)}", file=sys.stderr)
        print(json.dumps(event, ensure_ascii=False, indent=2), file=sys.stderr)
        return 23, []

    alarms = event_alarms(event)
    if not allow_empty and len(alarms) < expect_min_alarms:
        print(
            f"hms alarm count too small: expected>={expect_min_alarms}, actual={len(alarms)}",
            file=sys.stderr,
        )
        print(json.dumps(event, ensure_ascii=False, indent=2), file=sys.stderr)
        return 24, alarms
    if len(alarms) > 20:
        print(f"hms event data.list exceeds Dock3 limit: actual={len(alarms)}", file=sys.stderr)
        return 25, alarms

    for index, alarm in enumerate(alarms):
        errors = validate_alarm(alarm)
        if errors:
            print(f"hms alarm[{index}] schema invalid: {errors}", file=sys.stderr)
            print(json.dumps(alarm, ensure_ascii=False, indent=2), file=sys.stderr)
            return 26, alarms

    if expect_alarm_id and not any(
        alarm.get("id") == expect_alarm_id or alarm.get("alarm_id") == expect_alarm_id
        for alarm in alarms
    ):
        print(f"expected alarm id not found: {expect_alarm_id}", file=sys.stderr)
        print(json.dumps(event, ensure_ascii=False, indent=2), file=sys.stderr)
        return 27, alarms
    if expect_code and not any(str(alarm.get("code")) == expect_code for alarm in alarms):
        print(f"expected alarm code not found: {expect_code}", file=sys.stderr)
        print(json.dumps(event, ensure_ascii=False, indent=2), file=sys.stderr)
        return 28, alarms
    if expect_level is not None and not any(alarm.get("level") == expect_level for alarm in alarms):
        print(f"expected alarm level not found: {expect_level}", file=sys.stderr)
        print(json.dumps(event, ensure_ascii=False, indent=2), file=sys.stderr)
        return 29, alarms

    return 0, alarms


def build_alarm(args: argparse.Namespace) -> dict[str, Any]:
    alarm_id = args.alarm_id or f"dock3-hms-mgmt-{uuid.uuid4().hex[:8]}"
    return {
        "id": alarm_id,
        "alarm_id": alarm_id,
        "level": args.alarm_level,
        "module": 3,
        "code": args.alarm_code,
        "device_type": args.device_type,
        "in_the_sky": args.in_the_sky,
        "imminent": args.imminent,
        "args": {
            "component_index": args.component_index,
            "sensor_index": args.sensor_index,
        },
        "hms_key": args.hms_key,
        "content": args.alarm_content,
    }


class Dock3HmsManagementClient(MqttRequestClient):
    def service(
        self,
        method: str,
        data: dict[str, Any] | None,
        timeout: float,
        allowed_results: set[int] | None = None,
        expected_status: str | None = None,
    ) -> tuple[int, dict[str, Any] | None]:
        return self.request(
            method,
            data or {},
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else allowed_results,
            expected_status=expected_status,
        )


def wait_for_hms_event(client: Dock3HmsManagementClient, previous_count: int, args: argparse.Namespace) -> int:
    rc, event = client.wait_event("hms", previous_count, args.timeout, expected_status="ok")
    if rc != 0:
        return rc
    rc, alarms = validate_hms_event(
        event,
        expect_min_alarms=args.expect_min_alarms,
        expect_alarm_id=args.expect_alarm_id,
        expect_code=args.expect_code,
        expect_level=args.expect_level if args.expect_level >= 0 else None,
    )
    if rc != 0:
        return rc
    print(f"HMS 事件校验通过: alarms={len(alarms)}, tid={event.get('tid') if event else ''}")
    return 0


def run_observe_only(client: Dock3HmsManagementClient, args: argparse.Namespace) -> int:
    print(f"等待真实链路 HMS 事件: topic=thing/product/{args.sn}/events, timeout={args.timeout}s")
    return wait_for_hms_event(client, client.event_count("hms"), args)


def run_injected_flow(client: Dock3HmsManagementClient, args: argparse.Namespace) -> int:
    alarm = build_alarm(args)
    alarm_id = alarm["id"]
    hms_before = client.event_count("hms")

    print(f"开始 Dock3 HMS 管理测试: alarm_id={alarm_id}, code={alarm['code']}")
    rc, reply = client.service(
        "hms_get",
        {
            "simulate": True,
            "alarms": [alarm],
            "page": 1,
            "page_size": 20,
        },
        args.timeout,
        {0},
        "ok",
    )
    if rc != 0:
        return rc

    reply_alarms = event_alarms(reply)
    if not any(item.get("id") == alarm_id or item.get("alarm_id") == alarm_id for item in reply_alarms):
        print(f"hms_get reply missing injected alarm: alarm_id={alarm_id}", file=sys.stderr)
        print(json.dumps(reply, ensure_ascii=False, indent=2), file=sys.stderr)
        return 30

    rc, event = client.wait_event("hms", hms_before, args.timeout, expected_status="ok")
    if rc != 0:
        return rc
    rc, alarms = validate_hms_event(
        event,
        expect_min_alarms=1,
        expect_alarm_id=alarm_id,
        expect_code=args.alarm_code,
        expect_level=args.alarm_level,
    )
    if rc != 0:
        return rc
    print(f"HMS 告警上报通过: alarm_id={alarm_id}, alarms={len(alarms)}")

    clear_before = client.event_count("hms")
    rc, cleared = client.service(
        "hms_clear",
        {"alarm_ids": [alarm_id]},
        args.timeout,
        {0},
        "ok",
    )
    if rc != 0:
        return rc
    if cleared.get("data", {}).get("cleared_count", 0) < 1:
        print(f"hms_clear did not clear injected alarm: alarm_id={alarm_id}", file=sys.stderr)
        print(json.dumps(cleared, ensure_ascii=False, indent=2), file=sys.stderr)
        return 31

    rc, clear_event = client.wait_event("hms", clear_before, args.timeout, expected_status="ok")
    if rc != 0:
        return rc
    rc, clear_alarms = validate_hms_event(clear_event, expect_min_alarms=0, allow_empty=True)
    if rc != 0:
        return rc
    if any(item.get("id") == alarm_id or item.get("alarm_id") == alarm_id for item in clear_alarms):
        print(f"hms clear event still contains alarm_id={alarm_id}", file=sys.stderr)
        print(json.dumps(clear_event, ensure_ascii=False, indent=2), file=sys.stderr)
        return 32
    print(f"HMS 清除通过: alarm_id={alarm_id}, remaining={len(clear_alarms)}")

    return 0


def run_simulation_rejected_check(client: Dock3HmsManagementClient, args: argparse.Namespace) -> int:
    alarm = build_alarm(args)
    rc, rejected = client.service(
        "hms_get",
        {"simulate": True, "alarms": [alarm]},
        args.timeout,
        {INVALID_COMMAND_FORMAT},
        "rejected",
    )
    if rc != 0:
        return rc
    print(f"生产模式模拟注入拒绝校验通过: result={result_code(rejected)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test DJI Dock3 HMS management over MQTT")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument(
        "--inject-via-hms-get",
        action="store_true",
        help="Drive the existing gateway hms_get/hms_clear hooks for local test-mode acceptance.",
    )
    parser.add_argument(
        "--expect-simulation-rejected",
        action="store_true",
        help="Assert simulated hms_get is rejected; use when the gateway runs production mode.",
    )
    parser.add_argument("--alarm-id", default="")
    parser.add_argument("--alarm-code", default="0x16100083")
    parser.add_argument("--alarm-level", type=int, choices=[0, 1, 2], default=2)
    parser.add_argument("--device-type", default="3-119-0")
    parser.add_argument("--in-the-sky", type=int, choices=[0, 1], default=0)
    parser.add_argument("--imminent", type=int, choices=[0, 1], default=1)
    parser.add_argument("--component-index", type=int, default=0)
    parser.add_argument("--sensor-index", type=int, default=0)
    parser.add_argument("--hms-key", default="dock3.hms.management.test")
    parser.add_argument("--alarm-content", default="Dock3 HMS management test alarm")
    parser.add_argument("--expect-min-alarms", type=int, default=1)
    parser.add_argument("--expect-alarm-id", default="")
    parser.add_argument("--expect-code", default="")
    parser.add_argument("--expect-level", type=int, default=-1, help="Expected HMS level in observe-only mode; -1 disables.")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.expect_min_alarms < 0:
        parser.error("--expect-min-alarms must be non-negative")
    if args.expect_simulation_rejected and args.inject_via_hms_get:
        parser.error("--expect-simulation-rejected cannot be combined with --inject-via-hms-get")

    client = Dock3HmsManagementClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_hms_management",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    try:
        if args.expect_simulation_rejected:
            rc = run_simulation_rejected_check(client, args)
        elif args.inject_via_hms_get:
            rc = run_injected_flow(client, args)
        else:
            rc = run_observe_only(client, args)
        if rc != 0:
            return rc
        ignored_total = client.stale_total()
        if args.fail_on_stale and ignored_total:
            print(
                "strict stale replies observed: "
                f"stats={client.stats}, samples={client.stale_sample_summary()}",
                file=sys.stderr,
            )
            return 40
        print(
            "dock3 hms management test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_mismatched={client.stats['ignored_mismatched']}, "
            f"ignored_legacy={client.stats['ignored_legacy']}, "
            f"ignored_late={client.stats['ignored_late']}, "
            f"ignored_duplicate={client.stats['ignored_duplicate']}, "
            f"ignored_unknown={client.unrelated_reply_total()}, "
            f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, "
            f"ignored_total={ignored_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
