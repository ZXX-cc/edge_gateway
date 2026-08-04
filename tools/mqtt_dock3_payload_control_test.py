#!/usr/bin/env python3
"""DJI Dock3 负载 services 控制 MQTT 验收脚本。

本脚本覆盖上云 API services 里的相机/云台控制方法。它固定通过
thing/product/{sn}/services 发布，不进入 DRC 模式，也不测试
drc/down 里的 payload_control 方法。
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code
from src.payload.dock3_payload import payload_index_from_config
from src.utils.dji_error_codes import DOCK_COMMUNICATION_ERROR, DOCK_SYSTEM_ERROR
from tool_config import add_config_argument, load_config, resolve_mqtt_settings


ADAPTER_UNAVAILABLE_RESULTS = {DOCK_COMMUNICATION_ERROR, DOCK_SYSTEM_ERROR}


@dataclass(frozen=True)
class PayloadControlCase:
    label: str
    method: str
    data: dict[str, Any]
    allowed_non_strict: set[int] | None = None
    expected_status: str | None = None


class Dock3PayloadControlClient(MqttRequestClient):
    def request_case(self, case: PayloadControlCase, timeout: float, *, strict: bool):
        allowed = {0} if strict else {0, *(case.allowed_non_strict or ADAPTER_UNAVAILABLE_RESULTS)}
        return super().request(
            case.method,
            case.data,
            timeout,
            channel="services",
            allowed_results=allowed,
            expected_status=case.expected_status,
        )


def _payload_index(args) -> str:
    if args.payload_index:
        return args.payload_index
    config = load_config(args.config)
    return payload_index_from_config(config)


def _nested(data: dict[str, Any], *keys: str, default=None):
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def _gimbal_control_config(args) -> dict[str, Any]:
    config = load_config(args.config)
    control = _nested(
        config,
        "payload",
        "devices",
        "gimbal",
        "channels",
        "control",
        default={},
    )
    control = control if isinstance(control, dict) else {}
    return {
        "sdk_dir": args.sdk_dir or str(control.get("sdk_dir") or ""),
        "ip": args.gimbal_ip or str(control.get("host") or ""),
        "port": args.gimbal_port or int(control.get("port") or 0),
    }


def build_cases(args) -> list[PayloadControlCase]:
    payload_index = _payload_index(args)
    camera_type = args.camera_type
    video_id = args.video_id

    gimbal_only_cases = [
        PayloadControlCase(
            "云台控制-状态查询",
            "gimbal_status_query",
            {"payload_index": payload_index},
        ),
        PayloadControlCase(
            "云台控制-小角度转动",
            "gimbal_rotate",
            {"payload_index": payload_index, "pitch": args.gimbal_pitch, "yaw": args.gimbal_yaw},
        ),
        PayloadControlCase(
            "云台控制-变焦",
            "camera_focal_length_set",
            {
                "payload_index": payload_index,
                "camera_index": 0,
                "camera_type": camera_type,
                "zoom_factor": args.zoom_ratio,
                "focal_length": args.focal_length,
            },
        ),
        PayloadControlCase(
            "云台控制-拍照",
            "camera_photo_take",
            {
                "payload_index": payload_index,
                "camera_index": 0,
                "camera_type": camera_type,
            },
        ),
        PayloadControlCase(
            "云台控制-开始录像",
            "camera_recording_start",
            {
                "payload_index": payload_index,
                "camera_index": 0,
                "camera_type": camera_type,
                "resolution": args.resolution,
                "framerate": args.framerate,
                "video_id": video_id,
            },
        ),
        PayloadControlCase(
            "云台控制-停止录像",
            "camera_recording_stop",
            {
                "payload_index": payload_index,
                "camera_index": 0,
                "camera_type": camera_type,
                "video_id": video_id,
            },
        ),
        PayloadControlCase(
            "云台控制-回中",
            "gimbal_reset",
            {"payload_index": payload_index},
        ),
    ]
    if args.gimbal_only:
        return gimbal_only_cases

    return [
        PayloadControlCase(
            "负载控制-框选变焦",
            "camera_frame_zoom",
            {
                "payload_index": payload_index,
                "camera_type": camera_type,
                "locked": False,
                "x": 0.5,
                "y": 0.5,
                "width": 0.25,
                "height": 0.25,
            },
        ),
        PayloadControlCase(
            "负载控制-切换相机模式",
            "camera_mode_switch",
            {
                "payload_index": payload_index,
                "camera_type": camera_type,
                "camera_mode": args.camera_mode,
                "mode": "photo" if args.camera_mode == 0 else "video",
            },
        ),
        PayloadControlCase(
            "负载控制-开始拍照",
            "camera_photo_take",
            {
                "payload_index": payload_index,
                "camera_index": 0,
                "camera_type": camera_type,
            },
        ),
        PayloadControlCase(
            "负载控制-停止拍照",
            "camera_photo_stop",
            {
                "payload_index": payload_index,
                "camera_index": 0,
                "camera_type": camera_type,
            },
        ),
        PayloadControlCase(
            "负载控制-开始录像",
            "camera_recording_start",
            {
                "payload_index": payload_index,
                "camera_index": 0,
                "camera_type": camera_type,
                "resolution": args.resolution,
                "framerate": args.framerate,
                "video_id": video_id,
            },
        ),
        PayloadControlCase(
            "负载控制-停止录像",
            "camera_recording_stop",
            {
                "payload_index": payload_index,
                "camera_index": 0,
                "camera_type": camera_type,
                "video_id": video_id,
            },
        ),
        PayloadControlCase(
            "负载控制-画面拖动控制",
            "camera_screen_drag",
            {
                "payload_index": payload_index,
                "locked": False,
                "pitch_speed": args.drag_pitch_speed,
                "yaw_speed": args.drag_yaw_speed,
            },
        ),
        PayloadControlCase(
            "负载控制-双击成为 AIM",
            "camera_aim",
            {
                "payload_index": payload_index,
                "camera_type": camera_type,
                "locked": True,
                "x": 0.5,
                "y": 0.5,
            },
        ),
        PayloadControlCase(
            "负载控制-变焦",
            "camera_focal_length_set",
            {
                "payload_index": payload_index,
                "camera_index": 0,
                "camera_type": camera_type,
                "zoom_factor": args.zoom_ratio,
                "focal_length": args.focal_length,
            },
        ),
        PayloadControlCase(
            "负载控制-重置云台",
            "gimbal_reset",
            {"payload_index": payload_index},
        ),
        PayloadControlCase(
            "负载控制-Look At",
            "camera_look_at",
            {
                "payload_index": payload_index,
                "latitude": args.look_at_lat,
                "longitude": args.look_at_lon,
                "height": args.look_at_height,
            },
        ),
        PayloadControlCase(
            "负载控制-分屏",
            "camera_screen_split",
            {"payload_index": payload_index, "enable": args.split_enable},
        ),
        PayloadControlCase(
            "负载控制-照片存储设置",
            "photo_storage_set",
            {
                "payload_index": payload_index,
                "quality": args.photo_quality,
                "include_metadata": True,
                "photo_storage_settings": [{"format": "jpg", "enable": True}],
            },
        ),
        PayloadControlCase(
            "负载控制-视频存储设置",
            "video_storage_set",
            {
                "payload_index": payload_index,
                "video_storage_settings": [
                    {"video_id": video_id, "format": "mp4", "enable": True}
                ],
            },
        ),
        PayloadControlCase(
            "负载控制-相机曝光模式设置",
            "camera_exposure_mode_set",
            {
                "payload_index": payload_index,
                "camera_type": camera_type,
                "mode": args.exposure_mode,
            },
        ),
        PayloadControlCase(
            "负载控制-相机曝光值调节",
            "camera_exposure_set",
            {
                "payload_index": payload_index,
                "camera_type": camera_type,
                "exposure_value": args.exposure_value,
            },
        ),
        PayloadControlCase(
            "负载控制-相机对焦模式设置",
            "camera_focus_mode_set",
            {
                "payload_index": payload_index,
                "camera_type": camera_type,
                "mode": args.focus_mode,
            },
        ),
        PayloadControlCase(
            "负载控制-相机对焦值设置",
            "camera_focus_value_set",
            {
                "payload_index": payload_index,
                "camera_type": camera_type,
                "focus_distance": args.focus_distance,
                "focus_duration": args.focus_duration,
            },
        ),
        PayloadControlCase(
            "负载控制-点对焦",
            "camera_point_focus_action",
            {
                "payload_index": payload_index,
                "camera_type": camera_type,
                "x": args.focus_x,
                "y": args.focus_y,
            },
        ),
        PayloadControlCase(
            "负载控制-红外测温模式设置",
            "ir_metering_mode_set",
            {"payload_index": payload_index, "mode": args.ir_metering_mode},
        ),
        PayloadControlCase(
            "负载控制-红外测温点设置",
            "ir_metering_point_set",
            {"payload_index": payload_index, "x": args.ir_x, "y": args.ir_y},
        ),
        PayloadControlCase(
            "负载控制-红外测温区域设置",
            "ir_metering_area_set",
            {
                "payload_index": payload_index,
                "x": args.ir_x,
                "y": args.ir_y,
                "width": args.ir_width,
                "height": args.ir_height,
            },
        ),
    ]


def classify_reply(reply: dict | None) -> str:
    code = result_code(reply)
    if code == 0:
        return "ok"
    if code in ADAPTER_UNAVAILABLE_RESULTS:
        return "adapter_unavailable"
    return f"result={code}"


def print_summary(results: list[tuple[PayloadControlCase, int, dict | None]]) -> None:
    counts = {"ok": 0, "adapter_unavailable": 0, "failed": 0}
    for case, rc, reply in results:
        status = classify_reply(reply) if rc == 0 else f"client_rc={rc}"
        if status in counts:
            counts[status] += 1
        else:
            counts["failed"] += 1
        print(f"[{status}] {case.label} / {case.method}")

    print(
        "dock3 payload-control summary "
        f"(ok={counts['ok']}, adapter_unavailable={counts['adapter_unavailable']}, "
        f"failed={counts['failed']}, total={len(results)})"
    )


def run_payload_control_checks(client: Dock3PayloadControlClient, args) -> int:
    results: list[tuple[PayloadControlCase, int, dict | None]] = []

    if not args.skip_bind_gateway:
        rc = ensure_gateway_bound(client, args)
        if rc != 0:
            return rc

    if args.connect_gimbal or args.bind_gimbal:
        rc = connect_gimbal(client, args)
        if rc != 0:
            return rc

    if args.grab_authority:
        authority = PayloadControlCase(
            "负载控制权抢占",
            "payload_authority_grab",
            {"payload_index": _payload_index(args)},
            expected_status=None,
        )
        rc, reply = client.request_case(authority, args.timeout, strict=args.strict)
        results.append((authority, rc, reply))
        if rc != 0 and args.stop_on_failure:
            print_summary(results)
            return rc

    for case in build_cases(args):
        rc, reply = client.request_case(case, args.timeout, strict=args.strict)
        results.append((case, rc, reply))
        if rc != 0 and args.stop_on_failure:
            print_summary(results)
            return rc

    print_summary(results)

    failures = [
        (case, rc, reply)
        for case, rc, reply in results
        if rc != 0 or classify_reply(reply) not in {"ok", "adapter_unavailable"}
    ]
    if args.strict:
        failures = [
            (case, rc, reply)
            for case, rc, reply in results
            if rc != 0 or classify_reply(reply) != "ok"
        ]
    if failures:
        print("payload-control failures:", file=sys.stderr)
        for case, rc, reply in failures:
            print(
                f"- {case.label} / {case.method}: rc={rc}, reply={json.dumps(reply, ensure_ascii=False)}",
                file=sys.stderr,
            )
        return 6
    return 0


def ensure_gateway_bound(client: Dock3PayloadControlClient, args) -> int:
    rc, workspace = client.request(
        "workspace_get",
        {},
        args.timeout,
        channel="services",
        allowed_results={0},
        expected_status=None,
    )
    if rc != 0:
        return rc
    data = workspace.get("data", {}) if isinstance(workspace, dict) else {}
    if data.get("is_bound") in {True, 1, "1", "true", "True"}:
        return 0

    rc, _ = client.request(
        "device_binding",
        {
            "workspace_id": args.workspace_id,
            "workspace_name": args.workspace_name,
        },
        args.timeout,
        channel="services",
        allowed_results={0},
        expected_status=None,
    )
    if rc == 0:
        print(f"gateway bind ok (workspace_id={args.workspace_id})")
    return rc


def connect_gimbal(client: Dock3PayloadControlClient, args) -> int:
    control = _gimbal_control_config(args)
    if not control["sdk_dir"] or not control["ip"] or not control["port"]:
        print(
            "gimbal connection config is incomplete; provide --sdk-dir, --gimbal-ip and --gimbal-port "
            "or fill payload.devices.gimbal.channels.control in config.",
            file=sys.stderr,
        )
        return 11

    steps = [
        ("viewlink_initialize", {"sdk_dir": control["sdk_dir"]}),
        ("viewlink_connect", {"ip": control["ip"], "port": control["port"], "conn_type": "tcp"}),
        ("viewlink_is_connected", {}),
    ]
    for method, data in steps:
        rc, reply = client.request(
            method,
            data,
            args.timeout,
            channel="services",
            allowed_results={0},
            expected_status=None,
        )
        if rc != 0:
            return rc
        if method == "viewlink_is_connected":
            viewlink_result = reply.get("data", {}).get("viewlink_result", {}) if isinstance(reply, dict) else {}
            if viewlink_result.get("connected") is not True:
                print(f"gimbal not connected after bind: {json.dumps(reply, ensure_ascii=False)}", file=sys.stderr)
                return 12
    print(f"gimbal connect ok (ip={control['ip']}, port={control['port']}, sdk_dir={control['sdk_dir']})")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 payload camera/gimbal service controls")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Require every payload-control method to return result=0.")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--gimbal-only", action="store_true", help="Only run the ViewLink gimbal cases that match the current single-payload setup.")
    parser.add_argument("--skip-bind-gateway", action="store_true", help="Do not auto-bind the gateway before service control cases.")
    parser.add_argument("--workspace-id", default="local_test_workspace")
    parser.add_argument("--workspace-name", default="本地测试工作空间")
    parser.add_argument("--connect-gimbal", action="store_true", help="Initialize and connect the ViewLink gimbal before control cases.")
    parser.add_argument("--bind-gimbal", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sdk-dir", default="", help="Override ViewLink SDK directory for --bind-gimbal.")
    parser.add_argument("--gimbal-ip", default="", help="Override gimbal IP for --bind-gimbal.")
    parser.add_argument("--gimbal-port", type=int, default=0, help="Override gimbal control port for --bind-gimbal.")
    parser.add_argument("--grab-authority", action="store_true", help="Send payload_authority_grab before the control cases.")
    parser.add_argument("--payload-index", default=None, help="Dock3 payload index, for example 52-0-0.")
    parser.add_argument("--camera-type", default="zoom")
    parser.add_argument("--camera-mode", type=int, default=0, help="0 photo, 1 video.")
    parser.add_argument("--resolution", default="1080p")
    parser.add_argument("--framerate", type=int, default=30)
    parser.add_argument("--video-id", default="payload-control-test")
    parser.add_argument("--drag-pitch-speed", type=float, default=0.0)
    parser.add_argument("--drag-yaw-speed", type=float, default=15.0)
    parser.add_argument("--gimbal-pitch", type=float, default=0.0)
    parser.add_argument("--gimbal-yaw", type=float, default=0.0)
    parser.add_argument("--zoom-ratio", type=float, default=2.0)
    parser.add_argument("--focal-length", type=float, default=24.0)
    parser.add_argument("--look-at-lat", type=float, default=31.230416)
    parser.add_argument("--look-at-lon", type=float, default=121.473701)
    parser.add_argument("--look-at-height", type=float, default=30.0)
    parser.add_argument("--split-enable", action="store_true")
    parser.add_argument("--photo-quality", default="high")
    parser.add_argument("--exposure-mode", default="auto")
    parser.add_argument("--exposure-value", type=int, default=16)
    parser.add_argument("--focus-mode", default="auto_focus")
    parser.add_argument("--focus-distance", type=float, default=0.0)
    parser.add_argument("--focus-duration", type=float, default=0.3)
    parser.add_argument("--focus-x", type=float, default=0.5)
    parser.add_argument("--focus-y", type=float, default=0.5)
    parser.add_argument("--ir-metering-mode", type=int, default=0)
    parser.add_argument("--ir-x", type=float, default=0.5)
    parser.add_argument("--ir-y", type=float, default=0.5)
    parser.add_argument("--ir-width", type=float, default=0.25)
    parser.add_argument("--ir-height", type=float, default=0.25)
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = Dock3PayloadControlClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_payload_control",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_payload_control_checks(client, args)
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
            "dock3 payload-control test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
