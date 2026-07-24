#!/usr/bin/env python3
"""Dock3 直播源手动切换测试脚本。

用于云台和正射相机共用同一个公网 RTMP 推流地址的现场测试。脚本启动后会
一直停在交互提示符，等待人工输入切换命令；每次切换前会先停止当前视频源，
再启动目标视频源，避免两个负载同时抢占同一个推流地址。
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows only
    msvcrt = None

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - POSIX only
    termios = None
    tty = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code  # noqa: E402
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


class Dock3LiveSwitchClient(MqttRequestClient):
    def request(self, method, data, timeout, allowed_results=None, expected_status=None):
        return super().request(
            method,
            data,
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else set(allowed_results),
            expected_status=expected_status,
        )


def print_step(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def start_stream(client: Dock3LiveSwitchClient, *, label: str, url: str, url_type: int, video_id: str, quality: int, timeout: float) -> int:
    print_step(f"启动 {label}: {video_id}")
    rc, reply = client.request(
        "live_start_push",
        {
            "url": url,
            "url_type": url_type,
            "video_id": video_id,
            "video_quality": quality,
        },
        timeout,
        [0],
        "in_progress",
    )
    if rc == 0:
        print(f"{label} 推流已启动")
    return rc


def set_quality(client: Dock3LiveSwitchClient, *, video_id: str, quality: int, timeout: float) -> int:
    print_step(f"设置直播清晰度: {video_id} -> {quality}")
    rc, _ = client.request(
        "live_set_quality",
        {"video_id": video_id, "video_quality": quality},
        timeout,
        [0],
        "ok",
    )
    if rc == 0:
        print(f"直播清晰度已设置为 {quality}")
    return rc


def set_lens(client: Dock3LiveSwitchClient, *, video_id: str, video_type: str, timeout: float) -> int:
    print_step(f"设置直播镜头: {video_id} -> {video_type}")
    rc, _ = client.request(
        "live_lens_change",
        {"video_id": video_id, "video_type": video_type},
        timeout,
        [0],
        "ok",
    )
    if rc == 0:
        print(f"直播镜头已设置为 {video_type}")
    return rc


def stop_stream(client: Dock3LiveSwitchClient, *, label: str, video_id: str, timeout: float) -> int:
    print_step(f"停止 {label}: {video_id}")
    rc, reply = client.request(
        "live_stop_push",
        {"video_id": video_id},
        timeout,
        [0, INVALID_COMMAND_FORMAT],
    )
    if rc != 0:
        return rc
    if result_code(reply) == INVALID_COMMAND_FORMAT:
        reason = str(reply.get("data", {}).get("reason", "") or reply.get("msg", "")).lower()
        if "not streaming" in reason:
            print(f"{label} 本来就没有在推流")
            return 0
        print(f"{label} 停止推流被拒绝: {json.dumps(reply, ensure_ascii=False)}", file=sys.stderr)
        return 3
    print(f"{label} 推流已停止")
    return 0


def show_capability(client: Dock3LiveSwitchClient, args) -> int:
    print_step("直播能力")
    rc, capability = client.request("live_capability_query", {}, args.timeout, [0], "ok")
    if rc != 0:
        return rc

    camera_list = (
        capability.get("data", {})
        .get("live_capacity", {})
        .get("device_list", [{}])[0]
        .get("camera_list", [])
    )
    print("可用相机:")
    for camera in camera_list:
        videos = camera.get("video_list", []) if isinstance(camera, dict) else []
        print(f"  camera_index={camera.get('camera_index')} videos={videos}")
    return 0


def print_menu(args, active_label: str) -> None:
    print()
    print("直播源手动切换命令:")
    print(f"  1 / gimbal   -> 推云台      {args.gimbal_video_id}")
    print(f"  2 / mapping  -> 推正射相机  {args.mapping_video_id}")
    print("  + / -        -> 提高/降低直播清晰度")
    print("  0 / 3 / 4    -> 设置清晰度: 0=自适应, 3=高清, 4=超清")
    print("  n/w/z/i/f    -> 设置镜头: normal/wide/zoom/ir/fpv")
    print("  s / stop     -> 停止当前推流")
    print("  c / cap      -> 重新查询 live_capacity")
    print("  q / quit     -> 退出脚本")
    if args.play_url:
        print(f"  播放地址: {args.play_url}")
    print(f"  当前: {active_label or '未推流'}")


def normalize_command(command: str) -> str:
    """兼容现场输入时可能混入的空格、全角数字、回车符或 § 前缀。"""
    cleaned = command.strip().lower().replace("１", "1").replace("２", "2").replace("\r", "")
    if cleaned.startswith("§"):
        cleaned = cleaned[1:].strip()
    return cleaned


def read_command(args) -> str:
    if args.line_mode or not sys.stdin.isatty():
        return normalize_command(input("live-switch> "))

    print("live-switch> ", end="", flush=True)
    if msvcrt is not None:
        command = msvcrt.getwch()
        print(command)
        return normalize_command(command)

    if termios is None or tty is None:
        return normalize_command(input())

    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        command = sys.stdin.read(1)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    print(command)
    return normalize_command(command)


def switch_to(client: Dock3LiveSwitchClient, args, *, target_label: str, target_video_id: str, active: dict) -> int:
    if active.get("video_id") == target_video_id:
        print(f"{target_label} 已经在推流")
        return 0
    if active.get("video_id"):
        # 云台和正射相机共用同一个公网推流地址，切换前必须先停止当前视频源。
        rc = stop_stream(
            client,
            label=active.get("label", "current"),
            video_id=active["video_id"],
            timeout=args.timeout,
        )
        if rc != 0:
            return rc
    rc = start_stream(
        client,
        label=target_label,
        url=args.url,
        url_type=args.url_type,
        video_id=target_video_id,
        quality=args.quality,
        timeout=args.timeout,
    )
    if rc != 0:
        return rc
    active["label"] = target_label
    active["video_id"] = target_video_id
    active["quality"] = str(args.quality)
    if args.play_url:
        print(f"查看 {target_label}: {args.play_url}")
    return 0


def set_active_quality(client: Dock3LiveSwitchClient, args, active: dict, quality: int) -> int:
    if not active.get("video_id"):
        print("请先按 1 或 2 启动一个直播源，再设置直播清晰度")
        return 0
    quality = max(0, min(4, quality))
    rc = set_quality(client, video_id=active["video_id"], quality=quality, timeout=args.timeout)
    if rc == 0:
        active["quality"] = str(quality)
        args.quality = quality
    return rc


def set_active_lens(client: Dock3LiveSwitchClient, args, active: dict, video_type: str) -> int:
    if not active.get("video_id"):
        print("请先按 1 或 2 启动一个直播源，再设置直播镜头")
        return 0
    rc = set_lens(client, video_id=active["video_id"], video_type=video_type, timeout=args.timeout)
    if rc == 0:
        active["video_type"] = video_type
    return rc


def stop_active(client: Dock3LiveSwitchClient, args, active: dict) -> int:
    if not active.get("video_id"):
        print("当前脚本没有记录正在推流的视频源")
        return 0
    rc = stop_stream(
        client,
        label=active.get("label", "current"),
        video_id=active["video_id"],
        timeout=args.timeout,
    )
    if rc == 0:
        active.clear()
    return rc


def run_manual_switch(client: Dock3LiveSwitchClient, args) -> int:
    rc = show_capability(client, args)
    if rc != 0:
        return rc

    active: dict[str, str] = {}
    if args.stop_previous:
        # 现场多次测试时，先清理可能遗留的推流状态，避免 RTMP 地址被占用。
        stop_stream(client, label="previous/gimbal", video_id=args.gimbal_video_id, timeout=args.timeout)
        stop_stream(client, label="previous/mapping", video_id=args.mapping_video_id, timeout=args.timeout)

    print_menu(args, active.get("label", ""))
    while True:
        try:
            command = read_command(args)
        except (EOFError, KeyboardInterrupt):
            print()
            command = "q"

        if command:
            print(f"收到命令: {command}")

        if command in {"1", "g", "gimbal", "云台"}:
            rc = switch_to(
                client,
                args,
                target_label="gimbal",
                target_video_id=args.gimbal_video_id,
                active=active,
            )
            if rc != 0:
                return rc
        elif command in {"2", "m", "mapping", "camera", "mapping_camera", "相机", "正射", "正射相机"}:
            rc = switch_to(
                client,
                args,
                target_label="mapping_camera",
                target_video_id=args.mapping_video_id,
                active=active,
            )
            if rc != 0:
                return rc
        elif command in {"+", "="}:
            current_quality = int(active.get("quality", args.quality))
            rc = set_active_quality(client, args, active, current_quality + 1)
            if rc != 0:
                return rc
        elif command in {"-", "_"}:
            current_quality = int(active.get("quality", args.quality))
            rc = set_active_quality(client, args, active, current_quality - 1)
            if rc != 0:
                return rc
        elif command in {"0", "3", "4"}:
            rc = set_active_quality(client, args, active, int(command))
            if rc != 0:
                return rc
        elif command in {"n", "normal"}:
            rc = set_active_lens(client, args, active, "normal")
            if rc != 0:
                return rc
        elif command in {"w", "wide"}:
            rc = set_active_lens(client, args, active, "wide")
            if rc != 0:
                return rc
        elif command in {"z", "zoom"}:
            rc = set_active_lens(client, args, active, "zoom")
            if rc != 0:
                return rc
        elif command in {"i", "ir"}:
            rc = set_active_lens(client, args, active, "ir")
            if rc != 0:
                return rc
        elif command in {"f", "fpv"}:
            rc = set_active_lens(client, args, active, "fpv")
            if rc != 0:
                return rc
        elif command in {"s", "stop"}:
            rc = stop_active(client, args, active)
            if rc != 0:
                return rc
        elif command in {"c", "cap", "capability"}:
            rc = show_capability(client, args)
            if rc != 0:
                return rc
        elif command in {"q", "quit", "exit"}:
            if active.get("video_id") and not args.keep_on_exit:
                rc = stop_active(client, args, active)
                if rc != 0:
                    return rc
            print("退出")
            return 0
        elif command in {"", "h", "help", "?"}:
            print_menu(args, active.get("label", ""))
        else:
            print(f"未知命令: {command}")
            print_menu(args, active.get("label", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="手动切换 Dock3 云台/正射相机直播源")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--url", required=True, help="公网推流地址，RTMP 时填写 rtmp://...")
    parser.add_argument("--play-url", default="", help="播放地址，仅用于在控制台提示现场查看")
    parser.add_argument("--url-type", type=int, default=1, help="Dock3 url_type，1 表示 RTMP")
    parser.add_argument("--gimbal-video-id", default="")
    parser.add_argument("--mapping-video-id", default="")
    parser.add_argument("--quality", type=int, default=3)
    parser.add_argument("--stop-previous", action="store_true", help="进入交互前先停止已知的云台和正射相机视频源")
    parser.add_argument("--keep-on-exit", action="store_true", help="退出脚本时保留当前推流")
    parser.add_argument("--line-mode", action="store_true", help="使用传统输入模式，需要输入命令后再按回车")
    parser.add_argument("--quiet-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.gimbal_video_id = args.gimbal_video_id or f"{args.sn}/52-0-0/normal-0"
    args.mapping_video_id = args.mapping_video_id or f"{args.sn}/52-0-1/mapping-0"

    client = Dock3LiveSwitchClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_live_switch",
        subscribe_events=False,
    )
    if not client.connect(args.timeout):
        print("等待 MQTT 连接超时", file=sys.stderr)
        return 1

    try:
        rc = run_manual_switch(client, args)
        if rc == 0:
            print()
            print("Dock3 手动直播源切换测试结束")
        return rc
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
