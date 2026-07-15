#!/usr/bin/env python3
"""Acceptance test for DJI Dock3 remote debugging over MQTT.

The tested chain is:

platform MQTT -> gateway -> nest simulator / device manager -> flight control side.

All Dock3 remote-debug commands are sent through:

    thing/product/{gateway_sn}/services

and replies are expected on:

    thing/product/{gateway_sn}/services_reply

Job-style commands must also publish a same-method progress event on
``thing/product/{gateway_sn}/events``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, output_status, result_code  # noqa: E402
from src.cloud_api.dock3_cmd import OFFICIAL_DOCK3_CMD_METHODS  # noqa: E402
from src.utils.dji_error_codes import (  # noqa: E402
    DEVICE_STATE_UNSUPPORTED,
    DOCK_COMMUNICATION_ERROR,
    INVALID_COMMAND_FORMAT,
)
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


REMOTE_DEBUG_METHODS = {
    "debug_mode_open",
    "debug_mode_close",
    "cover_open",
    "cover_close",
    "cover_force_close",
    "charge_open",
    "charge_close",
    "drone_open",
    "drone_close",
    "drone_format",
    "device_format",
    "device_reboot",
    "battery_maintenance_switch",
    "air_conditioner_mode_switch",
    "alarm_state_switch",
    "battery_store_mode_switch",
    "sdr_workmode_switch",
    "supplement_light_open",
    "supplement_light_close",
}

LEGACY_REMOTE_DEBUG_METHODS = {
    "putter_open",
    "putter_close",
    "device_reset",
    "format_sd_card",
}


class Dock3RemoteDebugClient(MqttRequestClient):
    def service(
        self,
        method: str,
        data: dict[str, Any] | None,
        timeout: float,
        *,
        allowed_results: set[int] | None = None,
        expected_status: str | None = "ok",
    ) -> tuple[int, dict[str, Any] | None]:
        return self.request(
            method,
            data or {},
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else allowed_results,
            expected_status=expected_status,
        )

    def property_get(self, names: list[str], timeout: float) -> tuple[int, dict[str, Any]]:
        rc, reply = self.service(
            "property_get",
            {"properties": names},
            timeout,
            allowed_results={0},
            expected_status=None,
        )
        if rc != 0:
            return rc, {}
        data = reply.get("data", {}) if isinstance(reply, dict) else {}
        return 0, data if isinstance(data, dict) else {}


def assert_official_scope(include_legacy: bool) -> None:
    missing = REMOTE_DEBUG_METHODS - OFFICIAL_DOCK3_CMD_METHODS
    if missing:
        raise AssertionError(f"unexpected non-official Dock3 methods: {sorted(missing)}")
    if include_legacy:
        print(
            "legacy remote-debug compatibility enabled: "
            f"{', '.join(sorted(LEGACY_REMOTE_DEBUG_METHODS))}"
        )


def read_property(client: Dock3RemoteDebugClient, args: argparse.Namespace, name: str) -> tuple[int, Any]:
    rc, data = client.property_get([name], args.timeout)
    if rc != 0:
        return rc, None
    return 0, data.get(name)


def require_property(
    client: Dock3RemoteDebugClient,
    args: argparse.Namespace,
    name: str,
    expected: Any,
    *,
    strict: bool,
) -> int:
    rc, actual = read_property(client, args, name)
    if rc != 0:
        return rc
    if actual != expected:
        message = f"property {name} mismatch: expected={expected!r}, actual={actual!r}"
        if strict:
            print(message, file=sys.stderr)
            return 11
        print(f"warning: {message}; command reply/event accepted, readback is non-strict")
    return 0


def run_switch(
    client: Dock3RemoteDebugClient,
    args: argparse.Namespace,
    method: str,
    data: dict[str, Any],
    property_name: str | None = None,
    expected_value: Any = None,
    *,
    strict_readback: bool = True,
    allowed_results: set[int] | None = None,
) -> int:
    rc, payload = client.service(
        method,
        data,
        args.timeout,
        allowed_results=allowed_results,
        expected_status=None if allowed_results and DOCK_COMMUNICATION_ERROR in allowed_results else "ok",
    )
    if rc != 0:
        return rc
    print(f"开关命令通过: {method}, result={result_code(payload)}, status={output_status(payload)}")
    if property_name is None:
        return 0
    return require_property(
        client,
        args,
        property_name,
        expected_value,
        strict=strict_readback,
    )


def run_job(
    client: Dock3RemoteDebugClient,
    args: argparse.Namespace,
    method: str,
    data: dict[str, Any] | None = None,
    property_name: str | None = None,
    expected_value: Any = None,
    *,
    strict_readback: bool = True,
    allowed_results: set[int] | None = None,
    expected_status: str | None = "ok",
    require_event: bool = True,
) -> int:
    before = client.event_count(method)
    rc, payload = client.service(
        method,
        data or {},
        args.timeout,
        allowed_results=allowed_results,
        expected_status=expected_status,
    )
    if rc != 0:
        return rc
    print(f"动作命令通过: {method}, result={result_code(payload)}, status={output_status(payload)}")

    if require_event and result_code(payload) == 0:
        rc, event = client.wait_event(method, before, args.timeout, expected_status=expected_status)
        if rc != 0:
            return rc
        print(f"动作事件通过: {method}, status={output_status(event)}")

    if property_name is None:
        return 0
    return require_property(
        client,
        args,
        property_name,
        expected_value,
        strict=strict_readback,
    )


def run_remote_debug_flow(client: Dock3RemoteDebugClient, args: argparse.Namespace) -> int:
    print(f"开始 Dock3 远程调试测试: sn={args.sn}")

    rc = run_switch(client, args, "debug_mode_open", {}, "mode_code", 2)
    if rc != 0:
        return rc

    job_cases: list[tuple[str, dict[str, Any], str | None, Any, bool]] = [
        ("cover_open", {}, "cover_state", 1, False),
        ("cover_close", {}, "cover_state", 0, False),
        ("charge_open", {}, "charging_state", 1, False),
        ("charge_close", {}, "charging_state", 0, False),
        ("drone_open", {}, None, None, True),
        ("drone_close", {}, None, None, True),
    ]
    if args.include_legacy_putter:
        job_cases.extend([
            ("putter_open", {}, "putter_state", 1, False),
            ("putter_close", {}, "putter_state", 0, False),
        ])

    for method, data, prop, expected, strict in job_cases:
        rc = run_job(client, args, method, data, prop, expected, strict_readback=strict)
        if rc != 0:
            return rc
        time.sleep(args.step_delay)

    rc, drone_in_dock = read_property(client, args, "drone_in_dock")
    if rc != 0:
        return rc
    if drone_in_dock == 1:
        rc = run_job(
            client,
            args,
            "cover_force_close",
            {},
            allowed_results={DEVICE_STATE_UNSUPPORTED},
            expected_status="rejected",
            require_event=False,
        )
    else:
        rc = run_job(client, args, "cover_force_close", {}, "cover_state", 0, strict_readback=False)
    if rc != 0:
        return rc

    switch_cases = [
        ("supplement_light_open", {}, "supplement_light_state", 1, False, {0}),
        ("supplement_light_close", {}, "supplement_light_state", 0, False, {0}),
        ("battery_maintenance_switch", {"action": 1}, "battery_maintenance_mode", 1, False, {0, DOCK_COMMUNICATION_ERROR}),
        ("battery_maintenance_switch", {"action": 0}, "battery_maintenance_mode", 0, False, {0, DOCK_COMMUNICATION_ERROR}),
        ("air_conditioner_mode_switch", {"action": 2}, "air_conditioner_mode", 2, False, {0, DOCK_COMMUNICATION_ERROR}),
        ("air_conditioner_mode_switch", {"action": 0}, "air_conditioner_mode", 0, False, {0, DOCK_COMMUNICATION_ERROR}),
        ("alarm_state_switch", {"action": 1}, "alarm_state", 1, False, {0, DOCK_COMMUNICATION_ERROR}),
        ("alarm_state_switch", {"action": 0}, "alarm_state", 0, False, {0, DOCK_COMMUNICATION_ERROR}),
        ("battery_store_mode_switch", {"action": 2}, "battery_store_mode", 2, False, {0, DOCK_COMMUNICATION_ERROR}),
        ("battery_store_mode_switch", {"action": 1}, "battery_store_mode", 1, False, {0, DOCK_COMMUNICATION_ERROR}),
        ("sdr_workmode_switch", {"link_workmode": 1}, None, None, False, {0, DOCK_COMMUNICATION_ERROR}),
        ("sdr_workmode_switch", {"link_workmode": 0}, None, None, False, {0, DOCK_COMMUNICATION_ERROR}),
    ]
    for method, data, prop, expected, strict, allowed in switch_cases:
        rc = run_switch(
            client,
            args,
            method,
            data,
            prop,
            expected,
            strict_readback=strict,
            allowed_results=allowed,
        )
        if rc != 0:
            return rc
        time.sleep(args.step_delay)

    maintenance_jobs = ["drone_format", "device_format", "device_reboot"]
    if args.include_legacy_putter:
        maintenance_jobs.extend(["format_sd_card", "device_reset"])
    for method in maintenance_jobs:
        rc = run_job(
            client,
            args,
            method,
            {},
            allowed_results={0, DOCK_COMMUNICATION_ERROR},
            expected_status=None,
            require_event=False,
        )
        if rc != 0:
            return rc
        time.sleep(args.step_delay)

    invalid_cases = [
        ("battery_maintenance_switch", {"action": 2}),
        ("air_conditioner_mode_switch", {"action": 9}),
        ("alarm_state_switch", {"action": 2}),
        ("battery_store_mode_switch", {"action": 9}),
        ("sdr_workmode_switch", {"link_workmode": 9}),
    ]
    for method, data in invalid_cases:
        rc, payload = client.service(
            method,
            data,
            args.timeout,
            allowed_results={INVALID_COMMAND_FORMAT},
            expected_status="rejected",
        )
        if rc != 0:
            return rc
        print(f"异常分支通过: {method}, result={result_code(payload)}, status={output_status(payload)}")

    rc = run_switch(client, args, "debug_mode_close", {}, "mode_code", 0)
    if rc != 0:
        return rc

    required_events = ["cover_open", "cover_close", "charge_open", "charge_close"]
    if args.include_legacy_putter:
        required_events.extend(["putter_open", "putter_close"])
    missing_events = [method for method in required_events if not client.has_event(method)]
    if missing_events:
        print(f"missing expected remote-debug events: {missing_events}", file=sys.stderr)
        return 12

    stale_total = client.stale_total()
    if args.fail_on_stale and stale_total:
        print(
            "strict stale replies observed: "
            f"stats={client.stats}, samples={client.stale_sample_summary()}",
            file=sys.stderr,
        )
        return 13

    print(
        "dock3 remote debug test ok "
        f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
        f"ignored_mismatched={client.stats['ignored_mismatched']}, "
        f"ignored_legacy={client.stats['ignored_legacy']}, "
        f"ignored_late={client.stats['ignored_late']}, "
        f"ignored_duplicate={client.stats['ignored_duplicate']}, "
        f"ignored_unknown={client.unrelated_reply_total()}, "
        f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, "
        f"ignored_total={stale_total})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test DJI Dock3 remote-debug commands over MQTT")
    add_config_argument(parser)
    parser.add_argument("--host", default=None, help="MQTT broker host; default comes from config")
    parser.add_argument("--port", type=int, default=None, help="MQTT broker port; default comes from config")
    parser.add_argument("--sn", default=None, help="Gateway SN; default comes from config")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--step-delay", type=float, default=0.1)
    parser.add_argument("--include-legacy-putter", action="store_true", help="also test local legacy putter/format aliases")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.step_delay < 0:
        parser.error("--step-delay must be non-negative")

    assert_official_scope(args.include_legacy_putter)

    client = Dock3RemoteDebugClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_remote_debug",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        return run_remote_debug_flow(client, args)
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
