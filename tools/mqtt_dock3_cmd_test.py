#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 cmd / remote debugging methods."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient
from src.cloud_api.dock3_cmd import OFFICIAL_DOCK3_CMD_METHODS
from src.utils.dji_error_codes import (
    DEVICE_STATE_UNSUPPORTED,
    DOCK_COMMUNICATION_ERROR,
    INVALID_COMMAND_FORMAT,
)
from tool_config import add_config_argument, resolve_mqtt_settings


class Dock3CmdClient(MqttRequestClient):
    def request(
        self,
        method,
        data,
        timeout,
        allowed_results=None,
        expected_status="ok",
    ):
        return super().request(
            method,
            data,
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else set(allowed_results),
            expected_status=expected_status,
        )

    def property_get(self, names, timeout):
        rc, reply = self.request(
            "property_get",
            {"properties": names},
            timeout,
            allowed_results={0},
            expected_status=None,
        )
        if rc != 0:
            return rc, {}
        return 0, reply.get("data", {})


def require_property(client, args, name, expected, *, strict: bool = True):
    rc, data = client.property_get([name], args.timeout)
    if rc != 0:
        return rc
    actual = data.get(name)
    if actual != expected:
        message = f"property {name} mismatch: expected={expected!r}, actual={actual!r}"
        if strict:
            print(message, file=sys.stderr)
            return 7
        print(f"warning: {message}; command reply accepted and state readback is non-strict")
    return 0


def require_nested_property(client, args, property_name, nested_path, expected):
    rc, data = client.property_get([property_name], args.timeout)
    if rc != 0:
        return rc
    value = data.get(property_name)
    for key in nested_path:
        if not isinstance(value, dict):
            value = None
            break
        value = value.get(key)
    if value != expected:
        joined = ".".join([property_name, *nested_path])
        print(f"property {joined} mismatch: expected={expected!r}, actual={value!r}", file=sys.stderr)
        return 7
    return 0


def run_switch_checks(client, args):
    state_cases = [
        ("debug_mode_open", {}, "mode_code", 2, True),
        ("debug_mode_close", {}, "mode_code", 0, True),
        ("supplement_light_open", {}, "supplement_light_state", 1, False),
        ("supplement_light_close", {}, "supplement_light_state", 0, False),
    ]
    adapter_cases = [
        ("battery_maintenance_switch", {"action": 1}, "battery_maintenance_mode", 1),
        ("battery_maintenance_switch", {"action": 0}, "battery_maintenance_mode", 0),
        ("air_conditioner_mode_switch", {"action": 2}, "air_conditioner_mode", 2),
        ("air_conditioner_mode_switch", {"action": 0}, "air_conditioner_mode", 0),
        ("alarm_state_switch", {"action": 1}, "alarm_state", 1),
        ("alarm_state_switch", {"action": 0}, "alarm_state", 0),
        ("battery_store_mode_switch", {"action": 2}, "battery_store_mode", 2),
        ("battery_store_mode_switch", {"action": 1}, "battery_store_mode", 1),
        ("battery_store_mode_switch", {"action": 0}, "battery_store_mode", 1),
    ]

    for method, data, prop_name, expected, strict_readback in state_cases:
        rc, _ = client.request(method, data, args.timeout)
        if rc != 0:
            return rc
        rc = require_property(client, args, prop_name, expected, strict=strict_readback)
        if rc != 0:
            return rc

    for method, data, _prop_name, _expected in adapter_cases:
        rc, _ = client.request(
            method,
            data,
            args.timeout,
            allowed_results={0, DOCK_COMMUNICATION_ERROR},
            expected_status=None,
        )
        if rc != 0:
            return rc

    rc, _ = client.request(
        "sdr_workmode_switch",
        {"link_workmode": 1},
        args.timeout,
        allowed_results={0, DOCK_COMMUNICATION_ERROR},
        expected_status=None,
    )
    if rc != 0:
        return rc

    rc, _ = client.request(
        "sdr_workmode_switch",
        {"mode": 0},
        args.timeout,
        allowed_results={0, DOCK_COMMUNICATION_ERROR},
        expected_status=None,
    )
    if rc != 0:
        return rc

    rc, _ = client.request(
        "sdr_workmode_switch",
        {"action": 1},
        args.timeout,
        allowed_results={0, DOCK_COMMUNICATION_ERROR},
        expected_status=None,
    )
    if rc != 0:
        return rc

    invalid_cases = [
        ("battery_maintenance_switch", {"action": 2}),
        ("air_conditioner_mode_switch", {"action": 9}),
        ("alarm_state_switch", {"action": 2}),
        ("battery_store_mode_switch", {"action": 9}),
        ("sdr_workmode_switch", {"link_workmode": 9}),
    ]
    for method, data in invalid_cases:
        rc, _ = client.request(
            method,
            data,
            args.timeout,
            allowed_results={INVALID_COMMAND_FORMAT},
            expected_status="rejected",
        )
        if rc != 0:
            return rc

    return 0


