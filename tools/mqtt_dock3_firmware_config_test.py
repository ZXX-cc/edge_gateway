#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 firmware/config protocol."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code
from src.cloud_api.dock3_cmd import (
    TASK_STATUS_CANCELED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_OK,
    TASK_STATUS_REJECTED,
)
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, resolve_mqtt_settings


class Dock3FirmwareConfigClient(MqttRequestClient):
    def request(self, method, data, timeout, allowed_results=None, expected_status=None):
        return super().request(
            method,
            data,
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else set(allowed_results),
            expected_status=expected_status,
        )

    def ignored_total(self):
        return self.stale_total()


def assert_config_value(payload: dict, key: str, expected) -> bool:
    config = payload.get("data", {}).get("config", {})
    return isinstance(config, dict) and config.get(key) == expected


def firmware_device(sn: str, version: str, file_name: str = "firmware.bin") -> dict:
    return {
        "sn": sn,
        "product_version": version,
        "file_url": f"mock://{file_name}",
        "file_size": 1024,
        "md5": "mock-md5",
        "file_name": file_name,
        "firmware_upgrade_type": 3,
    }


def run_firmware_config_checks(client: Dock3FirmwareConfigClient, args) -> int:
    config_key = "dock3_test_config"
    config_value = "enabled"

    rc, _ = client.request("config_get", {}, args.timeout, [0], TASK_STATUS_OK)
    if rc != 0:
        return rc

    update_events = client.event_count("config_update_result")
    rc, updated = client.request(
        "config_update",
        {"config": {config_key: config_value, "silent_mode": 1}},
        args.timeout,
        [0],
        TASK_STATUS_OK,
    )
    if rc != 0:
        return rc
    revision = updated.get("data", {}).get("config_revision")
    if not isinstance(revision, int) or revision < 1:
        print(f"config_update missing revision: {updated}", file=sys.stderr)
        return 10
    rc, _ = client.wait_event(
        "config_update_result",
        update_events,
        args.timeout,
        expected_status=TASK_STATUS_OK,
    )
    if rc != 0:
        return rc

    rc, fetched = client.request("config_get", {"keys": [config_key, "silent_mode"]}, args.timeout, [0], TASK_STATUS_OK)
    if rc != 0:
        return rc
    if not assert_config_value(fetched, config_key, config_value):
        print(f"config_get missing updated value: {fetched}", file=sys.stderr)
        return 11

    rollback_events = client.event_count("config_rollback_result")
    rc, _ = client.request("config_rollback", {}, args.timeout, [0], TASK_STATUS_OK)
    if rc != 0:
        return rc
    rc, _ = client.wait_event(
        "config_rollback_result",
        rollback_events,
        args.timeout,
        expected_status=TASK_STATUS_OK,
    )
    if rc != 0:
        return rc

    ota_progress_before = client.event_count("ota_progress")
    ota_result_before = client.event_count("ota_result")
    rc, ota = client.request(
        "ota_create",
        {
            "simulate": True,
            "auto_complete": False,
            "devices": [firmware_device(args.sn, "9.9.9-test", "firmware-cancel.bin")],
        },
        args.timeout,
        [0],
        TASK_STATUS_IN_PROGRESS,
    )
    if rc != 0:
        return rc
    if not ota.get("data", {}).get("task_id"):
        print(f"ota_create missing task_id: {ota}", file=sys.stderr)
        return 12
    rc, _ = client.wait_event("ota_progress", ota_progress_before, args.timeout, expected_status=None)
    if rc != 0:
        return rc

    rc, _ = client.request("ota_cancel", {}, args.timeout, [0], TASK_STATUS_CANCELED)
    if rc != 0:
        return rc
    rc, _ = client.wait_event(
        "ota_result",
        ota_result_before,
        args.timeout,
        expected_status=TASK_STATUS_CANCELED,
    )
    if rc != 0:
        return rc

    result_before = client.event_count("ota_result")
    rc, fw = client.request(
        "firmware_upgrade",
        {
            "simulate": True,
            "auto_complete": True,
            "devices": [firmware_device(args.sn, "9.9.10-test", "firmware-ok.bin")],
        },
        args.timeout,
        [0],
        TASK_STATUS_IN_PROGRESS,
    )
    if rc != 0:
        return rc
    if fw.get("data", {}).get("target_version") != "9.9.10-test":
        print(f"firmware_upgrade target version mismatch: {fw}", file=sys.stderr)
        return 13
    rc, _ = client.wait_event("ota_result", result_before, args.timeout, expected_status=TASK_STATUS_OK)
    if rc != 0:
        return rc

    rc, invalid = client.request(
        "ota_create",
        {},
        args.timeout,
        [INVALID_COMMAND_FORMAT],
        TASK_STATUS_REJECTED,
    )
    if rc != 0:
        return rc
    if result_code(invalid) != INVALID_COMMAND_FORMAT:
        print(f"invalid ota_create did not return official error: {invalid}", file=sys.stderr)
        return 14
    return 0


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 firmware/config category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = Dock3FirmwareConfigClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_firmware_config",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    try:
        rc = run_firmware_config_checks(client, args)
        if rc != 0:
            return rc
        ignored_total = client.ignored_total()
        if args.fail_on_stale and ignored_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 9
        print(
            "dock3 firmware/config test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_mismatched={client.stats['ignored_mismatched']}, "
            f"ignored_legacy={client.stats['ignored_legacy']}, "
            f"ignored_late={client.stats['ignored_late']}, "
            f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, "
            f"ignored_total={ignored_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
