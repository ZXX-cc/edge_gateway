#!/usr/bin/env python3
"""Test whether direct ViewLink recording interrupts an RTSP preview stream."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient
from mqtt_viewlink_camera_mode_test import (
    _check_bound,
    _ensure_viewlink_connected,
    _request_service,
    _switch_camera_mode,
)
from mqtt_viewlink_stream_mode_impact_test import RtspMonitor, _print_phase, _read_record_mode
from tool_config import add_config_argument, resolve_mqtt_settings
from viewlink_media_download import (
    discover_media_links,
    download_file,
    latest_media_link_by_http_metadata,
)


VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv")


def _start_recording(client: MqttRequestClient, args) -> int:
    payload = {
        "payload_index": args.payload_index,
        "camera_index": args.camera_index,
        "camera_type": args.camera_type,
        "resolution": args.resolution,
        "framerate": args.framerate,
        "encoding": args.encoding,
    }
    print("start independent ViewLink recording")
    rc, _reply = _request_service(client, "camera_recording_start", payload, args.timeout, verbose=True)
    return rc


def _stop_recording(client: MqttRequestClient, args, *, allow_not_recording: bool = False) -> int:
    payload = {
        "payload_index": args.payload_index,
        "camera_index": args.camera_index,
        "camera_type": args.camera_type,
    }
    print("stop independent ViewLink recording")
    rc, reply = _request_service(client, "camera_recording_stop", payload, args.timeout, verbose=True)
    if rc == 0:
        return 0
    reason = str(reply or "").lower()
    if allow_not_recording and "not recording" in reason:
        print("recording stop skipped: device already not recording")
        return 0
    return rc


def _build_monitor(args) -> RtspMonitor:
    return RtspMonitor(
        ffmpeg=args.ffmpeg,
        rtsp_url=args.rtsp_url,
        rtsp_transport=args.rtsp_transport,
        startup_timeout=args.stream_startup_timeout,
        stall_threshold=args.stall_threshold,
        verbose=args.verbose_ffmpeg,
    )


def _discover_videos(args) -> list[str]:
    return discover_media_links(
        args.media_url,
        args.media_timeout,
        VIDEO_EXTENSIONS,
        max_depth=max(0, args.media_max_depth),
    )


def _latest_new_video(args, previous_urls: set[str] | None) -> str:
    print(f"read ViewLink media page for recorded video: {args.media_url}")
    deadline = time.time() + max(0.0, args.media_poll_timeout)
    videos: list[str] = []
    while True:
        videos = _discover_videos(args)
        if previous_urls is None:
            break
        new_videos = [video for video in videos if video not in previous_urls]
        if new_videos:
            videos = new_videos
            break
        if time.time() >= deadline:
            raise RuntimeError("no new recorded video appeared on ViewLink media page")
        time.sleep(max(0.1, args.media_poll_interval))
    if not videos:
        raise RuntimeError("no recorded video links found on ViewLink media page")
    return latest_media_link_by_http_metadata(videos, args.media_timeout)


def _retrieve_recorded_video(args, previous_urls: set[str] | None) -> str:
    video_url = _latest_new_video(args, previous_urls)
    print(f"retrieved_video_url={video_url}")

    if args.download_retrieved_video:
        target = download_file(video_url, Path(args.output_dir), args.media_timeout)
        print(f"retrieved_video_saved={target.resolve()}")

    if args.play_retrieved_video:
        executable = shutil.which(args.ffmpeg) or args.ffmpeg
        command = [
            executable,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-i",
            video_url,
        ]
        if args.retrieve_duration > 0:
            command.extend(["-t", f"{args.retrieve_duration:.3f}"])
        command.extend(["-an", "-f", "null", os.devnull])
        print("retrieve recorded video with ffmpeg: " + " ".join(command))
        completed = subprocess.run(command, timeout=args.retrieve_timeout, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"recorded video retrieve failed: ffmpeg exit={completed.returncode}")

    return video_url


def run_test(
    client: MqttRequestClient,
    args,
    *,
    monitor: RtspMonitor | None = None,
    sleep_fn=time.sleep,
) -> int:
    monitor = monitor or _build_monitor(args)
    recording_started = False
    previous_videos = None
    retrieved_video_url = ""

    if not monitor.start():
        monitor.stop()
        return 50

    try:
        if args.media_url and args.retrieve_recording:
            try:
                previous_videos = set(_discover_videos(args))
                print(f"ViewLink media baseline videos={len(previous_videos)}")
            except Exception as exc:
                print(f"failed to read ViewLink video baseline: {exc}", file=sys.stderr)
                return 40

        print(f"warm up RTSP stream for {args.stream_warmup:.1f}s")
        sleep_fn(args.stream_warmup)
        _print_phase("before_recording", monitor)

        before_mode = _read_record_mode(client, args)
        print(f"record_mode_before={json.dumps(before_mode, ensure_ascii=False)}")

        if args.switch_video_before_recording:
            rc = _switch_camera_mode(client, args, "video")
            if rc != 0:
                return rc
            _print_phase("after_switch_video_before_recording", monitor)
        else:
            print("skip camera_mode_switch before recording; call camera_recording_start while RTSP is running")

        after_precheck_mode = _read_record_mode(client, args)
        print(f"record_mode_after_precheck={json.dumps(after_precheck_mode, ensure_ascii=False)}")

        rc = _start_recording(client, args)
        if rc != 0:
            return rc
        recording_started = True
        _print_phase("after_start_recording", monitor)

        print(f"record while observing RTSP stream for {args.record_duration:.1f}s")
        sleep_fn(args.record_duration)
        _print_phase("during_recording_observe", monitor)

        rc = _stop_recording(client, args)
        recording_started = False
        if rc != 0:
            return rc
        _print_phase("after_stop_recording", monitor)

        print(f"observe RTSP stream after recording stop for {args.stream_observe:.1f}s")
        sleep_fn(args.stream_observe)
        _print_phase("after_recording_observe", monitor)

        if args.switch_video_after_recording:
            rc = _switch_camera_mode(client, args, "video")
            if rc != 0:
                return rc
            _print_phase("after_switch_video", monitor)
            print(f"observe RTSP stream after switching video mode for {args.stream_observe_after_video:.1f}s")
            sleep_fn(args.stream_observe_after_video)
            _print_phase("after_video_observe", monitor)

        if args.retrieve_recording:
            if not args.media_url:
                print("skip recorded video retrieve: --media-url was not provided")
            else:
                try:
                    retrieved_video_url = _retrieve_recorded_video(args, previous_videos)
                except Exception as exc:
                    print(f"recorded video retrieve failed: {exc}", file=sys.stderr)
                    return 41
                _print_phase("after_retrieve_recorded_video", monitor)
                print(f"observe RTSP stream after recorded video retrieve for {args.stream_observe_after_retrieve:.1f}s")
                sleep_fn(args.stream_observe_after_retrieve)
                _print_phase("after_retrieve_observe", monitor)

        return 0
    finally:
        if recording_started and args.stop_recording_on_error:
            _stop_recording(client, args, allow_not_recording=True)
        monitor.stop()
        summary = {
            "scenario": (
                "direct_record_then_video"
                if args.switch_video_after_recording and not args.switch_video_before_recording
                else "recording_stream_impact"
            ),
            "rtsp_url": args.rtsp_url,
            "frames": monitor.last_frame,
            "max_frame_gap_seconds": round(monitor.max_frame_gap, 3),
            "stall_threshold_seconds": args.stall_threshold,
            "unexpected_exit": monitor.unexpected_exit,
            "stream_affected": monitor.affected(),
            "retrieved_video_url": retrieved_video_url,
            "events": [event.__dict__ for event in monitor.events],
        }
        print("\n[summary]")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if monitor.affected():
            print("result: RTSP stream was interrupted or stalled during the recording test")
        else:
            print("result: RTSP stream stayed continuous during the recording test")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test whether direct ViewLink recording affects an RTSP stream")
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
    parser.add_argument("--resolution", default="1080p")
    parser.add_argument("--framerate", type=int, default=30)
    parser.add_argument("--encoding", default="h264")
    parser.add_argument("--record-duration", type=float, default=8.0)
    parser.add_argument("--media-url", default="", help="ViewLink media page, e.g. http://192.168.1.119:8554/download/")
    parser.add_argument(
        "--retrieve-recording",
        action="store_true",
        default=True,
        help="停止录像后调取最新录像文件，同时继续监测 RTSP 直播流。",
    )
    parser.add_argument(
        "--no-retrieve-recording",
        action="store_false",
        dest="retrieve_recording",
        help="只测试开始/停止录像，不调取录像文件。",
    )
    parser.add_argument(
        "--play-retrieved-video",
        action="store_true",
        default=True,
        help="使用 ffmpeg 读取最新录像文件到 null，用于模拟调取/回放录像。",
    )
    parser.add_argument(
        "--no-play-retrieved-video",
        action="store_false",
        dest="play_retrieved_video",
        help="只发现/下载录像文件，不用 ffmpeg 读取录像内容。",
    )
    parser.add_argument("--download-retrieved-video", action="store_true")
    parser.add_argument("--output-dir", default="logs/viewlink_media")
    parser.add_argument("--media-timeout", type=float, default=10.0)
    parser.add_argument("--media-poll-timeout", type=float, default=20.0)
    parser.add_argument("--media-poll-interval", type=float, default=1.0)
    parser.add_argument("--media-max-depth", type=int, default=4)
    parser.add_argument("--retrieve-duration", type=float, default=8.0)
    parser.add_argument("--retrieve-timeout", type=float, default=30.0)
    parser.add_argument(
        "--switch-video-before-recording",
        action="store_true",
        help="录像前先切到 video；默认不切模式，直接在 RTSP 拉流中开始录像。",
    )
    parser.add_argument(
        "--switch-video-after-recording",
        action="store_true",
        default=True,
        help="停止录像并观察后，再调用 camera_mode_switch 切到 video，验证录像后能否切回视频模式。",
    )
    parser.add_argument(
        "--no-switch-video-after-recording",
        action="store_false",
        dest="switch_video_after_recording",
        help="停止录像后不再切 video，只观察 RTSP 连续性。",
    )
    parser.add_argument("--mode-settle", type=float, default=0.5)
    parser.add_argument("--require-record-mode", action="store_true")
    parser.add_argument("--rtsp-url", default="rtsp://192.168.1.119:554/main")
    parser.add_argument("--rtsp-transport", choices=["tcp", "udp"], default="tcp")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--stream-startup-timeout", type=float, default=8.0)
    parser.add_argument("--stream-warmup", type=float, default=5.0)
    parser.add_argument("--stream-observe", type=float, default=8.0)
    parser.add_argument("--stream-observe-after-video", type=float, default=8.0)
    parser.add_argument("--stream-observe-after-retrieve", type=float, default=8.0)
    parser.add_argument("--stall-threshold", type=float, default=3.0)
    parser.add_argument("--stop-recording-on-error", action="store_true", default=True)
    parser.add_argument("--skip-bind-check", action="store_true")
    parser.add_argument("--skip-viewlink-connect", action="store_true")
    parser.add_argument("--allow-local-mode-only", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--verbose-ffmpeg", action="store_true")
    args = parser.parse_args()

    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.verify_record_mode = True
    args.skip_record_mode_verify = False

    for name in (
        "mode_settle",
        "record_duration",
        "media_poll_timeout",
        "media_poll_interval",
        "retrieve_duration",
        "retrieve_timeout",
        "stream_startup_timeout",
        "stream_warmup",
        "stream_observe",
        "stream_observe_after_video",
        "stream_observe_after_retrieve",
        "stall_threshold",
    ):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be non-negative")
    if args.framerate <= 0:
        parser.error("--framerate must be positive")

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_viewlink_stream_recording_impact",
    )
    if not client.connect(args.timeout):
        print(f"MQTT connect timeout: host={args.host} port={args.port}", file=sys.stderr)
        return 2

    try:
        rc = _check_bound(client, args.timeout, skip=args.skip_bind_check)
        if rc != 0:
            return rc
        rc = _ensure_viewlink_connected(client, args)
        if rc != 0:
            return rc
        return run_test(client, args)
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