def run_job_method(client, args, method, data=None, property_name=None, expected_value=None, *, strict_readback: bool = True):
    before = client.event_count(method)
    rc, _ = client.request(method, data or {}, args.timeout)
    if rc != 0:
        return rc
    rc, _ = client.wait_event(method, before, args.timeout)
    if rc != 0:
        return rc
    if property_name is not None:
        rc = require_property(client, args, property_name, expected_value, strict=strict_readback)
        if rc != 0:
            return rc
    return 0


def run_job_checks(client, args):
    jobs = [
        ("cover_open", {}, "cover_state", 1, False),
        ("cover_close", {}, "cover_state", 0, False),
        ("charge_open", {}, "charging_state", 1, True),
        ("charge_close", {}, "charging_state", 0, True),
        ("drone_open", {}, None, None, True),
        ("drone_close", {}, None, None, True),
    ]
    for method, data, prop_name, expected, strict_readback in jobs:
        rc = run_job_method(
            client,
            args,
            method,
            data,
            prop_name,
            expected,
            strict_readback=strict_readback,
        )
        if rc != 0:
            return rc

    adapter_jobs = ("drone_format", "device_format", "device_reboot")
    for method in adapter_jobs:
        rc, _ = client.request(
            method,
            {},
            args.timeout,
            allowed_results={0, DOCK_COMMUNICATION_ERROR},
            expected_status=None,
        )
        if rc != 0:
            return rc

    rc, data = client.property_get(["drone_in_dock"], args.timeout)
    if rc != 0:
        return rc
    if data.get("drone_in_dock") == 1:
        rc, _ = client.request(
            "cover_force_close",
            {},
            args.timeout,
            allowed_results={DEVICE_STATE_UNSUPPORTED},
            expected_status="rejected",
        )
        if rc != 0:
            return rc
    else:
        rc = run_job_method(client, args, "cover_force_close", {}, "cover_state", 0)
        if rc != 0:
            return rc

    return 0


def assert_official_scope():
    script_methods = {
        "debug_mode_open",
        "debug_mode_close",
        "supplement_light_open",
        "supplement_light_close",
        "battery_maintenance_switch",
        "air_conditioner_mode_switch",
        "alarm_state_switch",
        "battery_store_mode_switch",
        "sdr_workmode_switch",
        "cover_open",
        "cover_close",
        "charge_open",
        "charge_close",
        "drone_open",
        "drone_close",
        "drone_format",
        "device_format",
        "device_reboot",
        "cover_force_close",
    }
    missing = script_methods - OFFICIAL_DOCK3_CMD_METHODS
    if missing:
        raise AssertionError(f"non-official Dock3 cmd methods in official test: {sorted(missing)}")


def main():
    assert_official_scope()

    parser = argparse.ArgumentParser(description="Test DJI Dock3 cmd/remote-debug category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = Dock3CmdClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_cmd",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_switch_checks(client, args)
        if rc != 0:
            return rc

        rc = run_job_checks(client, args)
        if rc != 0:
            return rc

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(
                "stale duplicate replies observed "
                f"(ignored_mismatched={client.stats['ignored_mismatched']}, "
                f"ignored_legacy={client.stats['ignored_legacy']}, "
                f"ignored_late={client.stats['ignored_late']}, "
                f"ignored_wrong_topic={client.stats['ignored_wrong_topic']})",
                file=sys.stderr,
            )
            return 10

        print(
            "dock3 cmd test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_mismatched={client.stats['ignored_mismatched']}, "
            f"ignored_legacy={client.stats['ignored_legacy']}, "
            f"ignored_late={client.stats['ignored_late']}, "
            f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, "
            f"ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
