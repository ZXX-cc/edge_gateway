#!/usr/bin/env python3
"""DJI Dock3 gimbal full communication-chain test.

This script is intended for real gateway validation.  It checks the Cloud API
surface around the gimbal, not just the local ViewLink adapter:

- MQTT services request/reply path
- payload_status_query and gimbal_status_query
- official Dock3 payload index/type_subtype_gimbalindex fields
- property_get for gimbal payload property and live_capacity
- live_capability_query camera/video source structure
- payload_status_notify event after gimbal movement
- optional DRC payload/gimbal control path
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from tool_config import add_config_argument, resolve_mqtt_settings


DEFAULT_PAYLOAD_INDEX = "52-0-0"


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


class GimbalFullChainClient(MqttRequestClient):
    def services(self, method: str, data: Optional[dict], timeout: float, allowed_results: set[int] | None = None):
        return self.request(
            method,
            data or {},
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else allowed_results,
        )

    def drc(self, method: str, data: Optional[dict], timeout: float, allowed_results: set[int] | None = None):
        return self.request(
            method,
            data or {},
            timeout,
            channel="drc",
            allowed_results={0} if allowed_results is None else allowed_results,
        )

    def services_any_result(self, method: str, data: Optional[dict], timeout: float):
        return self.request(
            method,
            data or {},
            timeout,
            channel="services",
            allowed_results=None,
        )


def _print_step(step: Step) -> None:
    status = "PASS" if step.ok else "FAIL"
    suffix = f" - {step.detail}" if step.detail else ""
    print(f"[{status}] {step.name}{suffix}")


def _fail(name: str, detail: str) -> Step:
    return Step(name, False, detail)


def _ok(name: str, detail: str = "") -> Step:
    return Step(name, True, detail)


def _data(reply: Any) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {}
    data = reply.get("data")
    return data if isinstance(data, dict) else {}


def _gimbal_payload_from_status(reply: Any) -> dict[str, Any]:
    data = _data(reply)
    payload = data.get("payload")
    if isinstance(payload, dict):
        return payload
    payloads = data.get("payloads")
    if isinstance(payloads, list):
        for item in payloads:
            if isinstance(item, dict) and item.get("id", item.get("payload_type")) == "gimbal":
                return item
    return {}


def _official_payload(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("type_subtype_gimbalindex")
    return value if isinstance(value, dict) else {}


def _viewlink_result(reply: Any) -> dict[str, Any]:
    result = _data(reply).get("viewlink_result")
    return result if isinstance(result, dict) else {}


def _runtime_gimbal_control(payload: dict[str, Any]) -> dict[str, Any]:
    channels = payload.get("channels")
    control = channels.get("control") if isinstance(channels, dict) else None
    return control if isinstance(control, dict) else {}


def _assert_payload_index(name: str, payload: dict[str, Any], expected: str) -> Step:
    actual = payload.get("payload_index")
    official = _official_payload(payload)
    official_index = official.get("payload_index")
    if actual != expected:
        return _fail(name, f"payload_index expected={expected}, actual={actual}")
    if official_index != expected:
        return _fail(name, f"type_subtype_gimbalindex.payload_index expected={expected}, actual={official_index}")
    for field in ("gimbal_pitch", "gimbal_roll", "gimbal_yaw", "measure_target_error_state"):
        if field not in official:
            return _fail(name, f"missing official field: {field}")
    return _ok(name, expected)


def _assert_pose(name: str, payload: dict[str, Any], *, pitch: float, roll: float, yaw: float) -> Step:
    gimbal = payload.get("gimbal")
    if not isinstance(gimbal, dict):
        return _fail(name, "missing payload.gimbal")
    expected = {"pitch": pitch, "roll": roll, "yaw": yaw}
    for key, value in expected.items():
        actual = gimbal.get(key)
        if actual != value:
            return _fail(name, f"{key} expected={value}, actual={actual}")
    return _ok(name, f"pitch={pitch}, roll={roll}, yaw={yaw}")


def _assert_live_capacity(name: str, value: Any, expected_payload_index: str) -> Step:
    if not isinstance(value, dict):
        return _fail(name, "live_capacity is not object")
    devices = value.get("device_list")
    if not isinstance(devices, list) or not devices:
        return _fail(name, "live_capacity.device_list missing")
    cameras = devices[0].get("camera_list") if isinstance(devices[0], dict) else None
    if not isinstance(cameras, list) or not cameras:
        return _fail(name, "live_capacity.device_list[0].camera_list missing")
    camera = cameras[0]
    if camera.get("camera_index") != expected_payload_index:
        return _fail(name, f"camera_index expected={expected_payload_index}, actual={camera.get('camera_index')}")
    videos = camera.get("video_list")
    if not isinstance(videos, list) or not videos:
        return _fail(name, "camera.video_list missing")
    video = videos[0]
    for field in ("video_index", "video_type", "switchable_video_types"):
        if field not in video:
            return _fail(name, f"video_list[0] missing {field}")
    return _ok(name, f"camera_index={expected_payload_index}, video_index={video.get('video_index')}")


def _official_drc_enter_payload(args) -> dict[str, Any]:
    return {
        "mqtt_broker": {
            "address": args.drc_broker_address,
            "client_id": f"drc-{args.sn}",
            "username": args.drc_username,
            "password": args.drc_password,
            "expire_time": args.drc_expire_time,
            "enable_tls": args.drc_enable_tls,
        },
        "osd_frequency": args.osd_frequency,
        "hsi_frequency": args.hsi_frequency,
    }


def _request_viewlink(client: GimbalFullChainClient, method: str, data: dict[str, Any], timeout: float) -> tuple[Step, Any]:
    rc, reply = client.services_any_result(method, data, timeout)
    if rc != 0:
        return _fail(method, f"MQTT rc={rc}"), reply

    code = result_code(reply)
    viewlink = _viewlink_result(reply)
    if code != 0:
        detail = viewlink.get("message") or _data(reply).get("msg") or json.dumps(_data(reply), ensure_ascii=False)
        return _fail(method, f"gateway result={code}, {detail}"), reply

    if not viewlink.get("success", False):
        return _fail(method, json.dumps(viewlink, ensure_ascii=False)), reply

    return _ok(method, viewlink.get("message", "")), reply


def ensure_viewlink_connected(
    client: GimbalFullChainClient,
    args,
    payload_status: dict[str, Any],
) -> list[Step]:
    if args.skip_viewlink_connect:
        return [_ok("ViewLink initialize/connect", "skipped")]

    control = _runtime_gimbal_control(payload_status)
    sdk_dir = args.sdk_dir or str(control.get("sdk_dir") or "")
    gimbal_ip = args.gimbal_ip or str(control.get("host") or "")
    gimbal_port = args.gimbal_port or int(control.get("port") or 0)

    if not sdk_dir or not gimbal_ip or not gimbal_port:
        return [_fail(
            "ViewLink runtime config",
            f"sdk_dir={sdk_dir!r}, host={gimbal_ip!r}, port={gimbal_port!r}; "
            "请检查 config/gateway.yaml 的 payload.devices.gimbal.channels.control，"
            "或运行脚本时传 --sdk-dir/--gimbal-ip/--gimbal-port",
        )]

    steps: list[Step] = []
    for method, data in (
        ("viewlink_initialize", {"sdk_dir": sdk_dir}),
        ("viewlink_connect", {"ip": gimbal_ip, "port": gimbal_port, "conn_type": "tcp"}),
        ("viewlink_is_connected", {}),
    ):
        step, reply = _request_viewlink(client, method, data, args.timeout)
        steps.append(step)
        if not step.ok:
            return steps
        if method == "viewlink_is_connected" and _viewlink_result(reply).get("connected") is not True:
            steps.append(_fail("viewlink_is_connected.connected", "false"))
            return steps
    return steps


def run_services_chain(client: GimbalFullChainClient, args) -> list[Step]:
    steps: list[Step] = []

    rc, reply = client.services("payload_status_query", {}, args.timeout)
    if rc != 0:
        return [_fail("payload_status_query", f"rc={rc}")]
    payload = _gimbal_payload_from_status(reply)
    steps.append(_assert_payload_index("payload_status_query official payload", payload, args.payload_index))

    viewlink_steps = ensure_viewlink_connected(client, args, payload)
    steps.extend(viewlink_steps)
    if any(not step.ok for step in viewlink_steps):
        return steps

    rc, reply = client.services("gimbal_capability_query", {}, args.timeout)
    if rc != 0:
        return steps + [_fail("gimbal_capability_query", f"rc={rc}")]
    capability = _data(reply)
    if "pitch_range" not in capability or "yaw_range" not in capability:
        steps.append(_fail("gimbal capability fields", json.dumps(capability, ensure_ascii=False)))
    else:
        steps.append(_ok("gimbal capability fields", f"pitch_range={capability.get('pitch_range')}"))

    rc, reply = client.services("live_capability_query", {}, args.timeout)
    if rc != 0:
        return steps + [_fail("live_capability_query", f"rc={rc}")]
    steps.append(_assert_live_capacity("live_capability_query live_capacity", _data(reply).get("live_capacity"), args.payload_index))

    rc, reply = client.services(
        "property_get",
        {"properties": ["type_subtype_gimbalindex", "live_capacity"]},
        args.timeout,
    )
    if rc != 0:
        return steps + [_fail("property_get payload properties", f"rc={rc}")]
    props = _data(reply)
    property_payload = props.get("type_subtype_gimbalindex")
    if not isinstance(property_payload, dict):
        steps.append(_fail("property_get type_subtype_gimbalindex", "missing object"))
    elif property_payload.get("payload_index") != args.payload_index:
        steps.append(_fail(
            "property_get type_subtype_gimbalindex",
            f"payload_index expected={args.payload_index}, actual={property_payload.get('payload_index')}",
        ))
    else:
        steps.append(_ok("property_get type_subtype_gimbalindex", args.payload_index))
    steps.append(_assert_live_capacity("property_get live_capacity", props.get("live_capacity"), args.payload_index))

    previous_events = client.event_count("payload_status_notify")
    rc, rotate_reply = client.services(
        "gimbal_rotate",
        {"pitch": args.pitch, "roll": args.roll, "yaw": args.yaw},
        args.timeout,
    )
    if rc != 0:
        detail = (
            f"rc={rc}, result={result_code(rotate_reply)}, "
            f"msg={rotate_reply.get('msg') if isinstance(rotate_reply, dict) else ''}; "
            "通常表示 ViewLink SDK 未连接、云台 TCP 不通，或未启用飞控/MAVLink 回退"
        )
        return steps + [_fail("gimbal_rotate", detail)]
    steps.append(_ok("gimbal_rotate reply", f"pitch={args.pitch}, roll={args.roll}, yaw={args.yaw}"))

    rc, status = client.services("gimbal_status_query", {}, args.timeout)
    if rc != 0:
        return steps + [_fail("gimbal_status_query after rotate", f"rc={rc}")]
    status_payload = _gimbal_payload_from_status(status)
    steps.append(_assert_pose("gimbal_status_query pose", status_payload, pitch=args.pitch, roll=args.roll, yaw=args.yaw))
    steps.append(_assert_payload_index("gimbal_status_query official payload", status_payload, args.payload_index))

    rc, event = client.wait_event(
        "payload_status_notify",
        previous_events,
        args.event_timeout,
        expected_status=None,
    )
    if rc != 0:
        steps.append(_fail("payload_status_notify after rotate", f"rc={rc}"))
    else:
        event_payload = _gimbal_payload_from_status(event)
        steps.append(_assert_pose("payload_status_notify pose", event_payload, pitch=args.pitch, roll=args.roll, yaw=args.yaw))
        steps.append(_assert_payload_index("payload_status_notify official payload", event_payload, args.payload_index))

    rc, mode_reply = client.services_any_result("set_gimbal_mode", {"mode": args.mode}, args.timeout)
    mode_code = result_code(mode_reply)
    if rc == 0 and mode_code == 0:
        steps.append(_ok("set_gimbal_mode", args.mode))
    elif not args.require_gimbal_mode:
        steps.append(_ok(
            "set_gimbal_mode",
            f"optional unsupported/result={mode_code}; 当前实现此能力依赖 MAVLink 云台模式配置，ViewLink 链路不阻断",
        ))
    else:
        steps.append(_fail("set_gimbal_mode", f"rc={rc}, result={mode_code}"))

    if not args.skip_reset:
        rc, status = client.services("gimbal_reset", {}, args.timeout)
        if rc != 0:
            steps.append(_fail("gimbal_reset", f"rc={rc}"))
        else:
            steps.append(_ok("gimbal_reset reply"))
            rc, status = client.services("gimbal_status_query", {}, args.timeout)
            if rc == 0:
                steps.append(_assert_pose("gimbal_status_query after reset", _gimbal_payload_from_status(status), pitch=0, roll=0, yaw=0))
            else:
                steps.append(_fail("gimbal_status_query after reset", f"rc={rc}"))

    return steps


def run_drc_chain(client: GimbalFullChainClient, args) -> list[Step]:
    steps: list[Step] = []
    drc_entered = False
    try:
        rc, height_reply = client.services_any_result("property_get", {"properties": ["height"]}, args.timeout)
        height = _data(height_reply).get("height") if rc == 0 else None
        try:
            height_value = float(height)
        except (TypeError, ValueError):
            height_value = None
        if height_value is not None and height_value < args.min_drc_height:
            detail = f"skipped: 当前高度 {height_value:.1f}m 低于 DRC 准入阈值 {args.min_drc_height:.1f}m；请先起飞/升高后再测 DRC"
            if args.fail_low_drc_altitude:
                return [_fail("drc preflight altitude", detail)]
            return [_ok("drc preflight altitude", detail)]

        client.services("drc_mode_exit", {}, args.timeout, allowed_results={0, 319999})

        rc, enter_reply = client.services_any_result("drc_mode_enter", _official_drc_enter_payload(args), args.timeout)
        enter_code = result_code(enter_reply)
        enter_msg = enter_reply.get("msg") if isinstance(enter_reply, dict) else ""
        if rc != 0 or enter_code != 0:
            detail = f"rc={rc}, result={enter_code}, msg={enter_msg}"
            if "高度" in str(enter_msg) and not args.fail_low_drc_altitude:
                return [_ok("drc_mode_enter", f"skipped: {detail}")]
            return [_fail("drc_mode_enter", detail)]
        drc_entered = True
        steps.append(_ok("drc_mode_enter"))

        rc, reply = client.services("property_get", {"properties": ["drc_state"]}, args.timeout)
        if rc != 0 or _data(reply).get("drc_state") != 2:
            return steps + [_fail("property_get drc_state", f"rc={rc}, drc_state={_data(reply).get('drc_state')}")]
        steps.append(_ok("property_get drc_state", "2"))

        rc, _ = client.drc("heart_beat", {"seq": 1, "timestamp": int(time.time() * 1000)}, args.timeout)
        if rc != 0:
            return steps + [_fail("drc heart_beat", f"rc={rc}")]
        steps.append(_ok("drc heart_beat"))

        rc, _ = client.drc(
            "payload_control",
            {
                "payload_index": args.payload_index,
                "action": "gimbal_rotate",
                "params": {"pitch": args.drc_pitch, "yaw": args.drc_yaw, "roll": 0},
            },
            args.timeout,
        )
        steps.append(_ok("drc payload_control gimbal_rotate") if rc == 0 else _fail("drc payload_control gimbal_rotate", f"rc={rc}"))

        rc, _ = client.drc(
            "drc_gimbal_speed_push",
            {"pitch_speed": 0.0, "yaw_speed": 0.0, "roll_speed": 0.0},
            args.timeout,
        )
        steps.append(_ok("drc_gimbal_speed_push") if rc == 0 else _fail("drc_gimbal_speed_push", f"rc={rc}"))
    finally:
        if drc_entered:
            rc, _ = client.services("drc_mode_exit", {}, args.timeout, allowed_results={0, 319999})
            steps.append(_ok("drc_mode_exit") if rc == 0 else _fail("drc_mode_exit", f"rc={rc}"))

    return steps


def wait_gateway_osd(client: GimbalFullChainClient, args) -> Step:
    topic = f"thing/product/{args.sn}/osd"
    previous_count = client.observed_count(topic)
    payload = client.wait_observed(topic, previous_count, args.osd_timeout)
    if payload is None:
        return _fail("gateway OSD payload property", f"timeout topic={topic}")
    data = _data(payload)
    official = data.get("type_subtype_gimbalindex")
    if not isinstance(official, dict):
        return _fail("gateway OSD payload property", "missing type_subtype_gimbalindex")
    if official.get("payload_index") != args.payload_index:
        return _fail("gateway OSD payload property", f"payload_index={official.get('payload_index')}")
    return _ok("gateway OSD payload property", args.payload_index)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test Dock3 gimbal full MQTT communication chain")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--event-timeout", type=float, default=8.0)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")

    parser.add_argument("--payload-index", default=DEFAULT_PAYLOAD_INDEX)
    parser.add_argument("--sdk-dir", default="", help="override ViewLink SDK directory")
    parser.add_argument("--gimbal-ip", default="", help="override gimbal control IP")
    parser.add_argument("--gimbal-port", type=int, default=0, help="override gimbal control TCP port")
    parser.add_argument("--skip-viewlink-connect", action="store_true", help="do not initialize/connect ViewLink before gimbal_rotate")
    parser.add_argument("--pitch", type=float, default=-20.0)
    parser.add_argument("--roll", type=float, default=0.0)
    parser.add_argument("--yaw", type=float, default=30.0)
    parser.add_argument("--mode", choices=["FOLLOW", "FREE", "FPV", "RESET"], default="FREE")
    parser.add_argument("--require-gimbal-mode", action="store_true", help="fail if set_gimbal_mode is unsupported")
    parser.add_argument("--skip-reset", action="store_true")

    parser.add_argument("--wait-osd", action="store_true", help="wait gateway OSD and verify type_subtype_gimbalindex")
    parser.add_argument("--osd-timeout", type=float, default=8.0)

    parser.add_argument("--include-drc", action="store_true", help="also test DRC payload/gimbal control path")
    parser.add_argument("--drc-broker-address", default="127.0.0.1:1883")
    parser.add_argument("--drc-username", default="")
    parser.add_argument("--drc-password", default="")
    parser.add_argument("--drc-expire-time", type=int, default=3600)
    parser.add_argument("--drc-enable-tls", action="store_true")
    parser.add_argument("--osd-frequency", type=int, default=10)
    parser.add_argument("--hsi-frequency", type=int, default=1)
    parser.add_argument("--drc-pitch", type=float, default=-10.0)
    parser.add_argument("--drc-yaw", type=float, default=10.0)
    parser.add_argument("--min-drc-height", type=float, default=2.0)
    parser.add_argument("--fail-low-drc-altitude", action="store_true", help="fail instead of skipping DRC when height is too low")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    observed_topics = [f"thing/product/{args.sn}/osd"] if args.wait_osd else []
    client = GimbalFullChainClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_gimbal_full_chain",
        subscribe_events=True,
        observe_topics=observed_topics,
    )
    if not client.connect(args.timeout):
        print(f"MQTT connect timeout: {args.host}:{args.port}", file=sys.stderr)
        return 2

    steps: list[Step] = []
    try:
        steps.append(_ok("MQTT connect", f"{args.host}:{args.port}, sn={args.sn}"))
        steps.extend(run_services_chain(client, args))
        if args.wait_osd:
            steps.append(wait_gateway_osd(client, args))
        if args.include_drc:
            steps.extend(run_drc_chain(client, args))

        ignored_total = client.stale_total()
        if args.fail_on_stale and ignored_total:
            steps.append(_fail("strict stale replies", json.dumps(client.stats, ensure_ascii=False)))
        else:
            steps.append(_ok("stale reply summary", f"strict={ignored_total}, unrelated={client.unrelated_reply_total()}"))
    finally:
        client.disconnect()

    print("\nsummary")
    for step in steps:
        _print_step(step)

    failed = [step for step in steps if not step.ok]
    if failed:
        print("\nfailed steps:", file=sys.stderr)
        for step in failed:
            print(f"- {step.name}: {step.detail}", file=sys.stderr)
        return 1

    print("\ndock3 gimbal full chain test ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
