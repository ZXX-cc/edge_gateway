#!/usr/bin/env python3
"""DJI Dock 3 device-management full-chain MQTT acceptance.

This is a cloud-side validation tool for the real-chain startup profile in
docs/REAL_CHAIN_STARTUP.md. It focuses on the Dock3 device-management path:

* cloud triggers update_topo through services
* gateway reports sys/product/{sn}/status with method=update_topo
* cloud replies sys/product/{sn}/status_reply
* property_get verifies the compact sub_device state snapshot

Potentially disruptive checks such as offline/reconnect and unbind are opt-in.
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code
from tool_config import add_config_argument, resolve_mqtt_settings
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


def now_ms() -> int:
    return int(time.time() * 1000)


def build_status_reply(status_msg: Dict[str, Any], sn: str, result: int = 0) -> Dict[str, Any]:
    return {
        "bid": str(uuid.uuid4()),
        "data": {"result": result},
        "gateway": sn,
        "tid": status_msg.get("tid", ""),
        "timestamp": now_ms(),
        "method": status_msg.get("method", ""),
    }


class DeviceManagementClient(MqttRequestClient):
    def __init__(self, host: str, port: int, sn: str, qos: int, quiet_stale: bool = False):
        super().__init__(
            host,
            port,
            sn,
            qos,
            quiet_stale=quiet_stale,
            subscribe_events=True,
            subscribe_status=True,
            client_id_prefix="edge_gateway_dock3_real_device_management",
        )
        self.status_validation_errors: List[str] = []
        self.status_reply_sent = 0

    def _handle_status_message(self, payload: Dict[str, Any]) -> None:
        method = payload.get("method") if isinstance(payload, dict) else None
        if method != "update_topo":
            return super()._handle_status_message(payload)

        valid, errors = validate_update_topo_payload(payload.get("data", {}), strict=True)
        if payload.get("need_reply") != 1:
            valid = False
            errors = [*errors, "update_topo status must set need_reply=1"]
        if errors:
            self.status_validation_errors.extend(errors)

        self.status_messages_by_method.setdefault("update_topo", []).append(payload)
        self.stats["status"] += 1
        reply = build_status_reply(payload, self.sn, result=0 if valid else INVALID_COMMAND_FORMAT)
        self.publish_json(self.status_reply_topic, reply)
        self.status_reply_sent += 1
        self.status_signal.set()

        if not self.quiet_stale:
            print(f"received <- {self.status_topic}, method=update_topo, tid={payload.get('tid')}")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            print(f"published -> {self.status_reply_topic}")
            print(json.dumps(reply, ensure_ascii=False, indent=2))

    def request_services(self, method: str, data: Dict[str, Any], timeout: float, expected_result: int) -> int:
        rc, reply = self.request(method, data, timeout, channel="services")
        if rc != 0:
            return rc
        actual = result_code(reply)
        if actual != expected_result:
            print(
                f"{method} unexpected result: expected={expected_result}, actual={actual}",
                file=sys.stderr,
            )
            if reply:
                print(json.dumps(reply, ensure_ascii=False, indent=2), file=sys.stderr)
            return 3
        return 0

    def wait_update_topo_status(self, previous_count: int, timeout: float) -> Optional[Dict[str, Any]]:
        return self.wait_status("update_topo", previous_count, timeout)


def parse_sub_device(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"sub-device JSON must be valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise argparse.ArgumentTypeError("sub-device JSON must be an object")
    if not data.get("sn"):
        raise argparse.ArgumentTypeError("sub-device JSON requires sn")
    return build_sub_device_payload(
        data.get("sn"),
        domain=data.get("domain", "0"),
        device_type=data.get("type", data.get("device_type", 60)),
        sub_type=data.get("sub_type", 0),
        index=data.get("index", "A"),
        device_secret=data.get("device_secret", "aircraft-secret"),
        nonce=data.get("nonce", "aircraft-nonce"),
        thing_version=data.get("thing_version", THING_VERSION),
    )


def build_sub_devices(args) -> List[Dict[str, Any]]:
    if args.sub_device_json:
        return [parse_sub_device(raw) for raw in args.sub_device_json]
    return [
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
    ]


def topo_payload(args, sub_devices: List[Dict[str, Any]]) -> Dict[str, Any]:
    return build_update_topo_payload(
        domain=args.gateway_domain,
        device_type=args.gateway_type,
        sub_type=args.gateway_sub_type,
        device_secret=args.gateway_secret,
        nonce=args.gateway_nonce,
        thing_version=args.thing_version,
        sub_devices=sub_devices,
    )


def assert_status_payload(
    status_msg: Optional[Dict[str, Any]],
    expected_count: int,
    expected_sns: List[str],
) -> int:
    if not status_msg:
        print("timeout waiting for sys/product status update_topo", file=sys.stderr)
        return 4

    data = status_msg.get("data", {})
    valid, errors = validate_update_topo_payload(data, strict=True)
    if status_msg.get("need_reply") != 1:
        errors.append("update_topo status missing need_reply=1")
    if not valid or errors:
        for error in errors:
            print(f"update_topo status schema error: {error}", file=sys.stderr)
        return 5

    sub_devices = data.get("sub_devices", [])
    if len(sub_devices) != expected_count:
        print(
            f"update_topo sub_devices count mismatch: expected={expected_count}, actual={len(sub_devices)}",
            file=sys.stderr,
        )
        return 6

    actual_sns = {item.get("sn") for item in sub_devices}
    missing = sorted(set(expected_sns) - actual_sns)
    if missing:
        print(f"update_topo missing expected sub-device SNs: {', '.join(missing)}", file=sys.stderr)
        return 7
    return 0


def assert_property_sub_device(reply: Optional[Dict[str, Any]], expected_sn: str, expected_online: int) -> int:
    data = (reply or {}).get("data", {})
    sub_device = data.get("sub_device")
    if not isinstance(sub_device, dict):
        print("property_get missing sub_device object", file=sys.stderr)
        return 20
    actual_sn = sub_device.get("device_sn")
    actual_online = sub_device.get("device_online_status")
    if actual_sn != expected_sn:
        print(f"sub_device.device_sn mismatch: expected={expected_sn}, actual={actual_sn}", file=sys.stderr)
        return 21
    if actual_online != expected_online:
        print(
            "sub_device.device_online_status mismatch: "
            f"expected={expected_online}, actual={actual_online}",
            file=sys.stderr,
        )
        return 22
    return 0


def property_get_sub_device(client: DeviceManagementClient, timeout: float) -> Optional[Dict[str, Any]]:
    rc, reply = client.request(
        "property_get",
        {"properties": ["sub_device"]},
        timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return None
    return reply


def wait_unbinding_events(client: DeviceManagementClient, before_count: int, expected_sns: List[str], timeout: float) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = client.events_by_method.get("device_unbinding", [])[before_count:]
        actual_sns = [event.get("data", {}).get("device_sn") for event in events]
        if set(expected_sns).issubset(set(actual_sns)):
            return 0
        remaining = deadline - time.time()
        client.event_signal.wait(max(0.05, min(0.5, remaining)))
        client.event_signal.clear()
    print(
        "timeout waiting device_unbinding events for "
        + ",".join(expected_sns),
        file=sys.stderr,
    )
    return 30


def write_evidence(args, summary: Dict[str, Any]) -> str:
    evidence_dir = args.evidence_dir
    if not evidence_dir:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        evidence_dir = os.path.join("logs", "acceptance_evidence", f"{timestamp}_dock3_real_device_management")
    os.makedirs(evidence_dir, exist_ok=True)
    path = os.path.join(evidence_dir, "dock3_real_device_management_summary.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return os.path.abspath(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real-chain DJI Dock3 device-management MQTT acceptance")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--gateway-domain", default=GATEWAY_DOMAIN)
    parser.add_argument("--gateway-type", type=int, default=GATEWAY_TYPE)
    parser.add_argument("--gateway-sub-type", type=int, default=GATEWAY_SUB_TYPE)
    parser.add_argument("--gateway-secret", default="gw-secret")
    parser.add_argument("--gateway-nonce", default="gw-nonce")
    parser.add_argument("--thing-version", default=THING_VERSION)
    parser.add_argument("--sub-device-sn", default="DRONESIM001")
    parser.add_argument("--sub-device-domain", default="0")
    parser.add_argument("--sub-device-type", type=int, default=60)
    parser.add_argument("--sub-device-sub-type", type=int, default=0)
    parser.add_argument("--sub-device-index", default="A")
    parser.add_argument("--sub-device-secret", default="aircraft-secret")
    parser.add_argument("--sub-device-nonce", default="aircraft-nonce")
    parser.add_argument("--sub-device-thing-version", default=THING_VERSION)
    parser.add_argument("--sub-device-json", action="append", default=[])
    parser.add_argument("--include-offline-reconnect", action="store_true")
    parser.add_argument("--include-unbind", action="store_true", help="Run disruptive unbind validation; this clears cached sub-devices.")
    parser.add_argument("--skip-invalid-check", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--evidence-dir", default="")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = DeviceManagementClient(args.host, args.port, args.sn, args.qos, quiet_stale=args.quiet_stale)
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        sub_devices = build_sub_devices(args)
        expected_sns = [device["sn"] for device in sub_devices]
        first_sn = expected_sns[0]

        status_before = client.status_count("update_topo")
        rc = client.request_services("update_topo", topo_payload(args, sub_devices), args.timeout, expected_result=0)
        if rc != 0:
            return rc
        rc = assert_status_payload(
            client.wait_update_topo_status(status_before, args.timeout),
            expected_count=len(sub_devices),
            expected_sns=expected_sns,
        )
        if rc != 0:
            return rc

        reply = property_get_sub_device(client, args.timeout)
        rc = assert_property_sub_device(reply, first_sn, expected_online=1)
        if rc != 0:
            return rc

        offline_reconnect_ok = False
        if args.include_offline_reconnect:
            status_before = client.status_count("update_topo")
            rc = client.request_services("update_topo", topo_payload(args, []), args.timeout, expected_result=0)
            if rc != 0:
                return rc
            rc = assert_status_payload(
                client.wait_update_topo_status(status_before, args.timeout),
                expected_count=0,
                expected_sns=[],
            )
            if rc != 0:
                return rc
            reply = property_get_sub_device(client, args.timeout)
            rc = assert_property_sub_device(reply, first_sn, expected_online=0)
            if rc != 0:
                return rc

            status_before = client.status_count("update_topo")
            rc = client.request_services("update_topo", topo_payload(args, sub_devices), args.timeout, expected_result=0)
            if rc != 0:
                return rc
            rc = assert_status_payload(
                client.wait_update_topo_status(status_before, args.timeout),
                expected_count=len(sub_devices),
                expected_sns=expected_sns,
            )
            if rc != 0:
                return rc
            offline_reconnect_ok = True

        invalid_rejected = False
        if not args.skip_invalid_check:
            status_before = client.status_count("update_topo")
            rc = client.request_services("update_topo", {"sub_devices": []}, args.timeout, expected_result=INVALID_COMMAND_FORMAT)
            if rc != 0:
                return rc
            if client.wait_update_topo_status(status_before, 1.0) is not None:
                print("invalid update_topo unexpectedly produced sys/status", file=sys.stderr)
                return 40
            invalid_rejected = True

        unbind_ok = False
        if args.include_unbind:
            status_before = client.status_count("update_topo")
            event_before = client.event_count("device_unbinding")
            rc = client.request_services("device_unbinding", {}, args.timeout, expected_result=0)
            if rc != 0:
                return rc
            rc = wait_unbinding_events(client, event_before, [args.sn, *expected_sns], args.timeout)
            if rc != 0:
                return rc
            rc = assert_status_payload(
                client.wait_update_topo_status(status_before, args.timeout),
                expected_count=0,
                expected_sns=[],
            )
            if rc != 0:
                return rc
            unbind_ok = True

        if client.status_validation_errors:
            for error in client.status_validation_errors:
                print(f"status validation error: {error}", file=sys.stderr)
            return 50

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 60

        summary = {
            "status": "passed",
            "gateway_sn": args.sn,
            "mqtt": {"host": args.host, "port": args.port, "qos": args.qos},
            "device_management_checks": {
                "update_topo_services_reply": True,
                "sys_status_update_topo": True,
                "status_reply_sent": client.status_reply_sent,
                "property_get_sub_device": True,
                "invalid_update_topo_rejected": invalid_rejected,
                "offline_reconnect": offline_reconnect_ok,
                "unbind": unbind_ok,
            },
            "sub_device_count": len(sub_devices),
            "sub_device_sns": expected_sns,
            "mqtt_stats": client.stats,
            "stale_total": stale_total,
            "timestamp": now_ms(),
        }
        evidence_path = write_evidence(args, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"Dock3 real device-management evidence -> {evidence_path}")
        print("dock3 real device-management test ok")
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
