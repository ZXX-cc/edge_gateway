#!/usr/bin/env python3
"""Exercise DJI Dock3 organization requests through project request managers."""

import argparse
import asyncio
import json
import sys
import time
from threading import Event, Thread
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient

Config = None
load_config = None
TopicBuilder = None
CloudConnectionManager = None
DeviceManager = None
SubDevice = None


def start_background_loop():
    loop = asyncio.new_event_loop()
    thread = Thread(target=loop.run_forever, name="organization-test-asyncio", daemon=True)
    thread.start()
    return loop, thread


def stop_background_loop(loop, thread):
    if not loop:
        return
    if loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
    if thread and thread.is_alive():
        thread.join(timeout=2.0)
    if not loop.is_closed() and not loop.is_running():
        loop.close()


class OrganizationRequestClient(MqttRequestClient):
    """Minimal MQTT client required by CloudConnectionManager.send_request()."""

    def __init__(self, host, port, gateway_sn, qos, mqtt_module=None):
        self.gateway_sn = gateway_sn
        self.connected = False
        self._reply_handler = None
        self._status_reply_handler = None
        self._requests_reply_topic = TopicBuilder.requests_reply(gateway_sn)
        self._status_reply_topic = TopicBuilder.status_reply(gateway_sn)
        super().__init__(
            host,
            port,
            gateway_sn,
            qos,
            quiet_stale=True,
            client_id_prefix="edge_gateway_org_test",
            observe_topics={self._requests_reply_topic, self._status_reply_topic},
            subscribe_default_replies=False,
            mqtt_module=mqtt_module,
        )

    def set_reply_handler(self, handler):
        self._reply_handler = handler

    def set_status_reply_handler(self, handler):
        self._status_reply_handler = handler

    def connect(self, timeout=10.0):
        self.client.on_disconnect = self._on_disconnect
        return super().connect(timeout)

    def disconnect(self):
        super().disconnect()
        self.connected = False

    def publish_requests(self, data):
        topic = TopicBuilder.requests(self.gateway_sn)
        result = self.publish_json(topic, data)
        print(f"published {data.get('method')} -> {topic}, tid={data.get('tid')}")
        return getattr(result, "rc", 0) == 0

    def publish_event(self, data):
        topic = TopicBuilder.events(self.gateway_sn)
        result = self.publish_json(topic, data)
        print(f"published {data.get('method')} -> {topic}, tid={data.get('tid')}")
        return getattr(result, "rc", 0) == 0

    def publish_status(self, data):
        topic = TopicBuilder.status(self.gateway_sn)
        result = self.publish_json(topic, data)
        print(f"published {data.get('method')} -> {topic}, tid={data.get('tid')}")
        return getattr(result, "rc", 0) == 0

    def _on_connect(self, client, userdata, flags, rc):
        super()._on_connect(client, userdata, flags, rc)
        self.connected = rc == 0

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False

    def _handle_observed_message(self, topic, payload):
        if not isinstance(payload, dict):
            print(f"invalid reply on {topic}", file=sys.stderr)
            return
        method = payload.get("method", "")
        if topic == self._status_reply_topic:
            print(f"received <- {topic}, method={method}, tid={payload.get('tid')}")
            if self._status_reply_handler:
                self._status_reply_handler(method, payload)
            return

        print(f"received <- {topic}, method={method}, tid={payload.get('tid')}")
        if self._reply_handler:
            self._reply_handler(method, payload)


