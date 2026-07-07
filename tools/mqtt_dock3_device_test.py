#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 device topology."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code
from src.cloud_api.dock3_device import (
    GATEWAY_DOMAIN,
    GATEWAY_SUB_TYPE,
    GATEWAY_TYPE,
    THING_VERSION,
    build_sub_device_payload,
    build_update_topo_payload,
    validate_update_topo_payload,
)
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, resolve_mqtt_settings


def now_ms():
    return int(time.time() * 1000)


def build_status_reply(status_msg, sn, result=0):
    return {
        "bid": str(uuid.uuid4()),
        "data": {"result": result},
        "gateway": sn,
        "tid": status_msg.get("tid", ""),
        "timestamp": now_ms(),
        "method": status_msg.get("method", ""),
    }


class DeviceTopologyClient(MqttRequestClient):
    def __init__(self, host, port, sn, qos, quiet_stale=False):
        super().__init__(
            host,
            port,
            sn,
            qos,
            quiet_stale=quiet_stale,
            client_id_prefix="edge_gateway_dock3_device",
            subscribe_status=True,
        )
        self.status_validation_errors = []

    def _handle_status_message(self, payload):
        print(f"received <- {self.status_topic}, method={payload.get('method')}, tid={payload.get('tid')}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if payload.get("method") == "update_topo":
            valid, errors = validate_update_topo_payload(payload.get("data", {}))
            if errors:
                self.status_validation_errors.extend(errors)
            self.status_messages_by_method.setdefault("update_topo", []).append(payload)
            self.stats["status"] += 1
            reply = build_status_reply(payload, self.sn, result=0 if valid else INVALID_COMMAND_FORMAT)
            self.publish_json(self.status_reply_topic, reply)
            print(f"published -> {self.status_reply_topic}")
            print(json.dumps(reply, ensure_ascii=False, indent=2))
            self.status_signal.set()

    def request(self, method, data, timeout, expected_result):
        rc, reply = super().request(method, data, timeout, channel="services")
        if rc != 0:
            return rc, reply
        result = result_code(reply)
        if result != expected_result:
            print(
                f"{method} unexpected result: expected={expected_result}, actual={result}",
                file=sys.stderr,
            )
            return 3, reply
        return 0, reply

    def wait_for_new_status(self, previous_count, timeout):
        return self.wait_status("update_topo", previous_count, timeout)


def assert_status_payload(status_msg, expected_count, expected_sn=None):
    if not status_msg:
        print("timeout waiting for update_topo status", file=sys.stderr)
        return 4

    data = status_msg.get("data", {})
    valid, errors = validate_update_topo_payload(data)
    if not valid:
        for error in errors:
            print(error, file=sys.stderr)
        return 5

    sub_devices = data.get("sub_devices", [])
    if len(sub_devices) != expected_count:
        print(
            f"update_topo sub_devices count mismatch: expected={expected_count}, actual={len(sub_devices)}",
            file=sys.stderr,
        )
        return 6

    if expected_sn:
        sns = {device.get("sn") for device in sub_devices}
        if expected_sn not in sns:
            print(f"missing expected sub-device sn={expected_sn}", file=sys.stderr)
            return 7
    return 0


def online_payload(args):
    return build_update_topo_payload(
        domain=GATEWAY_DOMAIN,
        device_type=GATEWAY_TYPE,
        sub_type=GATEWAY_SUB_TYPE,
        device_secret=args.gateway_secret,
        nonce=args.gateway_nonce,
        thing_version=THING_VERSION,
        sub_devices=[
            build_sub_device_payload(
                args.sub_device_sn,
                domain=args.sub_device_domain,
                device_type=args.sub_device_type,
                sub_type=args.sub_device_sub_type,
                index=args.sub_device_index,
                device_secret=args.sub_device_secret,
                nonce=args.sub_device_nonce,
                thing_version=args.sub_device_thing_version,
            )
        ],
    )


def offline_payload(args):
    return build_update_topo_payload(
        domain=GATEWAY_DOMAIN,
        device_type=GATEWAY_TYPE,
        sub_type=GATEWAY_SUB_TYPE,
        device_secret=args.gateway_secret,
        nonce=args.gateway_nonce,
        thing_version=THING_VERSION,
        sub_devices=[],
    )


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 device/topology category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--gateway-secret", default="gw-secret")
    parser.add_argument("--gateway-nonce", default="gw-nonce")
    parser.add_argument("--sub-device-sn", default="DRONE-SIM-001")
    parser.add_argument("--sub-device-domain", default="0")
    parser.add_argument("--sub-device-type", type=int, default=60)
    parser.add_argument("--sub-device-sub-type", type=int, default=0)
    parser.add_argument("--sub-device-index", default="A")
    parser.add_argument("--sub-device-secret", default="drone-secret")
    parser.add_argument("--sub-device-nonce", default="drone-nonce")
    parser.add_argument("--sub-device-thing-version", default=THING_VERSION)
    parser.add_argument("--skip-offline-check", action="store_true")
    parser.add_argument("--skip-invalid-check", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument(
        "--no-status-window",
        type=float,
        default=1.0,
        help="Seconds to watch for unexpected status after invalid update_topo.",
    )
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = DeviceTopologyClient(args.host, args.port, args.sn, args.qos, quiet_stale=args.quiet_stale)
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        before = client.status_count("update_topo")
        rc, _ = client.request("update_topo", online_payload(args), args.timeout, expected_result=0)
        if rc != 0:
            return rc
        rc = assert_status_payload(
            client.wait_for_new_status(before, args.timeout),
            expected_count=1,
            expected_sn=args.sub_device_sn,
        )
        if rc != 0:
            return rc

        if not args.skip_offline_check:
            before = client.status_count("update_topo")
            rc, _ = client.request("update_topo", offline_payload(args), args.timeout, expected_result=0)
            if rc != 0:
                return rc
            rc = assert_status_payload(
                client.wait_for_new_status(before, args.timeout),
                expected_count=0,
            )
            if rc != 0:
                return rc

        if not args.skip_invalid_check:
            before = client.status_count("update_topo")
            invalid_payload = {"sub_devices": []}
            rc, _ = client.request(
                "update_topo",
                invalid_payload,
                args.timeout,
                expected_result=INVALID_COMMAND_FORMAT,
            )
            if rc != 0:
                return rc
            unexpected = client.wait_for_new_status(before, args.no_status_window)
            if unexpected is not None:
                print("invalid update_topo unexpectedly produced a status update", file=sys.stderr)
                return 8

        if client.status_validation_errors:
            for error in client.status_validation_errors:
                print(error, file=sys.stderr)
            return 9

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 10

        print(
            "dock3 device topology test ok "
            f"(status_messages={client.status_count('update_topo')}, sub_device_sn={args.sub_device_sn}, "
            f"accepted={client.stats['accepted']}, status={client.stats['status']}, "
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
