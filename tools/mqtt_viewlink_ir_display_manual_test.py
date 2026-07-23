#!/usr/bin/env python3
"""Manually switch the ViewLink camera display mode through MQTT services."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient  # noqa: E402
from mqtt_viewlink_visible_test import _ensure_connected, _viewlink_result  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


def _request_display_mode(client: MqttRequestClient, args) -> int:
    payload = {
        "payload_index": args.payload_index,
        "mode": args.mode,
        "palette": args.palette,
    }
    rc, reply = client.request(
        "ir_display_mode_set",
        payload,
        args.timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        print(f"ir_display_mode_set MQTT/网关层失败: rc={rc}", file=sys.stderr)
        return rc
    result = _viewlink_result(reply)
    if not result.get("success", False):
        print(f"ir_display_mode_set 未返回 SDK 成功: {result}", file=sys.stderr)
        return 20
    print(f"已发送显示切换: mode={args.mode}, palette={args.palette}, result={result}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manually switch ViewLink IR display mode")
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
    parser.add_argument(
        "--mode",
        choices=["visible", "ir", "visible_ir", "ir_visible", "fusion"],
        default="ir",
        help="ir=红外主画面；visible=可见光主画面；ir_visible=红外主画面+可见光小窗。",
    )
    parser.add_argument(
        "--palette",
        choices=["white_hot", "black_hot", "pseudo_hot", "rusty"],
        default="white_hot",
    )
    parser.add_argument("--quiet-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_viewlink_ir_display_manual",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = _ensure_connected(client, args)
        if rc != 0:
            return rc
        print("ViewLink 已连接；请先在 VLC 确认当前 rtsp://192.168.1.119:554/main 画面。")
        print("按 Enter 手动切换显示模式，输入 q 后回车则退出不切换。")
        choice = input("ir-display> ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            print("已取消显示切换。")
            return 0
        return _request_display_mode(client, args)
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
