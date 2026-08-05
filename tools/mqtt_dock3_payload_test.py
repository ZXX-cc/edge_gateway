#!/usr/bin/env python3
"""MQTT acceptance test for Dock3 camera and gimbal controls.

The script requires the gateway and its MAVLink/SITL link to be running.  It
checks both command replies and the gimbal status cache, so a failed command
cannot be mistaken for a successful local state update.
"""

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code
from src.utils.dji_error_codes import DOCK_SYSTEM_ERROR
from tool_config import add_config_argument, resolve_mqtt_settings


class Dock3PayloadClient(MqttRequestClient):
    def request(self, method, data, timeout, allowed_results=None):
        return super().request(
            method,
            data,
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else set(allowed_results),
        )

    def ignored_total(self):
        return self.stale_total()


def gimbal_state(reply: dict) -> dict:
    data = reply.get("data", {}) if isinstance(reply, dict) else {}
    return data if isinstance(data, dict) else {}


def assert_state(reply: dict, *, pitch=None, roll=None, yaw=None, mode=None) -> bool:
    state = gimbal_state(reply)
    expected = {"pitch": pitch, "roll": roll, "yaw": yaw, "mode": mode}
    for key, value in expected.items():
        if value is not None and state.get(key) != value:
            print(f"gimbal status mismatch: expected {key}={value}, actual={state}", file=sys.stderr)
            return False
    return True


def gimbal_payload_state(message: dict) -> dict:
    data = message.get("data", {}) if isinstance(message, dict) else {}
    if not isinstance(data, dict):
        return {}
    payload = data.get("payload")
    if isinstance(payload, dict):
        return payload
    payloads = data.get("payloads")
    if isinstance(payloads, list):
        for item in payloads:
            if isinstance(item, dict) and item.get("id", item.get("payload_type")) == "gimbal":
                return item
    return {}


def assert_gimbal_payload_state(message: dict, *, pitch=None, roll=None, yaw=None, mode=None) -> bool:
    payload = gimbal_payload_state(message)
    gimbal = payload.get("gimbal") if isinstance(payload, dict) else None
    if not isinstance(gimbal, dict):
        print(f"gimbal payload state missing: {message}", file=sys.stderr)
        return False
    if payload.get("payload_type") != "gimbal":
        print(f"payload_type mismatch: {payload}", file=sys.stderr)
        return False
    expected = {"pitch": pitch, "roll": roll, "yaw": yaw, "mode": mode}
    for key, value in expected.items():
        if value is not None and gimbal.get(key) != value:
            print(f"gimbal payload state mismatch: expected {key}={value}, actual={payload}", file=sys.stderr)
            return False
    return True


def run_payload_checks(client: Dock3PayloadClient, args) -> int:
    rc, camera_capability = client.request("payload_capability_query", {}, args.timeout, [0])
    if rc != 0:
        return rc
    if "supported_payloads" not in camera_capability.get("data", {}):
        print(f"payload capability missing supported_payloads: {camera_capability}", file=sys.stderr)
        return 10

    rc, gimbal_capability = client.request("gimbal_capability_query", {}, args.timeout, [0])
    if rc != 0:
        return rc
    if "pitch_range" not in gimbal_capability.get("data", {}):
        print(f"gimbal capability missing pitch_range: {gimbal_capability}", file=sys.stderr)
        return 11

    rc, payload_status = client.request("payload_status_query", {}, args.timeout, [0])
    if rc != 0:
        return rc
    if not assert_gimbal_payload_state(payload_status):
        return 17

    previous_payload_events = client.event_count("payload_status_notify")
    rc, _ = client.request(
        "gimbal_rotate",
        {"pitch": args.pitch, "roll": args.roll, "yaw": args.yaw},
        args.timeout,
        [0],
    )
    if rc != 0:
        return rc
    rc, status = client.request("gimbal_status_query", {}, args.timeout, [0])
    if rc != 0:
        return rc
    if not assert_state(status, pitch=args.pitch, roll=args.roll, yaw=args.yaw):
        return 12
    if not assert_gimbal_payload_state(status, pitch=args.pitch, roll=args.roll, yaw=args.yaw):
        return 18
    rc, event = client.wait_event(
        "payload_status_notify",
        previous_payload_events,
        args.event_timeout,
        expected_status=None,
    )
    if rc != 0:
        return rc
    if not assert_gimbal_payload_state(event, pitch=args.pitch, roll=args.roll, yaw=args.yaw):
        return 19

    rc, _ = client.request("set_gimbal_mode", {"mode": "FREE"}, args.timeout, [0])
    if rc != 0:
        return rc
    rc, status = client.request("gimbal_status_query", {}, args.timeout, [0])
    if rc != 0:
        return rc
    if not assert_state(status, mode=1):
        return 13

    rc, _ = client.request("gimbal_reset", {}, args.timeout, [0])
    if rc != 0:
        return rc
    rc, status = client.request("gimbal_status_query", {}, args.timeout, [0])
    if rc != 0:
        return rc
    if not assert_state(status, pitch=0, roll=0, yaw=0):
        return 14

    rc, _ = client.request("camera_photo_take", {"camera_index": 0}, args.timeout, [0])
    if rc != 0:
        return rc

    rc, _ = client.request(
        "camera_recording_start",
        {"camera_index": 0, "resolution": "1080p", "framerate": 30},
        args.timeout,
        [0],
    )
    if rc != 0:
        return rc
    rc, stopped = client.request("camera_recording_stop", {"camera_index": 0}, args.timeout, [0])
    if rc != 0:
        return rc
    if stopped.get("data", {}).get("recording") is not False:
        print(f"camera_recording_stop did not clear recording state: {stopped}", file=sys.stderr)
        return 15

    rc, rejected = client.request(
        "camera_recording_stop",
        {"camera_index": 0},
        args.timeout,
        [DOCK_SYSTEM_ERROR],
    )
    if rc != 0:
        return rc
    if result_code(rejected) != DOCK_SYSTEM_ERROR:
        print(f"second camera_recording_stop did not return official error: {rejected}", file=sys.stderr)
        return 16
    return 0


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 camera/gimbal MQTT controls")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--event-timeout", type=float, default=8.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--pitch", type=float, default=-20.0)
    parser.add_argument("--roll", type=float, default=0.0)
    parser.add_argument("--yaw", type=float, default=30.0)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = Dock3PayloadClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_payload",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    try:
        rc = run_payload_checks(client, args)
        if rc != 0:
            return rc
        ignored_total = client.ignored_total()
        if args.fail_on_stale and ignored_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 9
        print(
            "dock3 payload test ok "
            f"(accepted={client.stats['accepted']}, "
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