def parse_args():
    parser = argparse.ArgumentParser(description="Dock3 organization requests test")
    parser.add_argument("--config", default="config/gateway.yaml")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--action", choices=["status", "get", "bind", "full"], default="full")
    parser.add_argument("--binding-code", default="SIM-BIND-CODE")
    parser.add_argument("--organization-id", default="org_sim_001")
    parser.add_argument("--device-callsign", default="")
    parser.add_argument("--device-model-key", default="dock3-sim")
    parser.add_argument(
        "--gateway-device-secret",
        default="",
        help="Inject gateway device_secret for generated update_topo during tests.",
    )
    parser.add_argument(
        "--gateway-nonce",
        default="",
        help="Inject gateway nonce for generated update_topo during tests.",
    )
    parser.add_argument("--expect-get-result", type=int, default=0)
    parser.add_argument("--expect-bind-result", type=int, default=0)
    parser.add_argument("--expect-bind-error-count", type=int, default=0)
    parser.add_argument(
        "--expect-bind-status",
        choices=["any", "bound", "unbound"],
        default="any",
        help="Validate airport_bind_status bind state when --action status is used.",
    )
    parser.add_argument(
        "--expect-bind-status-sn",
        action="append",
        default=[],
        help="Require a device SN in airport_bind_status output.bind_status. Repeat for multiple SNs.",
    )
    parser.add_argument("--expect-organization-name", default="")
    parser.add_argument("--expect-timeout", action="store_true")
    parser.add_argument("--expect-event", action="append", default=[], help="Require an emitted event method. Repeat for multiple events.")
    parser.add_argument(
        "--expect-device-binding-sn",
        action="append",
        default=[],
        help="Require a device_binding event for this SN. Repeat for gateway and sub-devices.",
    )
    parser.add_argument(
        "--expect-device-binding-count",
        type=int,
        default=None,
        help="Require an exact number of generated device_binding events.",
    )
    parser.add_argument("--expect-bound", action="store_true", help="Verify gateway in-memory binding state after bind.")
    parser.add_argument(
        "--expect-bound-sn",
        action="append",
        default=[],
        help="Verify a specific SN is bound in DeviceManager memory. Repeat for gateway and sub-devices.",
    )
    parser.add_argument("--verify-db", action="store_true", help="Verify device_binding row in SQLite after bind.")
    parser.add_argument(
        "--publish-events",
        action="store_true",
        help="Publish generated events to MQTT. update_topo is published to sys/product/{sn}/status.",
    )
    parser.add_argument("--expect-status-reply", action="store_true", help="Wait for sys/product/{sn}/status_reply.")
    parser.add_argument("--expect-status-result", type=int, default=0)
    parser.add_argument(
        "--sub-device-json",
        action="append",
        default=[],
        help=(
            "Add an online simulated sub-device before binding. "
            "Value is JSON with sn/domain/type/sub_type/index/device_secret/nonce/thing_version. "
            "Repeat for multiple sub-devices."
        ),
    )
    parser.add_argument(
        "--bind-sub-devices",
        action="store_true",
        help="Include installed sub-devices in airport_organization_bind bind_devices.",
    )
    parser.add_argument(
        "--expect-sub-device-paired-sn",
        action="append",
        default=[],
        help="Require an installed sub-device SN to be paired after binding. Repeat for multiple SNs.",
    )
    parser.add_argument(
        "--expect-topo-sub-device-count",
        type=int,
        default=None,
        help="Validate generated update_topo data.sub_devices length.",
    )
    parser.add_argument(
        "--expect-topo-sub-device-sn",
        action="append",
        default=[],
        help="Require a sub-device SN in generated update_topo data.sub_devices. Repeat for multiple SNs.",
    )
    parser.add_argument(
        "--validate-topo-schema",
        action="store_true",
        help="Validate required top-level and sub-device keys in generated update_topo payload.",
    )
    parser.add_argument("--verify-timeout", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    return parser.parse_args()


def wait_for_reply(label, timeout, send_fn, expected_result=0, expect_timeout=False, validate_fn=None):
    done = Event()
    response = {}

    def callback(method, msg):
        response["method"] = method
        response["msg"] = msg
        done.set()

    tid = send_fn(callback)
    if not tid:
        print(f"{label} was not sent", file=sys.stderr)
        return 1

    if not done.wait(timeout):
        if expect_timeout:
            print(f"expected timeout for {label}, tid={tid}")
            return 0
        print(f"timeout waiting for {label} reply, tid={tid}", file=sys.stderr)
        return 2

    if expect_timeout:
        print(f"{label} received a reply but timeout was expected", file=sys.stderr)
        return 4

    msg = response["msg"]
    print(json.dumps(msg, ensure_ascii=False, indent=2))
    result = msg.get("data", {}).get("result")
    if result != expected_result:
        print(f"{label} unexpected result: expected={expected_result}, actual={result}", file=sys.stderr)
        return 3

    if validate_fn:
        error = validate_fn(msg)
        if error:
            print(f"{label} validation failed: {error}", file=sys.stderr)
            return 5

    return 0


def wait_for_events(recorded_events, expected_methods, timeout):
    deadline = time.time() + timeout
    expected = set(expected_methods)
    while time.time() < deadline:
        seen = {event.get("method") for event in recorded_events}
        missing = expected - seen
        if not missing:
            print(f"expected events observed: {', '.join(sorted(expected))}")
            return 0
        time.sleep(0.1)

    seen = {event.get("method") for event in recorded_events}
    missing = expected - seen
    print(f"missing expected events: {', '.join(sorted(missing))}; seen={sorted(seen)}", file=sys.stderr)
    return 6


def _unique_ordered(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def parse_boolish(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "bound"}
    return bool(value)


def verify_device_binding_events(recorded_events, expected_sns=None, expected_count=None):
    events = [event for event in recorded_events if event.get("method") == "device_binding"]
    errors = []

    if expected_count is not None and len(events) != expected_count:
        errors.append(f"device_binding count mismatch: expected={expected_count}, actual={len(events)}")

    expected_sns = _unique_ordered(expected_sns or [])
    if expected_sns:
        actual_sns = {event.get("data", {}).get("device_sn") for event in events}
        missing_sns = sorted(set(expected_sns) - actual_sns)
        if missing_sns:
            errors.append(f"missing device_binding SNs: {', '.join(missing_sns)}")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(json.dumps({"device_binding_events": events}, ensure_ascii=False, indent=2))
        return 14

    if expected_sns or expected_count is not None:
        actual_sns = [event.get("data", {}).get("device_sn") for event in events]
        print(f"device_binding events ok: count={len(events)}, sns={','.join(actual_sns)}")
    return 0


def wait_for_status_reply(recorded_replies, reply_event, timeout, expected_result):
    if not reply_event.wait(timeout):
        print("timeout waiting for status_reply", file=sys.stderr)
        return 8

    msg = recorded_replies[-1]
    print(json.dumps(msg, ensure_ascii=False, indent=2))
    result = msg.get("data", {}).get("result")
    if result != expected_result:
        print(f"status_reply unexpected result: expected={expected_result}, actual={result}", file=sys.stderr)
        return 9

    print(f"status_reply ok: method={msg.get('method')}, result={result}")
    return 0


def parse_sub_device_definition(raw):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid --sub-device-json: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("--sub-device-json must be a JSON object")
    if not data.get("sn"):
        raise ValueError("--sub-device-json requires sn")
    return data


def install_sub_devices(device, definitions):
    for raw in definitions:
        data = parse_sub_device_definition(raw)
        sub_device = SubDevice.from_dict(data)
        sub_device.online = bool(data.get("online", True))
        sub_device.paired = bool(data.get("paired", True))
        if not device.add_sub_device(sub_device):
            raise ValueError(f"failed to add sub-device: {data.get('sn')}")
        print(
            "simulated sub-device added: "
            f"sn={sub_device.sn}, domain={sub_device.domain}, type={sub_device.type}, "
            f"sub_type={sub_device.sub_type}, online={sub_device.online}"
        )


def build_bind_devices(device, gateway_sn, args):
    bind_devices = [{
        "sn": gateway_sn,
        "device_binding_code": args.binding_code,
        "organization_id": args.organization_id,
        "device_callsign": args.device_callsign or gateway_sn,
        "device_model_key": args.device_model_key,
    }]
    if not args.bind_sub_devices:
        return bind_devices

    for sub_device in device.get_all_sub_devices():
        bind_devices.append({
            "sn": sub_device.sn,
            "device_binding_code": args.binding_code,
            "organization_id": args.organization_id,
            "device_callsign": sub_device.sn,
            "device_model_key": f"{sub_device.domain}-{sub_device.type}-{sub_device.sub_type}",
        })
    return bind_devices


def verify_topo_payload(recorded_events, expected_count=None, expected_sns=None, validate_schema=False):
    topo_events = [event for event in recorded_events if event.get("method") == "update_topo"]
    if not topo_events:
        print("update_topo event was not generated", file=sys.stderr)
        return 10

    latest = topo_events[-1]
    data = latest.get("data", {})
    sub_devices = data.get("sub_devices", [])
    errors = []

    if validate_schema:
        required_topo_keys = {
            "domain", "type", "sub_type", "device_secret",
            "nonce", "thing_version", "sub_devices",
        }
        missing = sorted(required_topo_keys - set(data.keys()))
        if missing:
            errors.append(f"missing update_topo keys: {', '.join(missing)}")

    if expected_count is not None:
        actual_count = len(sub_devices)
        if actual_count != expected_count:
            errors.append(f"update_topo sub_devices count mismatch: expected={expected_count}, actual={actual_count}")

    expected_sns = expected_sns or []
    if expected_sns:
        actual_sns = {device.get("sn") for device in sub_devices if isinstance(device, dict)}
        missing_sns = sorted(set(expected_sns) - actual_sns)
        if missing_sns:
            errors.append(f"missing sub-device SNs: {', '.join(missing_sns)}")

    if validate_schema:
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

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(json.dumps(latest, ensure_ascii=False, indent=2))
        return 11

    print(f"update_topo schema ok: method={latest.get('method')}, sub_devices={len(sub_devices)}")
    print(json.dumps({"update_topo_sub_devices": sub_devices}, ensure_ascii=False, indent=2))
    return 0


def verify_memory_binding(device, gateway_sn, organization_id):
    binding = device._get_device_binding_info(gateway_sn)
    org = device._org_data.get("organization", {})
    state_bound = device._device_state >= device.STATE_BOUND

    if not state_bound:
        return "device state is not bound"
    if not binding.get("is_bound"):
        return "device binding cache is not bound"
    if binding.get("organization_id") != organization_id:
        return f"binding organization_id expected={organization_id}, actual={binding.get('organization_id')}"
    if org.get("organization_id") != organization_id:
        return f"organization cache expected={organization_id}, actual={org.get('organization_id')}"

    print(f"memory binding ok: sn={gateway_sn}, organization_id={organization_id}")
    return ""


def verify_memory_binding_for_sn(device, sn, organization_id):
    binding = device._get_device_binding_info(sn)
    if not binding.get("is_bound"):
        return f"{sn} binding cache is not bound"
    if binding.get("organization_id") != organization_id:
        return f"{sn} organization_id expected={organization_id}, actual={binding.get('organization_id')}"
    print(f"memory binding ok: sn={sn}, organization_id={organization_id}")
    return ""


def verify_sub_device_paired(device, sns):
    for sn in sns:
        sub_device = device.get_sub_device(sn)
        if not sub_device:
            return f"sub-device not found: {sn}"
        if not sub_device.paired:
            return f"sub-device is not paired: {sn}"
        print(f"sub-device paired ok: sn={sn}")
    return ""


async def fetch_db_binding(gateway_sn):
    from src.database.connection import init_database_async, get_async_session_local
    from src.database.device_db import DeviceDB

    await init_database_async()
    AsyncSessionLocal = get_async_session_local()
    async with AsyncSessionLocal() as session:
        binding = await DeviceDB.get_device_binding(session, gateway_sn)
        if not binding:
            return None
        return {
            "gateway_sn": binding.gateway_sn,
            "workspace_id": binding.workspace_id,
            "workspace_name": binding.workspace_name,
            "organization_id": binding.organization_id,
            "organization_name": binding.organization_name,
            "is_bound": bool(binding.is_bound),
            "bind_time": binding.bind_time,
        }


def verify_db_binding(loop, gateway_sn, organization_id, timeout):
    deadline = time.time() + timeout
    last_binding = None

    while time.time() < deadline:
        future = asyncio.run_coroutine_threadsafe(fetch_db_binding(gateway_sn), loop)
        try:
            binding = future.result(timeout=max(0.2, min(2.0, deadline - time.time())))
        except Exception as exc:
            last_binding = {"error": str(exc)}
            time.sleep(0.2)
            continue

        last_binding = binding
        if binding and binding.get("is_bound") and binding.get("organization_id") == organization_id:
            print(json.dumps({"db_binding": binding}, ensure_ascii=False, indent=2))
            return 0
        time.sleep(0.2)

    print(
        f"database binding verification failed: expected organization_id={organization_id}, last={last_binding}",
        file=sys.stderr,
    )
    return 7


def main():
    global Config, load_config, TopicBuilder
    global CloudConnectionManager, DeviceManager, SubDevice

    args = parse_args()

    try:
        from src.utils.config import Config as ConfigClass, load_config as load_config_func
        from src.utils.message import TopicBuilder as TopicBuilderClass
        from src.cloud_api.connection_manager import CloudConnectionManager as CloudConnectionManagerClass
        from src.device.manager import DeviceManager as DeviceManagerClass, SubDevice as SubDeviceClass
    except ImportError as exc:
        raise SystemExit("Project dependencies are missing. Activate .venv and install requirements-ubuntu.txt first.") from exc

    Config = ConfigClass
    load_config = load_config_func
    TopicBuilder = TopicBuilderClass
    CloudConnectionManager = CloudConnectionManagerClass
    DeviceManager = DeviceManagerClass
    SubDevice = SubDeviceClass

    load_config(args.config)
    mqtt_config = Config.get_mqtt_config()
    gateway_sn = Config.get_gateway_sn()
    host = args.host or mqtt_config["host"]
    port = args.port or mqtt_config["port"]

    request_client = OrganizationRequestClient(host, port, gateway_sn, args.qos)
    cloud_conn = CloudConnectionManager(request_client)
    request_client.set_reply_handler(cloud_conn.handle_requests_reply)
    recorded_status_replies = []
    status_reply_event = Event()
    status_context = {"device": None}

    def handle_status_reply(method, msg):
        recorded_status_replies.append(msg)
        if status_context["device"] and method == "update_topo":
            status_context["device"].handle_update_topo_reply(method, msg)
        status_reply_event.set()

    if args.expect_status_reply:
        request_client.set_status_reply_handler(handle_status_reply)

    if not request_client.connect(timeout=args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    cloud_conn._cloud_connected = True
    cloud_conn._start_request_cleaner()

    async_loop, async_thread = start_background_loop()
    if args.verify_db:
        try:
            asyncio.run_coroutine_threadsafe(fetch_db_binding(gateway_sn), async_loop).result(timeout=args.verify_timeout)
        except Exception as exc:
            print(f"database initialization failed: {exc}", file=sys.stderr)
            cloud_conn._stop_request_cleaner()
            request_client.disconnect()
            stop_background_loop(async_loop, async_thread)
            return 7

    recorded_events = []
    recorded_states = []

    def record_event(event):
        recorded_events.append(event)
        print(f"event observed: method={event.get('method')}")
        if args.publish_events:
            if event.get("method") == "update_topo":
                request_client.publish_status(event)
            else:
                request_client.publish_event(event)

    def record_state(state):
        recorded_states.append(state)

    device = DeviceManager()
    if args.gateway_device_secret:
        device._gateway_device_secret = args.gateway_device_secret
    if args.gateway_nonce:
        device._gateway_nonce = args.gateway_nonce
    status_context["device"] = device
    device.set_event_loop(async_loop)
    device.set_cloud_connection(cloud_conn)
    if args.expect_event or args.expect_bound or args.verify_db or args.publish_events:
        device.set_callbacks(record_event, record_state)
    try:
        install_sub_devices(device, args.sub_device_json)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        cloud_conn._stop_request_cleaner()
        request_client.disconnect()
        device.stop()
        stop_background_loop(async_loop, async_thread)
        return 12

    def validate_get(msg):
        if not args.expect_organization_name:
            return ""
        org_name = msg.get("data", {}).get("output", {}).get("organization_name", "")
        if org_name != args.expect_organization_name:
            return f"organization_name expected={args.expect_organization_name}, actual={org_name}"
        return ""

    def validate_bind(msg):
        err_infos = msg.get("data", {}).get("output", {}).get("err_infos", [])
        if len(err_infos) != args.expect_bind_error_count:
            return f"err_infos count expected={args.expect_bind_error_count}, actual={len(err_infos)}"
        return ""

    def validate_bind_status(msg):
        bind_status = msg.get("data", {}).get("output", {}).get("bind_status", [])
        if not isinstance(bind_status, list):
            return "output.bind_status is not a list"

        expected_sns = _unique_ordered(args.expect_bind_status_sn)
        if expected_sns:
            actual_sns = {item.get("sn") for item in bind_status if isinstance(item, dict)}
            missing_sns = sorted(set(expected_sns) - actual_sns)
            if missing_sns:
                return f"missing bind_status SNs: {', '.join(missing_sns)}"

        if args.expect_bind_status != "any":
            expected_bound = args.expect_bind_status == "bound"
            mismatched = []
            for item in bind_status:
                if not isinstance(item, dict):
                    continue
                raw_bound = item.get("is_device_bind_organization")
                actual_bound = parse_boolish(raw_bound)
                if actual_bound != expected_bound:
                    mismatched.append(f"{item.get('sn', '<missing-sn>')}={raw_bound!r}")
            if mismatched:
                return (
                    f"bind_status mismatch for {', '.join(mismatched)}: "
                    f"expected={args.expect_bind_status}"
                )

        print(json.dumps({"bind_status": bind_status}, ensure_ascii=False, indent=2))
        return ""

    try:
        if args.action == "status":
            rc = wait_for_reply(
                "airport_bind_status",
                args.timeout,
                lambda cb: device.query_bind_status_from_cloud(callback=cb),
                expected_result=args.expect_get_result,
                expect_timeout=args.expect_timeout,
                validate_fn=validate_bind_status,
            )
            if rc != 0:
                return rc

        if args.action in {"get", "full"}:
            rc = wait_for_reply(
                "airport_organization_get",
                args.timeout,
                lambda cb: device.query_organization_from_cloud(
                    args.binding_code,
                    args.organization_id,
                    callback=cb,
                ),
                expected_result=args.expect_get_result,
                expect_timeout=args.expect_timeout,
                validate_fn=validate_get,
            )
            if rc != 0:
                return rc

        if args.action in {"bind", "full"}:
            bind_devices = build_bind_devices(device, gateway_sn, args)
            rc = wait_for_reply(
                "airport_organization_bind",
                args.timeout,
                lambda cb: device.bind_to_organization(bind_devices, callback=cb),
                expected_result=args.expect_bind_result,
                expect_timeout=args.expect_timeout,
                validate_fn=validate_bind,
            )
            if rc != 0:
                return rc

        if args.expect_event:
            rc = wait_for_events(recorded_events, args.expect_event, args.verify_timeout)
            if rc != 0:
                return rc

        if args.expect_device_binding_sn or args.expect_device_binding_count is not None:
            rc = verify_device_binding_events(
                recorded_events,
                expected_sns=args.expect_device_binding_sn,
                expected_count=args.expect_device_binding_count,
            )
            if rc != 0:
                return rc

        if (
            args.expect_topo_sub_device_count is not None
            or args.expect_topo_sub_device_sn
            or args.validate_topo_schema
        ):
            rc = verify_topo_payload(
                recorded_events,
                expected_count=args.expect_topo_sub_device_count,
                expected_sns=args.expect_topo_sub_device_sn,
                validate_schema=args.validate_topo_schema,
            )
            if rc != 0:
                return rc

        if args.expect_status_reply:
            rc = wait_for_status_reply(
                recorded_status_replies,
                status_reply_event,
                args.verify_timeout,
                args.expect_status_result,
            )
            if rc != 0:
                return rc

        if args.expect_bound:
            error = verify_memory_binding(device, gateway_sn, args.organization_id)
            if error:
                print(f"memory binding verification failed: {error}", file=sys.stderr)
                return 6

        expected_bound_sns = _unique_ordered(args.expect_bound_sn)
        if args.expect_bound:
            expected_bound_sns = [sn for sn in expected_bound_sns if sn != gateway_sn]
        for sn in expected_bound_sns:
            error = verify_memory_binding_for_sn(device, sn, args.organization_id)
            if error:
                print(f"memory binding verification failed: {error}", file=sys.stderr)
                return 6

        if args.expect_sub_device_paired_sn:
            error = verify_sub_device_paired(device, args.expect_sub_device_paired_sn)
            if error:
                print(f"sub-device paired verification failed: {error}", file=sys.stderr)
                return 13

        if args.verify_db:
            rc = verify_db_binding(async_loop, gateway_sn, args.organization_id, args.verify_timeout)
            if rc != 0:
                return rc

        print("organization request test ok")
        return 0
    finally:
        cloud_conn._stop_request_cleaner()
        request_client.disconnect()
        device.stop()
        time.sleep(0.2)
        stop_background_loop(async_loop, async_thread)


if __name__ == "__main__":
    raise SystemExit(main())
