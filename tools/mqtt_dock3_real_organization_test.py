#!/usr/bin/env python3
"""Platform-side organization test for a real running Edge Gateway.

This tool does not instantiate DeviceManager or simulate the gateway. It acts
only as the cloud/platform side:

* subscribes to thing/product/{sn}/requests from the real gateway;
* replies on thing/product/{sn}/requests_reply;
* optionally verifies real gateway events and update_topo status messages.

For the startup organization flow, start this tool first and then start
src/main.py, or use --start-gateway to let the tool launch the real gateway.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from threading import Event
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.cloud_api.dock3_device import validate_update_topo_payload
from tool_config import add_config_argument, resolve_mqtt_settings

mqtt = None


ORGANIZATION_METHODS = {
    "airport_bind_status",
    "airport_organization_get",
    "airport_organization_bind",
}


def now_ms() -> int:
    return int(time.time() * 1000)


def build_client(client_id: str):
    if hasattr(mqtt, "CallbackAPIVersion"):
        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            pass
    return mqtt.Client(client_id=client_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test Dock3 organization management with the real Edge Gateway."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument(
        "--expect-method",
        action="append",
        default=None,
        choices=sorted(ORGANIZATION_METHODS),
        help="Organization request method expected from the real gateway. Repeatable.",
    )
    parser.add_argument("--bind-status", choices=["bound", "unbound"], default="unbound")
    parser.add_argument("--organization-id", default="org_sim_001")
    parser.add_argument("--organization-name", default="模拟组织")
    parser.add_argument("--device-callsign", default="")
    parser.add_argument("--organization-get-result", type=int, default=0)
    parser.add_argument("--organization-get-message", default="")
    parser.add_argument("--organization-bind-result", type=int, default=0)
    parser.add_argument("--organization-bind-message", default="")
    parser.add_argument("--organization-bind-err-code", type=int, default=327000)
    parser.add_argument("--organization-bind-err-msg", default="设备绑定失败")
    parser.add_argument("--organization-bind-fail-sn", action="append", default=[])
    parser.add_argument(
        "--expect-event",
        action="append",
        default=[],
        help="Require a gateway event method such as device_binding. Repeatable.",
    )
    parser.add_argument(
        "--expect-update-topo",
        action="store_true",
        help="Require real gateway to publish sys/product/{sn}/status update_topo.",
    )
    parser.add_argument(
        "--validate-update-topo-schema",
        action="store_true",
        help="Validate update_topo payload with project Dock3 topology schema.",
    )
    parser.add_argument(
        "--expect-sub-device-count",
        type=int,
        default=None,
        help="Expected update_topo data.sub_devices count.",
    )
    parser.add_argument("--start-gateway", action="store_true")
    parser.add_argument("--gateway-device-secret", default="sim-gateway-secret")
    parser.add_argument("--gateway-nonce", default="sim-gateway-nonce")
    parser.add_argument(
        "--gateway-extra-arg",
        action="append",
        default=[],
        help="Extra argument passed to src/main.py. Repeat for multiple args.",
    )
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    if args.expect_method is None:
        args.expect_method = ["airport_bind_status"]
    return args


def build_reply(request: dict[str, Any], data: dict[str, Any], gateway_sn: str) -> dict[str, Any]:
    msg = data.pop("_reply_msg", "") if isinstance(data, dict) else ""
    reply = {
        "bid": request.get("bid") or str(uuid.uuid4()),
        "data": data,
        "gateway": request.get("gateway", gateway_sn),
        "tid": request.get("tid", ""),
        "timestamp": now_ms(),
        "method": request.get("method", ""),
    }
    if msg:
        reply["msg"] = msg
    return reply


def validate_request(method: str, request: dict[str, Any]) -> list[str]:
    data = request.get("data", {})
    errors: list[str] = []
    if method == "airport_bind_status":
        devices = data.get("devices")
        if not isinstance(devices, list) or not devices:
            errors.append("airport_bind_status requires non-empty data.devices[]")
        else:
            for index, device in enumerate(devices):
                if not isinstance(device, dict) or not device.get("sn"):
                    errors.append(f"data.devices[{index}].sn is required")
    elif method == "airport_organization_get":
        if not data.get("device_binding_code"):
            errors.append("data.device_binding_code is required")
        if not data.get("organization_id"):
            errors.append("data.organization_id is required")
    elif method == "airport_organization_bind":
        bind_devices = data.get("bind_devices")
        if not isinstance(bind_devices, list) or not bind_devices:
            errors.append("airport_organization_bind requires non-empty data.bind_devices[]")
        else:
            required = {
                "sn",
                "device_binding_code",
                "organization_id",
                "device_callsign",
                "device_model_key",
            }
            for index, device in enumerate(bind_devices):
                if not isinstance(device, dict):
                    errors.append(f"data.bind_devices[{index}] must be object")
                    continue
                missing = sorted(key for key in required if not device.get(key))
                if missing:
                    errors.append(f"data.bind_devices[{index}] missing: {', '.join(missing)}")
    return errors


def response_for(request: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    method = request.get("method", "")
    data = request.get("data", {})
    if method == "airport_bind_status":
        is_bound = args.bind_status == "bound"
        devices = data.get("devices") or [{"sn": args.sn}]
        return {
            "result": 0,
            "output": {
                "bind_status": [
                    {
                        "sn": device.get("sn") or args.sn,
                        "is_device_bind_organization": is_bound,
                        "organization_id": args.organization_id if is_bound else "",
                        "organization_name": args.organization_name if is_bound else "",
                        "device_callsign": (
                            args.device_callsign or device.get("sn") or args.sn
                        ) if is_bound else "",
                    }
                    for device in devices
                    if isinstance(device, dict)
                ]
            },
        }

    if method == "airport_organization_get":
        output = {}
        if args.organization_get_result == 0:
            output["organization_name"] = args.organization_name
        response = {"result": args.organization_get_result, "output": output}
        if args.organization_get_message:
            response["_reply_msg"] = args.organization_get_message
        return response

    if method == "airport_organization_bind":
        bind_devices = data.get("bind_devices") or []
        request_sns = {device.get("sn") for device in bind_devices if isinstance(device, dict)}
        err_infos = []
        for sn in args.organization_bind_fail_sn:
            if not request_sns or sn in request_sns:
                err_infos.append({
                    "sn": sn,
                    "err_code": args.organization_bind_err_code,
                    "err_msg": args.organization_bind_err_msg,
                })
        response = {
            "result": args.organization_bind_result,
            "output": {"err_infos": err_infos},
        }
        if args.organization_bind_message:
            response["_reply_msg"] = args.organization_bind_message
        return response

    return {"result": 314000, "output": {}}


def start_gateway(args: argparse.Namespace) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "src/main.py",
        "-c",
        args.config,
        "--gateway-device-secret",
        args.gateway_device_secret,
        "--gateway-nonce",
        args.gateway_nonce,
        *args.gateway_extra_arg,
    ]
    print("+ " + " ".join(cmd))
    return subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def stop_gateway(proc: subprocess.Popen | None) -> None:
    if not proc:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
    output = ""
    if proc.stdout:
        try:
            output = proc.stdout.read()
        except Exception:
            output = ""
    if output:
        print("\n===== real gateway output =====")
        print(output[-8000:].rstrip())


def main() -> int:
    global mqtt
    try:
        import paho.mqtt.client as mqtt_module
    except ImportError as exc:
        raise SystemExit("paho-mqtt is required. Activate .venv and install requirements first.") from exc
    mqtt = mqtt_module

    args = parse_args()
    expected_methods = set(args.expect_method)
    expected_events = set(args.expect_event)
    seen_methods: set[str] = set()
    seen_events: set[str] = set()
    seen_update_topo = Event()
    done = Event()
    errors: list[str] = []
    gateway_proc = None

    request_topic = f"thing/product/{args.sn}/requests"
    reply_topic = f"thing/product/{args.sn}/requests_reply"
    event_topic = f"thing/product/{args.sn}/events"
    status_topic = f"sys/product/{args.sn}/status"
    status_reply_topic = f"sys/product/{args.sn}/status_reply"

    client = build_client(f"edge_gateway_real_org_cloud_{uuid.uuid4().hex[:8]}")

    def maybe_done() -> None:
        if expected_methods <= seen_methods and expected_events <= seen_events:
            if not args.expect_update_topo or seen_update_topo.is_set():
                done.set()

    def on_connect(client, userdata, flags, rc):
        if rc != 0:
            errors.append(f"MQTT connect failed rc={rc}")
            done.set()
            return
        client.subscribe(request_topic, qos=args.qos)
        client.subscribe(event_topic, qos=args.qos)
        client.subscribe(status_topic, qos=args.qos)
        print(f"platform subscribed -> {request_topic}")
        print(f"platform replies    -> {reply_topic}")
        print(f"platform observes   -> {event_topic}")
        print(f"platform observes   -> {status_topic}")
        if args.start_gateway:
            nonlocal_gateway["proc"] = start_gateway(args)

    nonlocal_gateway = {"proc": None}

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            errors.append(f"invalid JSON on {msg.topic}: {exc}")
            return

        method = payload.get("method", "")
        print(f"received <- {msg.topic}, method={method}, tid={payload.get('tid', '')}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        if msg.topic == request_topic:
            if method in ORGANIZATION_METHODS:
                seen_methods.add(method)
                request_errors = validate_request(method, payload)
                errors.extend(f"{method}: {error}" for error in request_errors)
            response = response_for(payload, args)
            reply = build_reply(payload, response, args.sn)
            client.publish(reply_topic, json.dumps(reply, ensure_ascii=False), qos=args.qos)
            print(f"published -> {reply_topic}")
            print(json.dumps(reply, ensure_ascii=False, indent=2))
            maybe_done()
            return

        if msg.topic == event_topic:
            if method:
                seen_events.add(method)
            maybe_done()
            return

        if msg.topic == status_topic:
            if method == "update_topo":
                if args.validate_update_topo_schema:
                    valid, schema_errors = validate_update_topo_payload(payload.get("data", {}))
                    errors.extend(f"update_topo: {error}" for error in schema_errors if not valid)
                if args.expect_sub_device_count is not None:
                    sub_devices = payload.get("data", {}).get("sub_devices", [])
                    if len(sub_devices) != args.expect_sub_device_count:
                        errors.append(
                            "update_topo sub_devices count mismatch: "
                            f"expected={args.expect_sub_device_count}, actual={len(sub_devices)}"
                        )
                seen_update_topo.set()
                reply = build_reply(payload, {"result": 0}, args.sn)
                client.publish(status_reply_topic, json.dumps(reply, ensure_ascii=False), qos=args.qos)
                print(f"published -> {status_reply_topic}")
                print(json.dumps(reply, ensure_ascii=False, indent=2))
            maybe_done()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    try:
        if not done.wait(args.timeout):
            missing = sorted(expected_methods - seen_methods)
            missing_events = sorted(expected_events - seen_events)
            if missing:
                errors.append(f"missing organization requests from real gateway: {', '.join(missing)}")
            if missing_events:
                errors.append(f"missing events from real gateway: {', '.join(missing_events)}")
            if args.expect_update_topo and not seen_update_topo.is_set():
                errors.append("missing update_topo status from real gateway")
        time.sleep(0.3)
    finally:
        gateway_proc = nonlocal_gateway["proc"]
        client.loop_stop()
        client.disconnect()
        stop_gateway(gateway_proc)

    if errors:
        print("dock3 real organization test failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(
        "dock3 real organization test ok "
        f"(requests={sorted(seen_methods)}, events={sorted(seen_events)}, "
        f"update_topo={seen_update_topo.is_set()})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
