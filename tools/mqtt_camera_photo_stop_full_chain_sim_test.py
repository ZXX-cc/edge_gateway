#!/usr/bin/env python3
"""Full MQTT-chain retest for camera_photo_stop with a simulated photo session.

The script uses only the official Dock3 services method:

    thing/product/{sn}/services
    method: camera_photo_stop

It starts a simulated gateway-side MQTT subscriber, attaches a simulated
continuous-photo thread to the real CameraController, then publishes
camera_photo_stop from the cloud side and waits for services_reply.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from src.mqtt.topic_router import TopicRouter
from src.payload.camera import CameraController
from src.payload.dock3_payload import payload_index_from_config
from src.utils.config import Config
from tool_config import (
    add_config_argument,
    load_config,
    resolve_mqtt_credentials,
    resolve_mqtt_settings,
)


OFFICIAL_METHOD = "camera_photo_stop"
SERVICES_TOPIC = "thing/product/{sn}/services"
SERVICES_REPLY_TOPIC = "thing/product/{sn}/services_reply"


def build_mqtt_client(mqtt_module, client_id: str):
    if hasattr(mqtt_module, "CallbackAPIVersion"):
        try:
            return mqtt_module.Client(mqtt_module.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            pass
    return mqtt_module.Client(client_id=client_id)


def attach_simulated_photo_session(camera: CameraController, *, stuck: bool, hold_seconds: float) -> threading.Thread:
    camera._photo_session_stop.clear()

    def run_stoppable_session() -> None:
        while not camera._photo_session_stop.is_set():
            time.sleep(0.05)

    def run_stuck_session() -> None:
        deadline = time.time() + hold_seconds
        while time.time() < deadline:
            time.sleep(0.05)

    thread = threading.Thread(
        target=run_stuck_session if stuck else run_stoppable_session,
        name="mqtt-simulated-photo-session-stuck" if stuck else "mqtt-simulated-photo-session",
        daemon=True,
    )
    camera._photo_session_thread = thread
    thread.start()
    return thread


class SimulatedGateway:
    def __init__(self, args: argparse.Namespace) -> None:
        import paho.mqtt.client as mqtt

        self.args = args
        self.services_topic = SERVICES_TOPIC.format(sn=args.sn)
        self.reply_topic = SERVICES_REPLY_TOPIC.format(sn=args.sn)
        self.connected_event = threading.Event()
        self.subscribed_event = threading.Event()
        self.received_event = threading.Event()
        self.reply_event = threading.Event()
        self.received_messages: list[dict[str, Any]] = []
        self.published_replies: list[dict[str, Any]] = []

        Config().load(args.config)
        self.camera = CameraController()
        self.camera.gateway_sn = args.sn
        self.photo_thread = attach_simulated_photo_session(
            self.camera,
            stuck=args.stuck,
            hold_seconds=args.stuck_hold_seconds,
        )

        self.router = TopicRouter()
        self.router.register(OFFICIAL_METHOD, self.camera.handle_camera_photo_stop, category="payload")

        self.client = build_mqtt_client(mqtt, f"edge_gateway_photo_stop_sim_{int(time.time() * 1000)}")
        if args.username and hasattr(self.client, "username_pw_set"):
            self.client.username_pw_set(args.username, args.password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_subscribe = self._on_subscribe

    def start(self) -> bool:
        self.client.connect(self.args.host, self.args.port, keepalive=30)
        self.client.loop_start()
        if not self.connected_event.wait(self.args.timeout):
            return False
        return self.subscribed_event.wait(self.args.timeout)

    def stop(self) -> None:
        self.camera._photo_session_stop.set()
        if self.photo_thread.is_alive() and not self.args.stuck:
            self.photo_thread.join(timeout=1.0)
        try:
            self.client.disconnect()
        finally:
            try:
                self.client.loop_stop(force=True)
            except TypeError:
                self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(self.services_topic, qos=self.args.qos)
            print(f"simulated gateway subscribed -> {self.services_topic}")
        else:
            print(f"simulated gateway connect failed: rc={rc}", file=sys.stderr)
        self.connected_event.set()

    def _on_subscribe(self, client, userdata, mid, granted_qos, *args):
        self.subscribed_event.set()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            print(f"simulated gateway invalid JSON on {msg.topic}: {exc}", file=sys.stderr)
            return

        method = payload.get("method") if isinstance(payload, dict) else None
        if msg.topic != self.services_topic or method != OFFICIAL_METHOD:
            return

        self.received_messages.append(payload)
        self.received_event.set()
        print(f"simulated gateway received <- {msg.topic}, method={method}, tid={payload.get('tid')}")

        reply = self.router.route(method, payload)
        if not isinstance(reply, dict):
            print("simulated gateway handler returned no reply", file=sys.stderr)
            return
        client.publish(self.reply_topic, json.dumps(reply, ensure_ascii=False), qos=self.args.qos)
        self.published_replies.append(reply)
        self.reply_event.set()
        print(f"simulated gateway published -> {self.reply_topic}, result={result_code(reply)}")


def payload_index(args: argparse.Namespace) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def validate_reply(reply: dict[str, Any] | None, *, stuck: bool) -> int:
    data = reply.get("data") if isinstance(reply, dict) else {}
    data = data if isinstance(data, dict) else {}
    code = result_code(reply)
    running = data.get("running")
    stopped = data.get("stopped")

    if stuck:
        if code == 0:
            print("failed: camera_photo_stop returned success while simulated photo thread is still alive", file=sys.stderr)
            print(json.dumps(reply, ensure_ascii=False, indent=2), file=sys.stderr)
            return 6
        if running is not True or stopped is not False:
            print("failed: stuck stop reply did not expose running=true/stopped=false", file=sys.stderr)
            print(json.dumps(reply, ensure_ascii=False, indent=2), file=sys.stderr)
            return 7
        return 0

    if code != 0 or running is not False or stopped is not True:
        print("failed: stoppable photo thread was not stopped successfully", file=sys.stderr)
        print(json.dumps(reply, ensure_ascii=False, indent=2), file=sys.stderr)
        return 8
    return 0


def run_check(args: argparse.Namespace) -> int:
    print(f"using simulated gateway SN: {args.sn}")
    gateway = SimulatedGateway(args)
    if not gateway.start():
        print("simulated gateway MQTT connect/subscribe timeout", file=sys.stderr)
        return 2

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_photo_stop_cloud",
        username=args.username,
        password=args.password,
    )
    if not client.connect(args.timeout):
        gateway.stop()
        print("cloud-side MQTT connect timeout", file=sys.stderr)
        return 3

    try:
        allowed = None if args.stuck else {0}
        rc, reply = client.request(
            OFFICIAL_METHOD,
            {"payload_index": payload_index(args)},
            args.timeout,
            channel="services",
            allowed_results=allowed,
        )
        if rc != 0:
            return rc

        if not gateway.received_event.wait(1.0):
            print("failed: simulated gateway did not receive camera_photo_stop", file=sys.stderr)
            return 9

        rc = validate_reply(reply, stuck=args.stuck)
        if rc != 0:
            return rc

        thread_alive = gateway.photo_thread.is_alive()
        print(f"gateway_received_camera_photo_stop_count={len(gateway.received_messages)}")
        print(f"simulated_photo_thread_alive_after_stop={thread_alive}")
        print("camera_photo_stop full MQTT-chain simulated session test ok")
        return 0
    finally:
        client.disconnect()
        gateway.stop()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retest camera_photo_stop through official MQTT services with a simulated gateway photo session."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--payload-index", default="", help="Dock3 payload index, e.g. 52-0-0.")
    parser.add_argument("--stuck", action="store_true", help="Simulate the 2-second stop timeout regression.")
    parser.add_argument("--stuck-hold-seconds", type=float, default=60.0)
    args = parser.parse_args()

    requested_sn = args.sn
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    if not requested_sn:
        args.sn = f"{args.sn}SIMPHOTO{int(time.time() * 1000)}"
    args.username, args.password = resolve_mqtt_credentials(args)
    return run_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
