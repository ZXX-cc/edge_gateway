#!/usr/bin/env python3
"""Dock3 负载画面拖动手动测试脚本。

按键会通过 thing/product/{sn}/services 下发 camera_screen_drag，底层由
云台 ViewLink move() 执行。用于现场边看直播画面边确认拖动方向和速度。
"""

import argparse
import sys
import termios
import tty
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


class ScreenDragClient(MqttRequestClient):
    def services(self, method, data, timeout):
        return super().request(
            method,
            data,
            timeout,
            channel="services",
            allowed_results={0},
            expected_status=None,
        )


def read_key(args) -> str:
    if args.line_mode or not sys.stdin.isatty():
        return input("screen-drag> ").strip().lower()

    print("screen-drag> ", end="", flush=True)
    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        key = sys.stdin.read(1)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    print(key)
    return key.strip().lower()


def send_drag(client: ScreenDragClient, args, *, pitch_speed: float, yaw_speed: float) -> int:
    rc, _ = client.services(
        "camera_screen_drag",
        {
            "payload_index": args.payload_index,
            "locked": args.locked,
            "pitch_speed": pitch_speed,
            "yaw_speed": yaw_speed,
        },
        args.timeout,
    )
    if rc == 0:
        print(f"已下发拖动: pitch_speed={pitch_speed}, yaw_speed={yaw_speed}")
    return rc


def print_menu(args) -> None:
    print()
    print("画面拖动按键:")
    print("  w 上拖 / s 下拖 / a 左拖 / d 右拖")
    print("  空格 停止拖动")
    print("  + 提高速度 / - 降低速度")
    print("  q 退出")
    print(f"  payload_index={args.payload_index}, 当前速度={args.speed} degree/s")


def run(client: ScreenDragClient, args) -> int:
    print_menu(args)
    speed = float(args.speed)
    while True:
        try:
            key = read_key(args)
        except (EOFError, KeyboardInterrupt):
            print()
            key = "q"

        if key == "w":
            rc = send_drag(client, args, pitch_speed=speed, yaw_speed=0.0)
        elif key == "s":
            rc = send_drag(client, args, pitch_speed=-speed, yaw_speed=0.0)
        elif key == "a":
            rc = send_drag(client, args, pitch_speed=0.0, yaw_speed=-speed)
        elif key == "d":
            rc = send_drag(client, args, pitch_speed=0.0, yaw_speed=speed)
        elif key in {" ", "stop", "0"}:
            rc = send_drag(client, args, pitch_speed=0.0, yaw_speed=0.0)
        elif key in {"+", "="}:
            speed = min(args.max_speed, speed + args.speed_step)
            print(f"当前速度={speed} degree/s")
            rc = 0
        elif key in {"-", "_"}:
            speed = max(args.min_speed, speed - args.speed_step)
            print(f"当前速度={speed} degree/s")
            rc = 0
        elif key in {"", "h", "help", "?"}:
            print_menu(args)
            rc = 0
        elif key in {"q", "quit", "exit"}:
            send_drag(client, args, pitch_speed=0.0, yaw_speed=0.0)
            print("退出")
            return 0
        else:
            print(f"未知按键: {key!r}")
            print_menu(args)
            rc = 0

        if rc != 0:
            return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="手动测试 Dock3 camera_screen_drag 画面拖动")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--payload-index", default="52-0-0")
    parser.add_argument("--speed", type=float, default=2.0)
    parser.add_argument("--speed-step", type=float, default=0.5)
    parser.add_argument("--min-speed", type=float, default=0.5)
    parser.add_argument("--max-speed", type=float, default=10.0)
    parser.add_argument("--locked", action="store_true")
    parser.add_argument("--line-mode", action="store_true", help="使用传统输入模式，需要输入命令后再按回车")
    parser.add_argument("--quiet-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = ScreenDragClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_screen_drag",
    )
    if not client.connect(args.timeout):
        print("等待 MQTT 连接超时", file=sys.stderr)
        return 1

    try:
        return run(client, args)
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
