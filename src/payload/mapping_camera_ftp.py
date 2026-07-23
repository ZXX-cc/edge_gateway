"""正射相机 FTP 媒体下载工具。"""
from __future__ import annotations

import os
import posixpath
import queue
import threading
import urllib.parse
from dataclasses import dataclass
from ftplib import FTP
from pathlib import PurePosixPath
from typing import Callable, Iterable, Optional


@dataclass(frozen=True)
class MappingCameraFtpFile:
    remote_path: str
    relative_path: str
    size: int = 0

    @property
    def file_name(self) -> str:
        return PurePosixPath(self.relative_path).name


@dataclass(frozen=True)
class MappingCameraDownloadResult:
    file_name: str
    file_path: str
    remote_path: str
    size: int = 0
    skipped: bool = False


class MappingCameraFtpDownloader:
    """按四路 FTP 并发下载睿铂正射相机照片。"""

    def __init__(
        self,
        endpoint: str,
        *,
        jobs: int = 4,
        timeout: float = 30.0,
        ftp_factory: Optional[Callable[..., FTP]] = None,
    ):
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme.lower() != "ftp" or not parsed.hostname:
            raise ValueError(f"unsupported mapping camera FTP endpoint: {endpoint}")

        self.endpoint = endpoint
        self.host = parsed.hostname
        self.port = parsed.port or 21
        self.username = urllib.parse.unquote(parsed.username or "anonymous")
        self.password = urllib.parse.unquote(parsed.password or "")
        self.base_path = parsed.path or "/DCIM"
        self.jobs = max(1, int(jobs or 4))
        self.timeout = float(timeout or 30.0)
        self.ftp_factory = ftp_factory or FTP

    def download_photo(self, local_dir: str, remote_hint: str = "") -> Optional[MappingCameraDownloadResult]:
        """下载指定照片；没有指定照片时下载 FTP 目录中最新的一张。"""
        files = self.list_files()
        selected = self._select_file(files, remote_hint)
        if not selected:
            return None
        results = self.download_files([selected], local_dir)
        return results[0] if results else None

    def download_all(self, local_dir: str) -> list[MappingCameraDownloadResult]:
        """下载 FTP 目录下全部未缓存照片，使用多连接并发。"""
        return self.download_files(self.list_files(), local_dir)

    def list_files(self) -> list[MappingCameraFtpFile]:
        with self._connect() as ftp:
            return self._walk(ftp, self.base_path, ".")

    def download_files(
        self,
        files: Iterable[MappingCameraFtpFile],
        local_dir: str,
    ) -> list[MappingCameraDownloadResult]:
        os.makedirs(local_dir, exist_ok=True)
        pending = []
        results: list[MappingCameraDownloadResult] = []
        for item in files:
            local_path = os.path.join(local_dir, item.relative_path)
            if item.size > 0 and os.path.exists(local_path) and os.path.getsize(local_path) == item.size:
                results.append(self._result(item, local_path, skipped=True))
                continue
            pending.append((item, local_path))

        if not pending:
            return results

        task_queue: queue.Queue[tuple[MappingCameraFtpFile, str] | None] = queue.Queue()
        result_queue: queue.Queue[MappingCameraDownloadResult | Exception] = queue.Queue()
        for item in sorted(pending, key=lambda pair: pair[0].size, reverse=True):
            task_queue.put(item)
        for _ in range(min(self.jobs, len(pending))):
            task_queue.put(None)

        threads = []
        for _ in range(min(self.jobs, len(pending))):
            thread = threading.Thread(target=self._worker, args=(task_queue, result_queue), daemon=True)
            thread.start()
            threads.append(thread)

        first_error: Optional[Exception] = None
        for _ in pending:
            item = result_queue.get()
            if isinstance(item, Exception):
                first_error = first_error or item
            else:
                results.append(item)

        for thread in threads:
            thread.join(timeout=3)

        if first_error:
            raise first_error
        return results

    def _connect(self) -> FTP:
        ftp = self.ftp_factory(self.host, timeout=self.timeout)
        if self.port != 21 and hasattr(ftp, "connect"):
            ftp.connect(self.host, self.port, timeout=self.timeout)
        ftp.login(self.username, self.password)
        return ftp

    def _worker(
        self,
        task_queue: queue.Queue[tuple[MappingCameraFtpFile, str] | None],
        result_queue: queue.Queue[MappingCameraDownloadResult | Exception],
    ) -> None:
        while True:
            task = task_queue.get()
            if task is None:
                return
            item, local_path = task
            try:
                result_queue.put(self._download_one(item, local_path))
            except Exception as exc:
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except OSError:
                        pass
                result_queue.put(exc)

    def _download_one(self, item: MappingCameraFtpFile, local_path: str) -> MappingCameraDownloadResult:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with self._connect() as ftp:
            with open(local_path, "wb") as handle:
                ftp.retrbinary(f"RETR {item.remote_path}", handle.write)
        return self._result(item, local_path, skipped=False)

    def _walk(self, ftp: FTP, remote_dir: str, relative_dir: str) -> list[MappingCameraFtpFile]:
        try:
            names = ftp.nlst(remote_dir)
        except Exception:
            return []

        files: list[MappingCameraFtpFile] = []
        for raw_name in names:
            name = str(raw_name).rstrip("/")
            leaf = PurePosixPath(name).name
            if leaf in {"", ".", ".."}:
                continue
            remote_path = name if name.startswith("/") else posixpath.join(remote_dir.rstrip("/"), name)
            relative_path = leaf if relative_dir == "." else posixpath.join(relative_dir, leaf)
            if self._is_directory(ftp, remote_path):
                files.extend(self._walk(ftp, remote_path, relative_path))
            elif leaf.lower().endswith((".jpg", ".jpeg")):
                files.append(MappingCameraFtpFile(remote_path, relative_path, self._file_size(ftp, remote_path)))
        return files

    @staticmethod
    def _is_directory(ftp: FTP, remote_path: str) -> bool:
        current = ""
        try:
            current = ftp.pwd()
        except Exception:
            pass
        try:
            ftp.cwd(remote_path)
            return True
        except Exception:
            return False
        finally:
            if current:
                try:
                    ftp.cwd(current)
                except Exception:
                    pass

    @staticmethod
    def _file_size(ftp: FTP, remote_path: str) -> int:
        try:
            size = ftp.size(remote_path)
            return int(size or 0)
        except Exception:
            return 0

    @staticmethod
    def _select_file(
        files: list[MappingCameraFtpFile],
        remote_hint: str = "",
    ) -> Optional[MappingCameraFtpFile]:
        if not files:
            return None

        normalized_hint = str(remote_hint or "").replace("\\", "/").strip()
        hint_name = PurePosixPath(normalized_hint).name.lower()
        hint_suffix = normalized_hint.lstrip("/").lower()
        if hint_name:
            for item in files:
                remote = item.remote_path.replace("\\", "/").lstrip("/").lower()
                relative = item.relative_path.replace("\\", "/").lstrip("/").lower()
                if item.file_name.lower() == hint_name or remote.endswith(hint_suffix) or relative.endswith(hint_suffix):
                    return item

        return sorted(files, key=lambda item: item.remote_path)[-1]

    @staticmethod
    def _result(
        item: MappingCameraFtpFile,
        local_path: str,
        *,
        skipped: bool,
    ) -> MappingCameraDownloadResult:
        return MappingCameraDownloadResult(
            file_name=item.file_name,
            file_path=local_path,
            remote_path=item.remote_path,
            size=item.size,
            skipped=skipped,
        )
