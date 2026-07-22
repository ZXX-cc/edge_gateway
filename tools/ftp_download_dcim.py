#!/usr/bin/env python3
"""Robust parallel FTP downloader for DJI/ViewLink DCIM folders."""

import argparse
import os
import posixpath
import queue
import sys
import threading
import time
from ftplib import FTP, error_perm


DEFAULT_HOST = "192.168.5.74"
DEFAULT_REMOTE_DIR = "/DCIM/00001"
DEFAULT_JOBS = 4


def fmt_sz(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_spd(n):
    return f"{fmt_sz(n)}/s"


def fmt_mbps(n):
    return f"{n * 8 / 1e6:.1f} Mbps"


def is_probably_file(name):
    return os.path.splitext(name)[1].lower() in {
        ".jpg", ".jpeg", ".png", ".dng", ".mp4", ".mov", ".avi", ".mkv",
    }


def ftp_connect(host, timeout):
    ftp = FTP(host, timeout=timeout)
    ftp.login()
    return ftp


def ftp_size(ftp, path):
    try:
        size = ftp.size(path)
        return int(size or 0)
    except Exception:
        return 0


def list_with_mlsd(ftp, remote_dir):
    files = []

    def walk(cur, rel):
        for name, facts in ftp.mlsd(cur):
            if name in (".", ".."):
                continue
            item_type = (facts.get("type") or "").lower()
            remote_path = posixpath.join(cur, name)
            local_rel = os.path.join(rel, name)
            if item_type == "dir":
                walk(remote_path, local_rel)
            elif item_type == "file":
                files.append((remote_path, local_rel, int(facts.get("size") or 0)))

    walk(remote_dir, ".")
    return files


def list_with_nlst(ftp, remote_dir):
    files = []

    def walk(cur, rel):
        try:
            names = ftp.nlst(cur)
        except error_perm:
            return
        for item in names:
            if item in (".", ".."):
                continue
            name = posixpath.basename(item.rstrip("/"))
            if name in (".", "..", ""):
                continue
            remote_path = item if item.startswith("/") else posixpath.join(cur, item)
            local_rel = os.path.join(rel, name)

            try:
                old = ftp.pwd()
                ftp.cwd(remote_path)
                ftp.cwd(old)
                walk(remote_path, local_rel)
                continue
            except Exception:
                pass

            size = ftp_size(ftp, remote_path)
            if size > 0 or is_probably_file(name):
                files.append((remote_path, local_rel, size))

    walk(remote_dir, ".")
    return files


def list_files(host, remote_dir, timeout):
    ftp = ftp_connect(host, timeout)
    try:
        try:
            files = list_with_mlsd(ftp, remote_dir)
            if files:
                return files
        except Exception as exc:
            print(f"MLSD unavailable, fallback to NLST: {exc}")
        return list_with_nlst(ftp, remote_dir)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


class Tracker:
    def __init__(self, total):
        self.lock = threading.RLock()
        self.bytes = 0
        self.done = 0
        self.total = total
        self.samples = []
        self.started_at = time.time()

    def add(self, n):
        now = time.time()
        with self.lock:
            self.bytes += n
            self.samples.append((now, n))
            self.samples = [(t, b) for t, b in self.samples if now - t <= 3]

    def finish(self):
        with self.lock:
            self.done += 1

    def speed(self):
        with self.lock:
            if len(self.samples) < 2:
                return 0
            dt = self.samples[-1][0] - self.samples[0][0]
            return sum(b for _, b in self.samples) / dt if dt > 0 else 0

    def snapshot(self):
        with self.lock:
            elapsed = max(0.001, time.time() - self.started_at)
            return self.bytes, self.done, self.total, self.speed(), self.bytes / elapsed


def download_one(host, remote_path, local_path, size, timeout, tracker, stop_flag):
    if size > 0 and os.path.exists(local_path) and os.path.getsize(local_path) == size:
        tracker.add(size)
        tracker.finish()
        return "skip", remote_path, size

    ftp = ftp_connect(host, timeout)
    try:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as handle:
            def callback(data):
                if stop_flag.is_set():
                    raise KeyboardInterrupt()
                handle.write(data)
                tracker.add(len(data))

            ftp.retrbinary(f"RETR {remote_path}", callback)
        tracker.finish()
        return "ok", remote_path, size
    except Exception as exc:
        tracker.finish()
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
        except Exception:
            pass
        return "fail", remote_path, str(exc)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


def worker(tasks, results, args, tracker, stop_flag):
    while not stop_flag.is_set():
        item = tasks.get()
        if item is None:
            return
        remote_path, local_path, size = item
        results.put(download_one(args.host, remote_path, local_path, size, args.timeout, tracker, stop_flag))


def main():
    parser = argparse.ArgumentParser(description="Download files from an FTP DCIM folder.")
    parser.add_argument("local_dir", nargs="?", default=os.getcwd())
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    parser.add_argument("-j", "--jobs", type=int, default=DEFAULT_JOBS)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    local_dir = os.path.abspath(args.local_dir)
    print(f"FTP: ftp://{args.host}{args.remote_dir}")
    print(f"To:  {local_dir}")
    print(f"Jobs: {args.jobs}")
    print("-" * 50)

    print("[1/2] Listing...", end="", flush=True)
    files = list_files(args.host, args.remote_dir, args.timeout)
    print(f"\r[1/2] {len(files)} files found")
    if not files:
        print("No files found. Check --remote-dir or FTP listing permissions.")
        return 1

    tasks = queue.Queue()
    pending = 0
    skipped = 0
    for remote_path, rel_path, size in files:
        local_path = os.path.join(local_dir, rel_path)
        if size > 0 and os.path.exists(local_path) and os.path.getsize(local_path) == size:
            skipped += 1
            continue
        pending += 1
        tasks.put((remote_path, local_path, size))
    for _ in range(args.jobs):
        tasks.put(None)

    total_bytes = sum(size for _, _, size in files)
    print(f"      {skipped} skipped, {pending} to download ({fmt_sz(total_bytes)})")
    if pending == 0:
        print("Done.")
        return 0

    tracker = Tracker(pending)
    results = queue.Queue()
    stop_flag = threading.Event()
    threads = [
        threading.Thread(target=worker, args=(tasks, results, args, tracker, stop_flag), daemon=True)
        for _ in range(args.jobs)
    ]
    for thread in threads:
        thread.start()

    print("[2/2] Downloading...")
    failures = 0
    completed = 0
    last_print = 0.0
    try:
        while completed < pending:
            now = time.time()
            if now - last_print >= 3:
                total, done, count, inst, avg = tracker.snapshot()
                pct = done / count * 100 if count else 100
                print(
                    f"  {pct:5.1f}% ({done}/{count}) | "
                    f"{fmt_spd(inst)} ({fmt_mbps(inst)}) | "
                    f"avg {fmt_spd(avg)} | {fmt_sz(total)}",
                    flush=True,
                )
                last_print = now

            try:
                status, name, detail = results.get(timeout=0.5)
            except queue.Empty:
                continue
            completed += 1
            if status == "fail":
                failures += 1
                print(f"  [FAIL] {name}: {detail}", file=sys.stderr)
    except KeyboardInterrupt:
        stop_flag.set()
        print("[STOPPED] Ctrl+C")
        return 130
    finally:
        stop_flag.set()
        for thread in threads:
            thread.join(timeout=2)

    total, done, count, inst, avg = tracker.snapshot()
    print("=" * 50)
    print(f"Status: {'OK' if failures == 0 else 'FAILED'}")
    print(f"Files:  {done}/{count}")
    print(f"Size:   {fmt_sz(total)}")
    print(f"Avg:    {fmt_spd(avg)} ({fmt_mbps(avg)})")
    if failures:
        print(f"Failures: {failures}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
