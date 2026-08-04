#!/usr/bin/env python3
"""Cloud-side MQTT simulator for DJI Dock3 requests/requests_reply flow."""

import argparse
import json
import sys
import time
import uuid
from threading import Event
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.cloud_api.dock3_device import validate_update_topo_payload as validate_dock3_update_topo_payload
from tool_config import add_config_argument, resolve_mqtt_credentials, resolve_mqtt_settings

mqtt = None


def build_client(client_id):
    if hasattr(mqtt, "CallbackAPIVersion"):
        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            pass
    return mqtt.Client(client_id=client_id)


def now_ms():
    return int(time.time() * 1000)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Simulate cloud replies for DJI Dock3 device-originated MQTT requests."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--once", action="store_true", help="Reply to one request and exit.")
    parser.add_argument(
        "--max-requests",
        type=int,
        default=0,
        help="Reply to N MQTT messages and exit. 0 means run forever.",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="Timeout for --once mode.")
    parser.add_argument("--bind-status", choices=["bound", "unbound"], default="unbound")
    parser.add_argument("--organization-id", default="org_sim_001")
    parser.add_argument("--organization-name", default="模拟组织")
    parser.add_argument("--device-callsign", default="")
    parser.add_argument("--reply-delay", type=float, default=0.0, help="Delay each reply by N seconds.")
    parser.add_argument("--organization-get-result", type=int, default=0)
    parser.add_argument("--organization-get-message", default="")
    parser.add_argument("--organization-bind-result", type=int, default=0)
    parser.add_argument("--organization-bind-message", default="")
    parser.add_argument("--organization-bind-err-code", type=int, default=327000)
    parser.add_argument("--organization-bind-err-msg", default="设备绑定失败")
    parser.add_argument(
        "--organization-bind-fail-sn",
        action="append",
        default=[],
        help="SN to include in airport_organization_bind output.err_infos. Repeat for multiple devices.",
    )
    parser.add_argument(
        "--enable-status",
        action="store_true",
        help="Also subscribe to sys/product/{sn}/status and publish status_reply.",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Only handle sys/product/{sn}/status messages, useful for update_topo validation.",
    )
    parser.add_argument("--status-result", type=int, default=0)
    parser.add_argument("--status-message", default="")
    parser.add_argument(
        "--expect-status-sub-device-count",
        type=int,
        default=None,
        help="Validate update_topo data.sub_devices length on sys/product/{sn}/status.",
    )
    parser.add_argument(
        "--expect-status-sub-device-sn",
        action="append",
        default=[],
        help="Require a sub-device SN in update_topo data.sub_devices. Repeat for multiple SNs.",
    )
    parser.add_argument(
        "--validate-status-topo-schema",
        action="store_true",
        help="Validate required top-level and sub-device keys in update_topo payload.",
    )
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.username, args.password = resolve_mqtt_credentials(args)
    return args


def build_reply(request, data, gateway_sn):
    reply_msg = ""
    if isinstance(data, dict):
        reply_msg = data.pop("_reply_msg", "")

    reply = {
        "bid": request.get("bid") or str(uuid.uuid4()),
        "data": data,
        "gateway": request.get("gateway", gateway_sn),
        "tid": request.get("tid", ""),
        "timestamp": now_ms(),
        "method": request.get("method", ""),
    }
    if reply_msg:
        reply["msg"] = reply_msg
    return reply


def _request_devices(request, gateway_sn):
    devices = request.get("data", {}).get("devices", [])
    if not devices:
        return [{"sn": gateway_sn}]
    return devices


def airport_bind_status_response(request, args):
    is_bound = args.bind_status == "bound"
    statuses = []

    for device in _request_devices(request, args.sn):
        sn = device.get("sn") or args.sn
        statuses.append({
            "sn": sn,
            "is_device_bind_organization": is_bound,
            "organization_id": args.organization_id if is_bound else "",
            "organization_name": args.organization_name if is_bound else "",
            "device_callsign": (args.device_callsign or sn) if is_bound else "",
        })

    return {
        "result": 0,
        "output": {
            "bind_status": statuses,
        },
    }


def airport_organization_get_response(request, args):
    output = {}
    if args.organization_get_result == 0:
        output["organization_name"] = args.organization_name

    response = {
        "result": args.organization_get_result,
        "output": output,
    }
    if args.organization_get_message:
        response["_reply_msg"] = args.organization_get_message
    return response


def airport_organization_bind_response(request, args):
    err_infos = []
    request_devices = request.get("data", {}).get("bind_devices", [])
    request_sns = {device.get("sn") for device in request_devices if device.get("sn")}

    for sn in args.organization_bind_fail_sn:
        if not request_sns or sn in request_sns:
            err_infos.append({
                "sn": sn,
                "err_code": args.organization_bind_err_code,
                "err_msg": args.organization_bind_err_msg,
            })

    response = {
        "result": args.organization_bind_result,
        "output": {
            "err_infos": err_infos,
        },
    }
    if args.organization_bind_message:
        response["_reply_msg"] = args.organization_bind_message
    return response


