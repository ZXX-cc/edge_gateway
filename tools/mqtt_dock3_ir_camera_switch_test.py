#!/usr/bin/env python3
"""Test switching a Dock3 live stream to the infrared camera source.

The important assertion is not only that live_lens_change returns result=0,
but that the live_status_notify event reports the infrared source configured
in media.video_sources.ir.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, output_status, result_code  # noqa: E402
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT  # noqa: E402
from tool_config import add_config_argument, load_config, resolve_mqtt_settings  # noqa: E402


SUPPORTED_IR_ALIASES = {"ir", "infrared"}


class Dock3IrSwitchClient(MqttRequestClient):
    def service(
        self,
        method: str,
        data: dict[str, Any],
        timeout: float,
        *,
        allowed_results: set[int] | None = None,
        expected_status: str | None = None,
    ):
        return super().request(
            method,
            data,
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else set(allowed_results),
            expected_status=expected_status,
        )


def nested(data: dict[str, Any], *keys: str, default=None):
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def event_data(event: dict[str, Any] | None) -> dict[str, Any]:
    data = event.get("data", {}) if isinstance(event, dict) else {}
    return data if isinstance(data, dict) else {}


def configured_ir_source(config_path: str) -> str:
    config = load_config(config_path)
    return str(nested(config, "media", "video_sources", "ir", default="") or "")


def find_ir_capability(capability: dict[str, Any] | None) -> tuple[bool, list[str]]:
    camera_list = (
        capability.get("data", {})
        .get("live_capacity", {})
        .get("device_list", [{}])[0]
        .get("camera_list", [])
        if isinstance(capability, dict)
        else []
    )
    advertised: list[str] = []
    for camera in camera_list if isinstance(camera_list, list) else []:
        if not isinstance(camera, dict):
            continue
        for video in camera.get("video_list", []) or []:
            if not isinstance(video, dict):
                continue
            advertised.extend(str(item) for item in video.get("switchable_video_types", []) or [])
    return bool(SUPPORTED_IR_ALIASES.intersection(advertised)), advertised


def wait_live_event(
    client: Dock3IrSwitchClient,
    since: int,
    timeout: float,
    *,
    expected_status: str,
):
    rc, event = client.wait_event(
        "live_status_notify",
        since,
        timeout,
        expected_status=expected_status,
    )
    if rc != 0:
        return rc, event
    return 0, event


def stop_stream(client: Dock3IrSwitchClient, args, video_id: str) -> int:
    rc, reply = client.service(
        "live_stop_push",
        {"video_id": video_id},
        args.timeout,
        allowed_results={0, INVALID_COMMAND_FORMAT},
    )
    if rc != 0:
        return rc
    if result_code(reply) == INVALID_COMMAND_FORMAT:
        reason = str(reply.get("data", {}).get("reason", "") or reply.get("msg", "")).lower()
        if "not streaming" in reason:
            print(f"停止推流跳过: {video_id} 当前未推流")
            return 0
        print(f"停止推流异常: {json.dumps(reply, ensure_ascii=False)}", file=sys.stderr)
        return 12
    print(f"停止推流成功: {video_id}")
    return 0


def run_ir_switch(client: Dock3IrSwitchClient, args) -> int:
    ir_source = args.expected_source or configured_ir_source(args.config)
    if not ir_source:
        print("配置中没有 media.video_sources.ir，请用 --expected-source 指定红外源", file=sys.stderr)
        return 20
    print(f"期望红外源: {ir_source}")

    rc, capability = client.service(
        "live_capability_query",
        {},
        args.timeout,
        expected_status="ok",
    )
    if rc != 0:
        return rc
    has_ir, advertised = find_ir_capability(capability)
    print(f"live_capacity switchable_video_types: {advertised}")
    if not has_ir and not args.allow_capability_mismatch:
        print("live_capacity 没有声明 ir/infrared 可切换镜头", file=sys.stderr)
        return 21

    if args.stop_previous:
        rc = stop_stream(client, args, args.video_id)
        if rc != 0:
            return rc

    start_event_count = client.event_count("live_status_notify")
    rc, start = client.service(
        "live_start_push",
        {
            "url": args.url,
            "url_type": args.url_type,
            "video_id": args.video_id,
            "video_quality": args.quality,
            "video_type": args.start_video_type,
        },
        args.timeout,
        expected_status="in_progress",
    )
    if rc != 0:
        return rc
    if result_code(start) != 0 or output_status(start) != "in_progress":
        print(f"live_start_push 回包异常: {json.dumps(start, ensure_ascii=False)}", file=sys.stderr)
        return 22
    print(f"已启动直播: video_id={args.video_id}, video_type={args.start_video_type}")

    rc, start_event = wait_live_event(
        client,
        start_event_count,
        args.timeout,
        expected_status="in_progress",
    )
    if rc != 0:
        return rc
    print(f"启动事件: {json.dumps(event_data(start_event), ensure_ascii=False)}")

    lens_event_count = client.event_count("live_status_notify")
    lens_payload = {"video_type": args.target_video_type}
    if args.lens_video_id:
        lens_payload["video_id"] = args.lens_video_id
    rc, lens = client.service(
        "live_lens_change",
        lens_payload,
        args.timeout,
        expected_status="ok",
    )
    if rc != 0:
        return rc
    if result_code(lens) != 0 or output_status(lens) != "ok":
        print(f"live_lens_change 回包异常: {json.dumps(lens, ensure_ascii=False)}", file=sys.stderr)
        return 23
    print(f"已发送红外切换: {json.dumps(lens_payload, ensure_ascii=False)}")

    rc, lens_event = wait_live_event(
        client,
        lens_event_count,
        args.timeout,
        expected_status="ok",
    )
    if rc != 0:
        return rc
    data = event_data(lens_event)
    actual_type = str(data.get("video_type", "") or "")
    actual_source = str(data.get("source", "") or "")
    print(f"切换事件: {json.dumps(data, ensure_ascii=False)}")

    if actual_type != args.target_video_type:
        print(f"红外切换失败: event.video_type={actual_type!r}", file=sys.stderr)
        return 24
    if not args.no_source_check and actual_source != ir_source:
        print(
            "红外源未真正切换: "
            f"expected source={ir_source!r}, actual source={actual_source!r}",
            file=sys.stderr,
        )
        print(
            "提示: 如果 actual source 仍是云台可见光源，说明 live_lens_change "
            "只更新了 video_type，但 ffmpeg 输入源没有切到 media.video_sources.ir。",
            file=sys.stderr,
        )
        return 25

    if args.keep_stream:
        print("红外直播保持运行；需要停止时使用 --stop-only")
        return 0
    return stop_stream(client, args, args.video_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test switching Dock3 live stream to infrared camera")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--url", default="mock://dock3/ir-switch")
    parser.add_argument("--url-type", type=int, default=4)
    parser.add_argument("--video-id", default="")
    parser.add_argument("--start-video-type", choices=["normal", "wide", "zoom", "ir", "fpv", "gimbal"], default="gimbal")
    parser.add_argument("--target-video-type", choices=["ir"], default="ir")
    parser.add_argument("--lens-video-id", default="", help="Optional video_id sent with live_lens_change.")
    parser.add_argument("--quality", type=int, default=3)
    parser.add_argument("--expected-source", default="", help="Override expected IR source; defaults to media.video_sources.ir.")
    parser.add_argument("--allow-capability-mismatch", action="store_true")
    parser.add_argument("--no-source-check", action="store_true", help="Only check reply/event video_type, not event source.")
    parser.add_argument("--stop-previous", action="store_true")
    parser.add_argument("--keep-stream", action="store_true")
    parser.add_argument("--stop-only", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.video_id = args.video_id or f"{args.sn}/52-0-0/normal-0"

    client = Dock3IrSwitchClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_ir_camera_switch",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 1

    try:
        if args.stop_only:
            return stop_stream(client, args, args.video_id)
        rc = run_ir_switch(client, args)
        if rc != 0:
            return rc
        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 30
        print(
            "dock3 ir camera switch test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
