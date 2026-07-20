#!/usr/bin/env python3
"""List or download media files from a ViewLink HTTP media page."""

from __future__ import annotations

import argparse
import html.parser
import os
import posixpath
import sys
import urllib.parse
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from tool_config import add_config_argument, load_config


DEFAULT_EXTENSIONS = (".jpg", ".jpeg", ".png", ".mp4", ".mov", ".avi")


class LinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.links.append(value)


def fetch_text(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "edge-gateway-viewlink-media/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def discover_media_links(
    url: str,
    timeout: float,
    extensions: tuple[str, ...],
    *,
    max_depth: int,
) -> list[str]:
    media_urls: list[str] = []
    seen_pages: set[str] = set()
    seen_media: set[str] = set()

    def visit(page_url: str, depth: int) -> None:
        normalized_page = page_url.split("#", 1)[0]
        if normalized_page in seen_pages or depth > max_depth:
            return
        seen_pages.add(normalized_page)

        html = fetch_text(page_url, timeout)
        parser = LinkParser()
        parser.feed(html)

        for href in parser.links:
            if href in {"../", ".."}:
                continue
            absolute = urllib.parse.urljoin(page_url, href)
            parsed = urllib.parse.urlparse(absolute)
            if parsed.scheme not in {"http", "https"}:
                continue
            path_lower = parsed.path.lower()
            if path_lower.endswith("/"):
                visit(absolute, depth + 1)
                continue
            if extensions and not path_lower.endswith(extensions):
                continue
            if absolute not in seen_media:
                media_urls.append(absolute)
                seen_media.add(absolute)

    visit(url, 0)
    return media_urls


def discover_page_links(url: str, timeout: float) -> list[str]:
    html = fetch_text(url, timeout)
    parser = LinkParser()
    parser.feed(html)

    urls: list[str] = []
    seen: set[str] = set()
    for href in parser.links:
        if href in {"../", ".."}:
            continue
        absolute = urllib.parse.urljoin(url, href)
        if absolute not in seen:
            urls.append(absolute)
            seen.add(absolute)
    return urls


def safe_filename(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = posixpath.basename(parsed.path.rstrip("/"))
    if not name:
        name = "downloaded_media"
    return name.replace(os.sep, "_")


def download_file(url: str, output_dir: Path, timeout: float) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / safe_filename(url)
    request = urllib.request.Request(url, headers={"User-Agent": "edge-gateway-viewlink-media/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="List or download ViewLink HTTP media files")
    add_config_argument(parser)
    parser.add_argument("--url", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output-dir", default="logs/viewlink_media")
    parser.add_argument("--download", action="store_true", help="download discovered media files")
    parser.add_argument("--list-all", action="store_true", help="list immediate links without filtering")
    parser.add_argument("--max-depth", type=int, default=4, help="directory recursion depth")
    parser.add_argument(
        "--ext",
        action="append",
        default=[],
        help="media extension to include, for example --ext .jpg --ext .mp4",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    remote_media = config.get("media", {}).get("remote_media", {}) if isinstance(config, dict) else {}
    configured_url = remote_media.get("base_url", "") if isinstance(remote_media, dict) else ""
    args.url = args.url or configured_url or "http://192.168.1.119:8554/download/"

    extensions = tuple(ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in args.ext)
    if not extensions:
        extensions = DEFAULT_EXTENSIONS

    try:
        if args.list_all:
            media_urls = discover_page_links(args.url, args.timeout)
        else:
            media_urls = discover_media_links(
                args.url,
                args.timeout,
                extensions,
                max_depth=max(0, args.max_depth),
            )
    except Exception as exc:
        print(f"failed to read media page: {exc}", file=sys.stderr)
        return 2

    if not media_urls:
        print("no media links found")
        return 0

    for media_url in media_urls:
        print(media_url)

    if args.download:
        output_dir = Path(args.output_dir)
        for media_url in media_urls:
            try:
                target = download_file(media_url, output_dir, args.timeout)
            except Exception as exc:
                print(f"download failed: {media_url}: {exc}", file=sys.stderr)
                return 3
            print(f"saved {target.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
