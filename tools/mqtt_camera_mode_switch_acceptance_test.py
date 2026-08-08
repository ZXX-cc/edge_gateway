#!/usr/bin/env python3
"""相机模式切换 camera_mode_switch 上云专项验收脚本。

用于现场真实网关测试，重点验证：

* camera_mode_switch 切换拍照、录像、回放模式是否生效
* 切换成功/失败后，云端 services_reply 的 result 是否符合预期
* 重复切换、非法模式、录像中设备忙是否返回正确的非 0 错误码

脚本只发布网关已有的 Dock3 上云 services 方法，不直连相机、不新增协议：
gateway_state_query、camera_mode_switch、camera_recording_start、
camera_recording_stop。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from mqtt_viewlink_camera_mode_test import _check_bound
from tool_config import add_config_argument, resolve_mqtt_settings


VIEWLINK_RECORD_MODES = {"photo": 1, "video": 2}
DOCK3_CAMERA_MODES = {"photo": 0, "video": 1, "playback": 2}
MODE_NAMES_CN = {"photo": "拍照模式", "video": "录像模式", "playback": "回放模式"}
CAMERA_TYPE_NAMES_CN = {
    "zoom": "正常光",
    "normal": "正常光",
    "visible": "正常光",
    "ir": "红外光",
    "infrared": "红外光",
    "thermal": "红外光",
}
CAMERA_TYPE_IMAGE_TYPES = {
    "zoom": 0,
    "normal": 0,
    "visible": 0,
    "ir": 2,
    "infrared": 2,
    "thermal": 2,
}


def _reply_data(reply: Any) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {}
    data = reply.get("data")
    return data if isinstance(data, dict) else {}


def _viewlink_result(reply: Any) -> dict[str, Any]:
    result = _reply_data(reply).get("viewlink_result")
    return result if isinstance(result, dict) else {}


def _request_service(
    client: MqttRequestClient,
    method: str,
    data: dict[str, Any],
    timeout: float,
    *,
    verbose: bool = False,
) -> tuple[int, dict[str, Any]]:
    rc, reply = client.request(
        method,
        data,
        timeout,
        channel="services",
        allowed_results=None,
        verbose=verbose,
    )
    return rc, reply if isinstance(reply, dict) else {}


def _switch_mode(
    client: MqttRequestClient,
    args,
    mode: str,
    *,
    expect_success: bool,
    label: str,
) -> tuple[int, dict[str, Any]]:
    payload = {
        "payload_index": args.payload_index,
        "camera_type": args.camera_type,
        "camera_mode": DOCK3_CAMERA_MODES.get(mode, args.invalid_camera_mode),
        "mode": mode,
    }
    mode_cn = MODE_NAMES_CN.get(mode, mode)
    print(f"\n[相机模式切换] 下发 camera_mode_switch -> {mode_cn} ({mode})")
    rc, reply = _request_service(client, "camera_mode_switch", payload, args.timeout, verbose=True)
    summary: dict[str, Any] = {
        "case": label,
        "用例": f"相机模式切换-{mode_cn}",
        "method": "camera_mode_switch",
        "测试点": "云端通过 services 下发 camera_mode_switch，并按上云 result-only 回包校验错误码",
        "request": payload,
        "期望相机模式": mode_cn,
        "期望camera_mode": payload["camera_mode"],
        "reply_result": result_code(reply),
        "说明": "camera_mode_switch 属于 result-only services 方法，上云回包会过滤 mode/camera_type_result/viewlink_result",
    }
    if rc != 0:
        summary["error"] = "request_failed"
        return rc, summary

    reply_result = result_code(reply)
    if expect_success:
        if reply_result != 0:
            summary["error"] = "expected_success"
            return 10, summary
    else:
        if reply_result == 0:
            summary["error"] = "expected_nonzero_error_code"
            return 12, summary
        return 0, summary

    return 0, summary


def _switch_camera_type(
    client: MqttRequestClient,
    args,
    camera_type: str,
    *,
    label: str,
) -> tuple[int, dict[str, Any]]:
    """Switch visible/IR image source through camera_mode_switch."""
    camera_type = str(camera_type).strip().lower()
    expected_image_type = CAMERA_TYPE_IMAGE_TYPES.get(camera_type)
    camera_type_cn = CAMERA_TYPE_NAMES_CN.get(camera_type, camera_type)
    payload = {
        "payload_index": args.payload_index,
        "camera_type": camera_type,
        "camera_mode": DOCK3_CAMERA_MODES["photo"],
        "mode": "photo",
    }
    print(f"\n[相机模式切换] 下发 camera_mode_switch -> {camera_type_cn} ({camera_type})")
    rc, reply = _request_service(client, "camera_mode_switch", payload, args.timeout, verbose=True)
    data = _reply_data(reply)
    camera_type_result = data.get("camera_type_result")
    camera_type_result = camera_type_result if isinstance(camera_type_result, dict) else {}
    summary: dict[str, Any] = {
        "case": label,
        "用例": f"相机模式切换-{camera_type_cn}",
        "method": "camera_mode_switch",
        "测试点": "通过 camera_mode_switch 的 camera_type 字段切换正常光/红外光",
        "request": payload,
        "期望光路": camera_type_cn,
        "期望camera_type": camera_type,
        "期望image_type": expected_image_type,
        "reply_result": result_code(reply),
        "说明": (
            "camera_mode_switch 上云回包按 result-only 收敛，平台侧不能直接看到 "
            "camera_type_result.image_type；正常光/红外光实际效果需观察 RTSP 画面或网关 payload 日志"
        ),
    }
    if rc != 0:
        summary["error"] = "request_failed"
        return rc, summary
    if result_code(reply) != 0:
        summary["error"] = "camera_type_switch_failed"
        return 50, summary
    return 0, summary


def _negative_mode_case(client: MqttRequestClient, args) -> tuple[int, dict[str, Any]]:
    payload = {
        "payload_index": args.payload_index,
        "camera_type": args.camera_type,
        "camera_mode": args.invalid_camera_mode,
        "mode": args.invalid_mode,
    }
    print(f"\n[相机模式切换] 下发非法 camera_mode_switch -> {args.invalid_mode}/{args.invalid_camera_mode}")
    rc, reply = _request_service(client, "camera_mode_switch", payload, args.timeout, verbose=True)
    summary: dict[str, Any] = {
        "case": "illegal_mode",
        "用例": "相机模式切换-非法模式",
        "method": "camera_mode_switch",
        "测试点": "非法相机模式必须返回非 0 错误码",
        "request": payload,
        "reply_result": result_code(reply),
        "说明": "camera_mode_switch 属于 result-only services 方法，上云回包只校验 result 错误码",
    }
    if rc != 0:
        summary["error"] = "request_failed"
        return rc, summary
    if result_code(reply) == 0:
        summary["error"] = "illegal_mode_was_accepted"
        return 20, summary

    return 0, summary


def _busy_case(client: MqttRequestClient, args) -> tuple[int, dict[str, Any]]:
    start_payload = {
        "payload_index": args.payload_index,
        "camera_index": args.camera_index,
        "camera_type": args.camera_type,
    }
    rc, start_reply = _request_service(
        client,
        "camera_recording_start",
        start_payload,
        args.timeout,
        verbose=True,
    )
    summary: dict[str, Any] = {
        "case": "device_busy_during_recording",
        "用例": "相机模式切换-录像中设备忙",
        "method": "camera_mode_switch",
        "测试点": "录像过程中切换到拍照模式应返回非 0 错误码",
        "start_recording_result": result_code(start_reply),
        "说明": "camera_recording_start/stop 和 camera_mode_switch 都是 result-only services 方法",
    }
    if rc != 0 or result_code(start_reply) != 0:
        summary["error"] = "recording_start_failed"
        return 30, summary

    try:
        time.sleep(max(0.0, args.busy_settle))
        rc, reply = _request_service(
            client,
            "camera_mode_switch",
            {
                "payload_index": args.payload_index,
                "camera_type": args.camera_type,
                "camera_mode": 0,
                "mode": "photo",
            },
            args.timeout,
            verbose=True,
        )
        summary["switch_reply_result"] = result_code(reply)
        if rc != 0:
            summary["error"] = "busy_switch_request_failed"
            return rc, summary
        if result_code(reply) == 0:
            summary["error"] = "busy_switch_was_accepted"
            return 31, summary

        return 0, summary
    finally:
        stop_rc, stop_reply = _request_service(
            client,
            "camera_recording_stop",
            start_payload,
            args.timeout,
            verbose=True,
        )
        summary["stop_recording_rc"] = stop_rc
        summary["stop_recording_result"] = result_code(stop_reply)


def _print_summary(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Use the real gateway Cloud API path to test camera_mode_switch."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--sdk-dir", default="")
    parser.add_argument("--gimbal-ip", default="")
    parser.add_argument("--gimbal-port", type=int, default=0)
    parser.add_argument("--payload-index", default="52-0-0")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-type", default="zoom")
    parser.add_argument("--visible-camera-type", default="zoom", help="正常光 camera_type，默认 zoom。")
    parser.add_argument("--ir-camera-type", default="ir", help="红外光 camera_type，默认 ir。")
    parser.add_argument("--mode-settle", type=float, default=0.5)
    parser.add_argument("--busy-settle", type=float, default=1.0)
    parser.add_argument("--invalid-mode", default="illegal_mode")
    parser.add_argument("--invalid-camera-mode", type=int, default=99)
    parser.add_argument("--skip-bind-check", action="store_true")
    parser.add_argument("--skip-viewlink-connect", action="store_true")
    parser.add_argument("--skip-playback", action="store_true")
    parser.add_argument("--skip-visible-ir", action="store_true", help="跳过正常光/红外光 camera_type 切换用例。")
    parser.add_argument("--expect-playback-success", action="store_true")
    parser.add_argument("--skip-illegal-mode", action="store_true")
    parser.add_argument("--skip-busy", action="store_true")
    parser.add_argument(
        "--require-active-record-mode",
        action="store_true",
        help="Compatibility option; ignored because this strict script does not call non-allowlisted readback methods.",
    )
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the Cloud API methods that would be used, without publishing MQTT.",
    )
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.dry_run:
        cases = [
            {
                "用例": "相机模式切换-正常光",
                "method": "camera_mode_switch",
                "data": {"camera_mode": 0, "mode": "photo", "camera_type": args.visible_camera_type},
                "内部预期": "网关内部 camera_type_result.image_type == 0",
                "实际上云回包": "result-only，仅校验 result=0；光路效果需观察 RTSP/日志",
            },
            {
                "用例": "相机模式切换-正常光切换为红外光",
                "method": "camera_mode_switch",
                "data": {"camera_mode": 0, "mode": "photo", "camera_type": args.ir_camera_type},
                "内部预期": "网关内部 camera_type_result.image_type == 2",
                "实际上云回包": "result-only，仅校验 result=0；光路效果需观察 RTSP/日志",
            },
            {
                "用例": "相机模式切换-红外光切回正常光",
                "method": "camera_mode_switch",
                "data": {"camera_mode": 0, "mode": "photo", "camera_type": args.visible_camera_type},
                "内部预期": "网关内部 camera_type_result.image_type == 0",
                "实际上云回包": "result-only，仅校验 result=0；光路效果需观察 RTSP/日志",
            },
            {
                "用例": "相机模式切换-拍照模式",
                "method": "camera_mode_switch",
                "data": {"camera_mode": 0, "mode": "photo"},
            },
            {
                "用例": "相机模式切换-重复拍照模式",
                "method": "camera_mode_switch",
                "data": {"camera_mode": 0, "mode": "photo"},
            },
            {
                "用例": "相机模式切换-录像模式",
                "method": "camera_mode_switch",
                "data": {"camera_mode": 1, "mode": "video"},
            },
            {
                "用例": "相机模式切换-重复录像模式",
                "method": "camera_mode_switch",
                "data": {"camera_mode": 1, "mode": "video"},
            },
            {
                "用例": "相机模式切换-回放模式",
                "method": "camera_mode_switch",
                "data": {"camera_mode": 2, "mode": "playback"},
            },
            {
                "用例": "相机模式切换-非法模式",
                "method": "camera_mode_switch",
                "data": {"camera_mode": args.invalid_camera_mode, "mode": args.invalid_mode},
            },
            {
                "用例": "相机模式切换-录像中设备忙",
                "method": "camera_mode_switch",
                "data": {"camera_mode": 0, "mode": "photo"},
                "前置": "先通过 camera_recording_start 让真实相机进入录像中",
                "清理": "最后通过 camera_recording_stop 停止录像",
            },
        ]
        print(json.dumps({
            "dry_run": True,
            "说明": "以下所有用例均通过 thing/product/{sn}/services 上云下发，不直连相机",
            "只使用allowlist方法": [
                "gateway_state_query",
                "camera_mode_switch",
                "camera_recording_start",
                "camera_recording_stop",
            ],
            "相机模式切换用例": cases,
            "状态校验依据": "camera_mode_switch 是 result-only 上云回包，本脚本只校验 result 错误码",
        }, ensure_ascii=False, indent=2))
        return 0

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_camera_mode_switch_acceptance",
    )
    if not client.connect(args.timeout):
        print(f"MQTT connect timeout: host={args.host} port={args.port}", file=sys.stderr)
        return 2

    summaries: list[dict[str, Any]] = []
    try:
        rc = _check_bound(client, args.timeout, skip=args.skip_bind_check)
        if rc != 0:
            return rc

        if args.require_active_record_mode:
            summaries.append({
                "case": "require_active_record_mode_ignored",
                "说明": "--require-active-record-mode 已忽略；当前脚本严格不调用 viewlink_get_record_mode 等非 allowlist 方法",
            })

        if rc == 0 and not args.skip_visible_ir:
            for label, camera_type in (
                ("相机模式切换-正常光", args.visible_camera_type),
                ("相机模式切换-正常光切换为红外光", args.ir_camera_type),
                ("相机模式切换-红外光切回正常光", args.visible_camera_type),
            ):
                rc, summary = _switch_camera_type(
                    client,
                    args,
                    camera_type,
                    label=label,
                )
                summaries.append(summary)
                if rc != 0:
                    break

        if rc == 0:
            for label, mode in (
                ("相机模式切换-拍照模式", "photo"),
                ("相机模式切换-重复拍照模式", "photo"),
                ("相机模式切换-录像模式", "video"),
                ("相机模式切换-重复录像模式", "video"),
            ):
                rc, summary = _switch_mode(
                    client,
                    args,
                    mode,
                    expect_success=True,
                    label=label,
                )
                summaries.append(summary)
                if rc != 0:
                    break

        if rc == 0 and not args.skip_playback:
            rc, summary = _switch_mode(
                client,
                args,
                "playback",
                expect_success=args.expect_playback_success,
                label="相机模式切换-回放模式",
            )
            summaries.append(summary)

        if rc == 0 and not args.skip_illegal_mode:
            rc, summary = _negative_mode_case(client, args)
            summaries.append(summary)

        if rc == 0 and not args.skip_busy:
            rc, summary = _busy_case(client, args)
            summaries.append(summary)

        for item in summaries:
            _print_summary(item)

        if rc != 0:
            print("camera_mode_switch acceptance test failed", file=sys.stderr)
            return rc

        print(
            "camera_mode_switch acceptance test ok "
            f"(gateway={args.sn}, payload_index={args.payload_index})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