def response_for(request, args):
    method = request.get("method", "")
    if method == "airport_bind_status":
        return airport_bind_status_response(request, args)
    if method == "airport_organization_get":
        return airport_organization_get_response(request, args)
    if method == "airport_organization_bind":
        return airport_organization_bind_response(request, args)

    return {
        "result": 314000,
        "output": {},
    }


def validate_update_topo_payload(request, args):
    data = request.get("data", {})
    errors = []

    if args.validate_status_topo_schema:
        valid, schema_errors = validate_dock3_update_topo_payload(data)
        if not valid:
            errors.extend(schema_errors)

    sub_devices = data.get("sub_devices", [])
    if args.expect_status_sub_device_count is not None:
        actual_count = len(sub_devices)
        if actual_count != args.expect_status_sub_device_count:
            errors.append(
                "status sub_devices count mismatch: "
                f"expected={args.expect_status_sub_device_count}, actual={actual_count}"
            )

    if args.expect_status_sub_device_sn:
        actual_sns = {device.get("sn") for device in sub_devices if isinstance(device, dict)}
        missing_sns = sorted(set(args.expect_status_sub_device_sn) - actual_sns)
        if missing_sns:
            errors.append(f"missing sub-device SNs: {', '.join(missing_sns)}")

    if args.validate_status_topo_schema:
        required_sub_device_keys = {
            "sn", "domain", "type", "sub_type", "index",
            "device_secret", "nonce", "thing_version",
        }
        for index, device in enumerate(sub_devices):
            if not isinstance(device, dict):
                errors.append(f"sub_devices[{index}] is not an object")
                continue
            missing = sorted(required_sub_device_keys - set(device.keys()))
            if missing:
                errors.append(f"sub_devices[{index}] missing keys: {', '.join(missing)}")

    return errors


def main():
    global mqtt
    args = parse_args()
    max_requests = 1 if args.once else max(args.max_requests, 0)
    try:
        import paho.mqtt.client as mqtt_module
    except ImportError as exc:
        raise SystemExit("paho-mqtt is required. Install requirements-ubuntu.txt first.") from exc

    mqtt = mqtt_module
    request_topic = f"thing/product/{args.sn}/requests"
    reply_topic = f"thing/product/{args.sn}/requests_reply"
    status_topic = f"sys/product/{args.sn}/status"
    status_reply_topic = f"sys/product/{args.sn}/status_reply"
    done = Event()
    handled = {"count": 0}
    exit_code = {"value": 0}

    client = build_client(f"edge_gateway_cloud_sim_{uuid.uuid4().hex[:8]}")
    if args.username:
        client.username_pw_set(args.username, args.password)

    def on_connect(client, userdata, flags, rc):
        if rc != 0:
            print(f"connect failed: rc={rc}", file=sys.stderr)
            done.set()
            return
        if not args.status_only:
            client.subscribe(request_topic, qos=args.qos)
            print(f"cloud simulator subscribed -> {request_topic}")
            print(f"cloud simulator replies  -> {reply_topic}")
        if args.enable_status or args.status_only:
            client.subscribe(status_topic, qos=args.qos)
            print(f"cloud simulator subscribed -> {status_topic}")
            print(f"cloud simulator replies  -> {status_reply_topic}")

    def on_message(client, userdata, msg):
        try:
            request = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            print(f"invalid JSON on {msg.topic}: {exc}", file=sys.stderr)
            return

        if msg.topic == status_topic:
            validation_errors = validate_update_topo_payload(request, args)
            if validation_errors:
                for error in validation_errors:
                    print(error, file=sys.stderr)
                exit_code["value"] = 5
            data = {
                "result": args.status_result,
            }
            if args.status_message:
                data["_reply_msg"] = args.status_message
            publish_topic = status_reply_topic
        else:
            data = response_for(request, args)
            publish_topic = reply_topic

        reply = build_reply(request, data, args.sn)
        payload = json.dumps(reply, ensure_ascii=False)
        if args.reply_delay > 0:
            time.sleep(args.reply_delay)
        client.publish(publish_topic, payload, qos=args.qos)
        handled["count"] += 1

        print(f"received <- {msg.topic}")
        print(json.dumps(request, ensure_ascii=False, indent=2))
        print(f"published -> {publish_topic}")
        print(json.dumps(reply, ensure_ascii=False, indent=2))

        if max_requests and handled["count"] >= max_requests:
            done.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    try:
        if max_requests:
            if not done.wait(args.timeout):
                print("timeout waiting for MQTT message", file=sys.stderr)
                return 2
            time.sleep(0.3)
            return exit_code["value"] if handled["count"] else 1

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
