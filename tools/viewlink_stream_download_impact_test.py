#!/usr/bin/env python3
"""Test whether downloading ViewLink media files affects an RTSP preview stream."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_viewlink_stream_mode_impact_test import RtspMonitor, _print_phase
from tool_config import add_config_argument, load_config
from viewlink_media_download import (
    DEFAULT_EXTENSIONS,
    discover_media_links,
    latest_media_link_by_http_metadata,
    safe_filename,
)


@dataclass
class DownloadResult:
    url: str
    target: str
    bytes: int
    duration_seconds: float
    ok: bool
    error: str = ""

    @property
    def mbps(self) -> float:
        if self.duration_seconds <= 0:
            return 0.0
        return (self.bytes * 8) / self.duration_seconds / 1_000_000


def _configured_media_url(config_path: str) -> str:
    config = load_config(config_path)
    remote_media = config.get("media", {}).get("remote_media", {}) if isinstance(config, dict) else {}
    if isinstance(remote_media, dict):
        return str(remote_media.get("base_url") or "")
    return ""


def _normalize_extensions(raw_extensions: list[str]) -> tuple[str, ...]:
    if not raw_extensions:
        return DEFAULT_EXTENSIONS
    return tuple(ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in raw_extensions)


def _resolve_download_urls(args) -> list[str]:
    urls = list(args.file_url)
    if urls:
        return urls

    media_url = args.media_url or _configured_media_url(args.config)
    if not media_url:
        raise RuntimeError("provide --media-url or at least one --file-url")

    print(f"discover media files from {media_url}")
    discovered = discover_media_links(
        media_url,
        args.media_timeout,
        _normalize_extensions(args.ext),
        max_depth=max(0, args.media_max_depth),
    )
    if not discovered:
        raise RuntimeError(f"no media files found from {media_url}")

    if args.latest_only:
        return [latest_media_link_by_http_metadata(discovered, args.media_timeout)]

    return discovered[: args.max_files] if args.max_files > 0 else discovered


def _download_once(url: str, output_dir: Path, timeout: float, chunk_size: int) -> DownloadResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    target = output_dir / safe_filename(url)
    if len(parsed.query) > 0:
        target = output_dir / f"{target.stem}_{abs(hash(url)) & 0xffff:x}{target.suffix}"

    started = time.time()
    total_bytes = 0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "edge-gateway-stream-download-impact/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            with target.open("wb") as handle:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    handle.write(chunk)
        return DownloadResult(
            url=url,
            target=str(target.resolve()),
            bytes=total_bytes,
            duration_seconds=time.time() - started,
            ok=True,
        )
    except Exception as exc:
        return DownloadResult(
            url=url,
            target=str(target.resolve()),
            bytes=total_bytes,
            duration_seconds=time.time() - started,
            ok=False,
            error=str(exc),
        )


def _download_many(args, urls: list[str]) -> list[DownloadResult]:
    expanded_urls = urls * max(1, args.repeat)
    output_dir = Path(args.output_dir)
    results: list[DownloadResult] = []
    workers = max(1, args.concurrency)

    print(f"download files while RTSP is running: count={len(expanded_urls)} concurrency={workers}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_download_once, url, output_dir, args.media_timeout, args.chunk_size)
            for url in expanded_urls
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            if result.ok:
                print(
                    "download ok "
                    f"bytes={result.bytes} duration={result.duration_seconds:.3f}s "
                    f"mbps={result.mbps:.2f} url={result.url}"
                )
            else:
                print(f"download failed bytes={result.bytes} url={result.url}: {result.error}", file=sys.stderr)
    return results


def _build_monitor(args) -> RtspMonitor:
    return RtspMonitor(
        ffmpeg=args.ffmpeg,
        rtsp_url=args.rtsp_url,
        rtsp_transport=args.rtsp_transport,
        startup_timeout=args.stream_startup_timeout,
        stall_threshold=args.stall_threshold,
        verbose=args.verbose_ffmpeg,
    )


def run_test(args, *, monitor: RtspMonitor | None = None, sleep_fn=time.sleep) -> int:
    try:
        urls = _resolve_download_urls(args)
    except Exception as exc:
        print(f"failed to prepare download urls: {exc}", file=sys.stderr)
        return 40

    print("download targets:")
    for url in urls:
        print(f"  {url}")

    monitor = monitor or _build_monitor(args)
    results: list[DownloadResult] = []
    if not monitor.start():
        monitor.stop()
        return 50

    try:
        print(f"warm up RTSP stream for {args.stream_warmup:.1f}s")
        sleep_fn(args.stream_warmup)
        _print_phase("before_download", monitor)

        results = _download_many(args, urls)
        _print_phase("after_download", monitor)

        print(f"observe RTSP stream after download for {args.stream_observe:.1f}s")
        sleep_fn(args.stream_observe)
        _print_phase("after_download_observe", monitor)

        failed_downloads = [result for result in results if not result.ok]
        if failed_downloads:
            return 41
        if args.fail_on_impact and monitor.affected():
            return 60
        return 0
    finally:
        monitor.stop()
        successful = [result for result in results if result.ok]
        total_bytes = sum(result.bytes for result in successful)
        total_duration = sum(result.duration_seconds for result in successful)
        average_mbps = 0.0
        if successful:
            average_mbps = sum(result.mbps for result in successful) / len(successful)

        summary = {
            "scenario": "stream_download_impact",
            "rtsp_url": args.rtsp_url,
            "download_count": len(results),
            "successful_download_count": len(successful),
            "total_download_bytes": total_bytes,
            "average_download_mbps": round(average_mbps, 3),
            "total_download_duration_seconds": round(total_duration, 3),
            "frames": monitor.last_frame,
            "max_frame_gap_seconds": round(monitor.max_frame_gap, 3),
            "stall_threshold_seconds": args.stall_threshold,
            "unexpected_exit": monitor.unexpected_exit,
            "stream_affected": monitor.affected(),
            "downloads": [result.__dict__ | {"mbps": round(result.mbps, 3)} for result in results],
            "events": [event.__dict__ for event in monitor.events],
        }
        print("\n[summary]")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if monitor.affected():
            print("result: RTSP stream was interrupted or stalled during file download")
        else:
            print("result: RTSP stream stayed continuous during file download")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test whether ViewLink file download affects an RTSP stream")
    add_config_argument(parser)
    parser.add_argument("--rtsp-url", default="rtsp://192.168.1.119:554/main")
    parser.add_argument("--rtsp-transport", choices=["tcp", "udp"], default="tcp")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--stream-startup-timeout", type=float, default=8.0)
    parser.add_argument("--stream-warmup", type=float, default=5.0)
    parser.add_argument("--stream-observe", type=float, default=8.0)
    parser.add_argument("--stall-threshold", type=float, default=3.0)
    parser.add_argument("--media-url", default="")
    parser.add_argument("--file-url", action="append", default=[], help="Direct file URL to download; can repeat")
    parser.add_argument("--latest-only", action="store_true", default=True)
    parser.add_argument("--all-files", action="store_false", dest="latest_only")
    parser.add_argument("--max-files", type=int, default=3, help="Used with --all-files; 0 means no limit")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--media-timeout", type=float, default=30.0)
    parser.add_argument("--media-max-depth", type=int, default=4)
    parser.add_argument("--chunk-size", type=int, default=1024 * 1024)
    parser.add_argument("--output-dir", default="logs/viewlink_stream_download")
    parser.add_argument("--ext", action="append", default=[], help="media extension, for example --ext .mp4")
    parser.add_argument("--fail-on-impact", action="store_true")
    parser.add_argument("--verbose-ffmpeg", action="store_true")
    args = parser.parse_args()

    for name in (
        "stream_startup_timeout",
        "stream_warmup",
        "stream_observe",
        "stall_threshold",
        "media_timeout",
    ):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be non-negative")
    if args.repeat <= 0:
        parser.error("--repeat must be positive")
    if args.concurrency <= 0:
        parser.error("--concurrency must be positive")
    if args.chunk_size <= 0:
        parser.error("--chunk-size must be positive")
    if args.max_files < 0:
        parser.error("--max-files must be non-negative")

    return run_test(args)


if __name__ == "__main__":
    raise SystemExit(main())
