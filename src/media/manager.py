"""
媒体管理模块
处理媒体文件上传、直播控制、存储管理
"""
import time
import os
import glob
import hashlib
import hmac
import html.parser
import subprocess
import threading
import asyncio
import mimetypes
import urllib.parse
import requests
from pathlib import Path
from typing import Any, Dict, Optional, Callable, List
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.utils.dji_error_codes import (
    DEVICE_STATE_UNSUPPORTED,
    DOCK_SYSTEM_ERROR,
    INVALID_COMMAND_FORMAT,
)
from src.cloud_api.dock3_media import (
    build_media_file,
    build_remote_media_file,
    file_list_data,
    filter_media_files,
    media_task_output,
    normalize_delete_ids,
    normalize_delete_ids_with_codes,
    FILE_ID_ERROR_MISSING_IDS,
    normalize_page,
    normalize_priority_items,
    paginate,
    storage_config_output,
    upload_progress_event_data,
)
from src.cloud_api.dock3_live import (
    REALTIME_FFMPEG_URL_TYPES,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_OK,
    TASK_STATUS_REJECTED,
    is_mock_live_url,
    live_capability_data,
    live_status_event_data,
    live_task_output,
    validate_live_camera_payload,
    validate_live_lens_payload,
    validate_live_quality_payload,
    validate_live_start_payload,
)
from src.payload.dock3_payload import payload_index_from_config
from src.database.connection import get_async_session_local, init_database_async
from src.database.media_db import MediaDB

logger = get_logger("media")


class _DirectoryLinkParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: List[str] = []

    def handle_starttag(self, tag, attrs):
        if str(tag).lower() != "a":
            return
        for name, value in attrs:
            if str(name).lower() == "href" and value:
                self.links.append(str(value))


class MediaManager:
    """
    媒体管理器

    职责:
    1. 媒体文件管理(列表、删除)
    2. 上传控制(优先级调整、断点续传、云端凭证请求、HTTP上传)
    3. 直播控制(开始/停止/画质/镜头切换)
    4. 存储管理(容量监控)

    API方法:
    - adjust_upload_priority
    - storage_config_get: 设备主动 requests 获取存储配置
    - live_start, live_stop, set_livestream_lens, set_livestream_quality
    """

    # 视频质量映射 (ffmpeg 参数)
    QUALITY_PRESETS = {
        0: {'resolution': '1280x720', 'bitrate': '2000k', 'fps': 30},    # 自适应(默认720p)
        1: {'resolution': '640x480', 'bitrate': '800k', 'fps': 25},      # 流畅
        2: {'resolution': '1280x720', 'bitrate': '2000k', 'fps': 30},    # 标清
        3: {'resolution': '1920x1080', 'bitrate': '4000k', 'fps': 30},   # 高清
        4: {'resolution': '1920x1080', 'bitrate': '6000k', 'fps': 30},   # 超清
    }

    # 默认视频源
    DEFAULT_VIDEO_SOURCE = '/dev/video0'
    DEDICATED_LIVE_ADAPTER_URL_TYPES = {
        0: "Agora",
        3: "GB28181",
        4: "WebRTC",
    }

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()
        self.storage_path = self._resolve_storage_path(Config.get('media.storage_path', '/data/media'))

        # 上传队列
        self._upload_queue = []
        self._priority_overrides: Dict[str, int] = {}
        self._uploading = False
        self._upload_thread: Optional[threading.Thread] = None
        self._upload_lock = threading.Lock()

        # 直播状态
        self._live_streaming = False
        self._live_session_id = ""
        self._live_protocol = 4  # 默认 WebRTC
        self._live_quality = 0   # 默认自适应
        self._live_video_id = ""
        self._live_url = ""
        self._live_video_type = self._default_live_video_type()
        self._live_source_key = self._live_video_type
        self._live_camera_position = 0
        self._live_pipeline: List[Dict[str, Any]] = []

        # ffmpeg 推流进程
        self._ffmpeg_process: Optional[subprocess.Popen] = None
        self._ffmpeg_monitor_thread: Optional[threading.Thread] = None
        self._ffmpeg_process_lock = threading.RLock()

        # 存储状态
        self._total_capacity = Config.get('device.storage_capacity', 64000)  # MB
        self._used_capacity = 0

        # 回调
        self._event_callback: Optional[Callable] = None
        self._state_callback: Optional[Callable] = None
        self._request_sender: Optional[Callable] = None
        self._camera_controller = None
        self._payload_state_provider: Optional[Callable] = None

        # 数据库相关
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._db_initialized = False

        # 上传重试配置
        self._max_retry_count = 3

    @staticmethod
    def _default_live_video_type() -> str:
        """读取默认直播镜头类型。"""
        return str(Config.get('livestream.default_video_type', 'gimbal') or 'gimbal')

    def _reset_live_start_state(self):
        """清理一次未成功开始的直播会话状态。"""
        default_video_type = self._default_live_video_type()
        self._live_streaming = False
        self._live_session_id = ""
        self._live_protocol = 4
        self._live_quality = 0
        self._live_video_id = ""
        self._live_url = ""
        self._live_video_type = default_video_type
        self._live_source_key = default_video_type

    @staticmethod
    def _resolve_storage_path(configured_path: str) -> str:
        """Use configured media storage, falling back to a repo-local path when unwritable."""
        candidates = [Path(configured_path).expanduser()]
        project_root = Path(__file__).resolve().parents[2]
        fallback = project_root / "data" / "media"
        if not candidates[0].is_absolute():
            candidates[0] = project_root / candidates[0]
        if candidates[0] != fallback:
            candidates.append(fallback)

        for path in candidates:
            try:
                path.mkdir(parents=True, exist_ok=True)
                probe = path / ".write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                return str(path)
            except Exception as exc:
                logger.warning("Media storage path unavailable: path=%s, error=%s", path, exc)

        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback)

    def set_event_callback(self, callback: Callable):
        """设置事件上报回调"""
        self._event_callback = callback

    def set_state_callback(self, callback: Callable):
        """设置物模型 state 上报回调"""
        self._state_callback = callback

    def set_request_sender(self, sender: Callable):
        """设置请求发送器（用于 requests 通道）"""
        self._request_sender = sender

    def set_camera_controller(self, camera_controller):
        """注入相机控制器，用于直播切流后直接拍照。"""
        self._camera_controller = camera_controller

    def set_payload_state_provider(self, provider: Callable):
        """注入负载状态 provider，用于直播能力按实际连接状态过滤。"""
        self._payload_state_provider = provider

    def set_event_loop(self, loop):
        """注入异步事件循环引用"""
        self._main_loop = loop
        logger.info("MediaManager event loop set")

    def _ensure_event_loop(self):
        """确保事件循环已设置"""
        if self._main_loop is None:
            try:
                self._main_loop = asyncio.get_running_loop()
                logger.info("MediaManager captured main event loop")
            except RuntimeError:
                logger.warning("No running event loop detected")

    async def _init_database(self):
        """初始化数据库"""
        if self._db_initialized:
            return
        await init_database_async()
        self._db_initialized = True
        logger.info("MediaManager database initialized")

    def _run_db_coroutine(self, coro):
        """在主事件循环中运行数据库协程（非阻塞版）"""
        self._ensure_event_loop()
        if self._main_loop:
            try:
                asyncio.run_coroutine_threadsafe(coro, self._main_loop)
            except Exception as e:
                logger.error(f"Failed to schedule DB coroutine: {e}")
        else:
            logger.warning("Cannot schedule DB coroutine: no event loop available")
            if hasattr(coro, "close"):
                coro.close()

    # ---- 媒体文件管理 ----

    def handle_adjust_upload_priority(self, msg: dict) -> dict:
        """
        调整上传优先级

        Request: { files: [{file_id, priority}, ...] }
        """
        data = msg.get('data', {})
        files, errors = normalize_priority_items(data)
        if errors:
            logger.warning("Rejected adjust_upload_priority payload: %s", "; ".join(errors))
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=media_task_output("rejected", {"errors": errors}),
                msg_str="; ".join(errors),
            )

        logger.info(f"Adjust upload priority: {len(files)} files")
        for item in files:
            self._priority_overrides[item["file_id"]] = item["priority"]

        known = self._scan_local_files()
        by_id = self._media_identity_map(known)
        touched = [by_id[file["file_id"]] for file in files if file["file_id"] in by_id]
        if touched and self._event_callback:
            self._event_callback(MessageBuilder.build_event(
                "fileupload_progress",
                upload_progress_event_data(touched, status="in_progress", progress=0),
                self.gateway_sn,
            ))

        return MessageBuilder.build_reply(msg, result=0, data=media_task_output("ok", {
            "files": files,
        }))

    def request_storage_config_get(self, module: int = 0,
                                    callback: Callable = None) -> str:
        """
        主动获取存储配置/OSS临时凭证 (storage_config_get)
        Topic: requests / requests_reply

        Args:
            module: 模块枚举值，0=媒体
            callback: 响应回调
        """
        if not self._request_sender:
            logger.warning("Request sender not set, cannot request storage config")
            return ""
        return self._request_sender(
            'storage_config_get',
            {'module': module},
            callback
        )

    def handle_storage_config_get(self, msg: dict) -> dict:
        """
        storage_config_get 是 Dock3 requests/requests_reply 链路，不暴露 services 入口。
        """
        return MessageBuilder.build_reply(
            msg,
            result=DEVICE_STATE_UNSUPPORTED,
            msg_str="storage_config_get must use requests/requests_reply",
        )

    def report_upload_complete(self, file_info: dict, flight_task: dict = None):
        """
        上报文件上传完成 (file_upload_callback)

        Args:
            file_info: 文件信息
                {object_key, path, name, ext: {flight_id, drone_model_key, payload_model_key, is_original},
                 metadata: {gimbal_yaw_degree, absolute_altitude, relative_altitude, create_time, shoot_position: {lat, lng}},
                 cloud_to_cloud_id}
            flight_task: 飞行任务信息
                {uploaded_file_count, expected_file_count, flight_type}
        """
        if self._event_callback:
            object_key, file_path = self._normalize_upload_callback_object_key(file_info)
            ext = {
                'flight_id': '',
                'drone_model_key': '',
                'payload_model_key': '',
                'is_original': None,
                **(file_info.get('ext') or {}),
            }
            metadata = {
                'gimbal_yaw_degree': None,
                'absolute_altitude': None,
                'relative_altitude': None,
                'create_time': '',
                'shoot_position': {'lat': None, 'lng': None},
                **(file_info.get('metadata') or {}),
            }
            shoot_position = metadata.get('shoot_position') if isinstance(metadata.get('shoot_position'), dict) else {}
            metadata['shoot_position'] = {'lat': None, 'lng': None, **shoot_position}
            payload = {
                'file': {
                    'object_key': object_key,
                    'path': file_path,
                    'name': file_info.get('name', ''),
                    'ext': ext,
                    'metadata': metadata,
                    'cloud_to_cloud_id': file_info.get('cloud_to_cloud_id', 'DEFAULT')
                }
            }
            if flight_task:
                payload['flight_task'] = flight_task

            event = MessageBuilder.build_event(
                'file_upload_callback',
                payload,
                self.gateway_sn
            )
            self._event_callback(event)

    @staticmethod
    def _normalize_upload_callback_object_key(file_info: dict) -> tuple[str, str]:
        """规范化上传完成事件的对象键，避免把完整 upload_url 当 object_key。"""
        raw_object_key = str(file_info.get('object_key') or file_info.get('key') or '').strip()
        file_path = str(file_info.get('path') or file_info.get('upload_url') or '').strip()

        if '://' not in raw_object_key:
            return raw_object_key, file_path

        if not file_path:
            file_path = raw_object_key
        parsed = urllib.parse.urlparse(raw_object_key)
        object_key = urllib.parse.unquote(parsed.path or '').lstrip('/')
        return object_key, file_path

    def report_priority_media(self, flight_id: str):
        """
        上报最高优先级任务媒体 (highest_priority_upload_flighttask_media)

        Args:
            flight_id: 当前优先级最高的任务 ID
        """
        if self._event_callback:
            event = MessageBuilder.build_event(
                'highest_priority_upload_flighttask_media',
                {'flight_id': flight_id},
                self.gateway_sn
            )
            self._event_callback(event)

    # ---- 文件上传管理 ----

    def _scan_local_files(self) -> List[dict]:
        """扫描本地媒体文件"""
        files = []
        try:
            if not os.path.exists(self.storage_path):
                os.makedirs(self.storage_path, exist_ok=True)
                return files

            # 扫描常见媒体文件格式；正射相机 FTP 会保留 DCIM 下的架次子目录。
            extensions = ['*.jpg', '*.jpeg', '*.png', '*.dng', '*.mp4', '*.mov', '*.avi', '*.mkv', '*.zip']
            for ext in extensions:
                pattern = os.path.join(self.storage_path, "**", ext)
                for filepath in glob.glob(pattern, recursive=True):
                    if os.path.isdir(filepath) or f"{os.sep}.remote_cache{os.sep}" in filepath:
                        continue
                    item = build_media_file(filepath, self.storage_path, self.gateway_sn)
                    item['file_path'] = filepath
                    item['priority'] = self._priority_overrides.get(item['file_id'], 0)
                    files.append(item)
        except Exception as e:
            logger.error(f"Scan local files failed: {e}")

        return files

    def _scan_remote_media_files(self) -> List[dict]:
        """Scan configured HTTP media directory indexes from direct payloads."""
        remote_cfg = Config.get('media.remote_media', {}) or {}
        if not isinstance(remote_cfg, dict) or not remote_cfg.get('enabled', False):
            return []

        base_urls = remote_cfg.get('base_urls', None)
        if base_urls is None:
            base_url = remote_cfg.get('base_url', '')
            base_urls = [base_url] if base_url else []
        if isinstance(base_urls, str):
            base_urls = [base_urls]
        if not isinstance(base_urls, list):
            return []

        timeout = float(remote_cfg.get('timeout_seconds', 5.0) or 5.0)
        max_depth = max(0, int(remote_cfg.get('max_depth', 4) or 4))
        extensions = tuple(
            str(ext).lower() if str(ext).startswith('.') else f".{str(ext).lower()}"
            for ext in (remote_cfg.get('extensions') or ['jpg', 'jpeg', 'png', 'dng', 'mp4', 'mov', 'avi', 'mkv'])
        )

        media_urls: List[str] = []
        seen_pages = set()
        seen_media = set()

        def visit(page_url: str, depth: int):
            normalized_page = page_url.split('#', 1)[0]
            if normalized_page in seen_pages or depth > max_depth:
                return
            seen_pages.add(normalized_page)
            try:
                response = requests.get(page_url, timeout=timeout)
                response.raise_for_status()
            except Exception as exc:
                logger.warning("Scan remote media page failed: url=%s error=%s", page_url, exc)
                return

            parser = _DirectoryLinkParser()
            parser.feed(response.text)
            for href in parser.links:
                if href in {'../', '..'}:
                    continue
                absolute = urllib.parse.urljoin(page_url, href)
                parsed = urllib.parse.urlparse(absolute)
                if parsed.scheme not in {'http', 'https'}:
                    continue
                path = parsed.path.lower()
                if path.endswith('/'):
                    visit(absolute, depth + 1)
                    continue
                if extensions and not path.endswith(extensions):
                    continue
                if absolute not in seen_media:
                    media_urls.append(absolute)
                    seen_media.add(absolute)

        for url in base_urls:
            if url:
                visit(str(url), 0)

        return [build_remote_media_file(url, self.gateway_sn) for url in media_urls]

    def _sync_direct_payload_media(self) -> List[dict]:
        """同步网关直连负载媒体到本地目录。

        例如睿铂正射/倾斜相机的照片经 FTP 暴露在相机端 /DCIM 目录；
        DJI 的 file_list_get/file_download 仍走统一媒体域，所以列文件前先
        让对应负载 adapter 把真实文件拉到 media.storage_path。
        """
        if not self._camera_controller or not hasattr(self._camera_controller, "sync_mapping_camera_media"):
            return []
        try:
            return self._camera_controller.sync_mapping_camera_media(download_all=True)
        except Exception as exc:
            logger.warning("Direct payload media sync failed: %s", exc)
            return []

    def _scan_media_files(self) -> List[dict]:
        self._sync_direct_payload_media()
        return self._scan_local_files() + self._scan_remote_media_files()

    async def _scan_media_files_async(self) -> List[dict]:
        """在线程中扫描媒体文件，避免远端 HTTP 目录扫描阻塞主事件循环。"""
        return await asyncio.to_thread(self._scan_media_files)

    def _remote_media_cache_path(self, url: str) -> str:
        """将云台等远端媒体 URL 映射到稳定的本地缓存路径。"""
        parsed = urllib.parse.urlparse(url)
        filename = urllib.parse.unquote(Path(parsed.path).name) or "remote_media"
        digest = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:16]
        cache_dir = Path(self.storage_path) / ".remote_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return str(cache_dir / f"{digest}_{filename}")

    def _download_remote_media(self, url: str) -> str:
        """先下载负载端媒体文件，再复用现有上传流水线。"""
        target = self._remote_media_cache_path(url)
        tmp_target = f"{target}.part"
        timeout = float(Config.get('media.remote_media.timeout_seconds', 10.0) or 10.0)
        try:
            with requests.get(url, stream=True, timeout=timeout) as response:
                response.raise_for_status()
                with open(tmp_target, 'wb') as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            os.replace(tmp_target, target)
            return target
        except Exception:
            try:
                if os.path.exists(tmp_target):
                    os.remove(tmp_target)
            finally:
                raise

    def _download_media_item(self, item: dict) -> dict:
        """下载单个媒体条目；本地文件直接返回路径，远端文件缓存到本地。"""
        if item.get("file_path") and os.path.exists(item["file_path"]):
            filepath = item["file_path"]
        elif item.get("download_url"):
            filepath = self._download_remote_media(str(item["download_url"]))
        else:
            raise ValueError("media item has no local path or download_url")

        return {
            "file_id": item.get("file_id", ""),
            "fingerprint": item.get("fingerprint", item.get("file_id", "")),
            "name": item.get("name", item.get("file_name", "")),
            "file_name": item.get("file_name", item.get("name", "")),
            "media_type": item.get("media_type", ""),
            "source": item.get("source", "local"),
            "download_url": item.get("download_url", ""),
            "cache_path": filepath,
            "file_path": filepath,
            "size": os.path.getsize(filepath) if os.path.exists(filepath) else 0,
        }

    def _resolve_media_items_for_download(self, data: dict) -> tuple[List[dict], List[str]]:
        known = self._scan_media_files()
        by_id = self._media_identity_map(known)
        requested, validation_errors = normalize_delete_ids_with_codes(data)
        urls = []
        raw_urls = data.get("download_urls", data.get("download_url", [])) if isinstance(data, dict) else []
        if isinstance(raw_urls, list):
            urls = [str(url) for url in raw_urls if url]
        elif raw_urls:
            urls = [str(raw_urls)]
        requested.extend(urls)
        requested = list(dict.fromkeys(requested))
        errors = [
            item["message"]
            for item in validation_errors
            if not (urls and item.get("code") == FILE_ID_ERROR_MISSING_IDS)
        ]
        if not requested:
            return [], errors or ["file_download missing file_ids/files/download_url"]

        items: List[dict] = []
        missing: List[str] = []
        for identity in requested:
            item = by_id.get(identity)
            if not item and str(identity).startswith(("http://", "https://")):
                item = build_remote_media_file(str(identity), self.gateway_sn)
            if item:
                items.append(item)
            else:
                missing.append(identity)
        if missing:
            errors.append(f"file not found: {', '.join(missing)}")
        return items, errors

    @staticmethod
    def _media_identity_map(files: List[dict]) -> Dict[str, dict]:
        mapping: Dict[str, dict] = {}
        for item in files:
            identities = {
                item.get("file_id"),
                item.get("fingerprint"),
                item.get("name"),
                item.get("file_name"),
                item.get("object_key"),
                item.get("path"),
            }
            for identity in identities:
                if identity:
                    mapping[str(identity)] = item
        return mapping

    @staticmethod
    def _db_media_to_upload_item(media_file) -> dict:
        filepath = getattr(media_file, "file_path", "") or ""
        file_name = getattr(media_file, "file_name", "") or os.path.basename(filepath)
        db_id = str(getattr(media_file, "id", "") or "")
        stable_id = db_id or file_name
        if filepath and os.path.exists(filepath):
            try:
                item = build_media_file(filepath, os.path.dirname(filepath) or ".", getattr(media_file, "device_sn", ""))
            except Exception:
                item = {}
        else:
            item = {}
        db_file_size = int(getattr(media_file, "file_size", 0) or 0)
        resolved_size = db_file_size or int(item.get("file_size", item.get("size", 0)) or 0)
        item.update({
            "db_id": db_id,
            "id": db_id,
            "file_id": item.get("file_id") or stable_id,
            "fingerprint": item.get("fingerprint") or stable_id,
            "device_sn": getattr(media_file, "device_sn", ""),
            "file_name": file_name,
            "name": file_name,
            "file_path": filepath,
            "file_size": resolved_size,
            "size": resolved_size,
            "file_type": getattr(media_file, "file_type", "") or Path(file_name).suffix.lower().lstrip("."),
            "mission_id": getattr(media_file, "mission_id", "") or "",
            "flight_id": getattr(media_file, "mission_id", "") or "",
            "upload_status": getattr(media_file, "upload_status", "pending") or "pending",
        })
        return item

    def _apply_upload_priority(self, item: dict) -> dict:
        identities = [
            item.get("file_id"),
            item.get("fingerprint"),
            item.get("name"),
            item.get("file_name"),
            item.get("object_key"),
            item.get("path"),
        ]
        priority = 0
        for identity in identities:
            if identity and str(identity) in self._priority_overrides:
                priority = max(priority, int(self._priority_overrides[str(identity)]))
        item["priority"] = priority
        return item

    @staticmethod
    def _normalize_upload_target_names(data: dict) -> set[str]:
        """Extract optional target file identities from an upload trigger payload."""
        if not isinstance(data, dict):
            return set()
        targets: set[str] = set()
        raw_files = data.get("files", [])
        if isinstance(raw_files, list):
            for item in raw_files:
                if isinstance(item, str):
                    value = item
                    if value:
                        targets.add(value)
                        targets.add(os.path.basename(value))
                    continue
                if not isinstance(item, dict):
                    continue
                for key in ("file_id", "fingerprint", "name", "file_name", "object_key", "path"):
                    value = str(item.get(key) or "")
                    if value:
                        targets.add(value)
                        targets.add(os.path.basename(value))
        for key in ("file_id", "fingerprint", "name", "file_name", "object_key", "path"):
            value = str(data.get(key) or "")
            if value:
                targets.add(value)
                targets.add(os.path.basename(value))
        return {target for target in targets if target}

    @staticmethod
    def _upload_item_matches_targets(item: dict, targets: set[str]) -> bool:
        if not targets:
            return True
        identities = [
            item.get("file_id"),
            item.get("fingerprint"),
            item.get("name"),
            item.get("file_name"),
            item.get("object_key"),
            item.get("path"),
            item.get("file_path"),
            item.get("download_url"),
        ]
        for identity in identities:
            value = str(identity or "")
            if not value:
                continue
            if value in targets or os.path.basename(value) in targets:
                return True
        return False

    async def _get_pending_files_for_upload(self, flight_id: str = "", target_names: set[str] | None = None) -> List[dict]:
        """获取待上传的文件列表（与数据库比对）"""
        await self._init_database()
        AsyncSessionLocal = get_async_session_local()
        target_names = set(target_names or set())

        async with AsyncSessionLocal() as session:
            db_files = await MediaDB.get_pending_uploads(session, self.gateway_sn)
            flight_id = str(flight_id or "")
            if flight_id:
                db_files = [
                    item for item in db_files
                    if str(getattr(item, "mission_id", "") or "") == flight_id
                ]
            pending_files = []
            for item in db_files:
                upload_item = self._apply_upload_priority(self._db_media_to_upload_item(item))
                if self._upload_item_matches_targets(upload_item, target_names):
                    pending_files.append(upload_item)

            if not flight_id or target_names:
                # 扫描本地/远端媒体可能访问文件系统或 HTTP 目录，不能阻塞主事件循环。
                local_files = await self._scan_media_files_async()
                db_paths = {str(getattr(f, "file_path", "") or "") for f in db_files}
                db_file_names = {str(getattr(f, "file_name", "") or "") for f in db_files}
                for local_file in local_files:
                    file_path = str(local_file.get("file_path", "") or "")
                    file_name = str(local_file.get("file_name", "") or "")
                    if file_path in db_paths or file_name in db_file_names:
                        continue
                    if not self._upload_item_matches_targets(local_file, target_names):
                        continue
                    pending_files.append(self._apply_upload_priority(local_file))

            return self._sort_upload_files(pending_files)

    @staticmethod
    def _sort_upload_files(files: List[dict]) -> List[dict]:
        return sorted(
            files,
            key=lambda item: (
                -int(item.get("priority", 0) or 0),
                int(item.get("create_time", item.get("capture_time", 0)) or 0),
                str(item.get("file_name", "")),
            ),
        )

    @staticmethod
    def _upload_item_identity(file_info: dict) -> str:
        """生成上传队列去重键，优先使用数据库主键。"""
        if not isinstance(file_info, dict):
            return ""
        for key in (
            "db_id",
            "id",
            "media_id",
            "file_id",
            "fingerprint",
            "file_path",
            "download_url",
            "file_name",
            "name",
        ):
            value = file_info.get(key)
            if value:
                return f"{key}:{value}"
        return ""

    def _enqueue_upload_files_locked(self, files: List[dict]) -> int:
        """在持有 _upload_lock 时入列，并按媒体身份去重。"""
        existing = {
            identity
            for identity in (self._upload_item_identity(item) for item in self._upload_queue)
            if identity
        }
        added = 0
        for item in files:
            identity = self._upload_item_identity(item)
            if identity and identity in existing:
                continue
            self._upload_queue.append(item)
            if identity:
                existing.add(identity)
            added += 1
        self._upload_queue = self._sort_upload_files(self._upload_queue)
        return added

    def _pop_next_upload_file(self) -> Optional[dict]:
        if not self._upload_queue:
            return None
        self._upload_queue = self._sort_upload_files(self._upload_queue)
        return self._upload_queue.pop(0)

    def _request_upload_credentials(self, files: List[dict]) -> Optional[dict]:
        """向云端请求上传凭证"""
        if not self._request_sender:
            logger.warning("Request sender not set, cannot request upload credentials")
            return None

        # 构建请求参数
        request_data = {
            'module': 0,
            'files': [{'name': f['file_name'], 'size': f['file_size']} for f in files]
        }

        result_holder = {"received": False, "payload": {}}
        done = threading.Event()

        def callback(*args):
            result_holder["received"] = True
            result_holder["payload"] = args[-1] if args else {}
            done.set()

        request_id = self._request_sender('storage_config_get', request_data, callback)
        if not request_id:
            logger.warning("storage_config_get request was not sent")
            return None

        if not done.wait(timeout=10.0):
            logger.warning("Timeout waiting storage_config_get reply")
            return None

        result = result_holder["payload"]
        return result.get('data') if isinstance(result, dict) else None

    def _upload_file_http(self, filepath: str, upload_url: str) -> bool:
        """通过HTTP上传文件到对象存储"""
        try:
            with open(filepath, 'rb') as f:
                response = requests.put(upload_url, data=f, timeout=300)
                return response.status_code == 200
        except Exception as e:
            logger.error(f"HTTP upload failed: {e}")
            return False

    @staticmethod
    def _storage_config_from_credentials(credentials: dict) -> dict:
        """Normalize storage_config_get data/output shapes into one config dict."""
        if not isinstance(credentials, dict):
            return {}
        output = credentials.get("output") if isinstance(credentials.get("output"), dict) else {}
        merged = dict(output)
        for key, value in credentials.items():
            if key in {"result", "output"}:
                continue
            if key not in merged or merged.get(key) in (None, ""):
                merged[key] = value
        return merged

    @staticmethod
    def _object_key_from_storage(storage: dict, file_name: str) -> str:
        object_key = str(storage.get("object_key") or "").strip("/")
        if object_key:
            return object_key
        prefix = str(storage.get("object_key_prefix") or "").strip("/")
        safe_name = os.path.basename(str(file_name or "media_file"))
        return f"{prefix}/{safe_name}" if prefix else safe_name

    def _object_key_from_upload_target(self, storage: dict, upload_url: str, file_name: str) -> str:
        object_key = self._object_key_from_storage(storage, file_name)
        if object_key and object_key != os.path.basename(str(file_name or "")):
            return object_key
        parsed = urllib.parse.urlparse(str(upload_url or ""))
        path_key = urllib.parse.unquote(parsed.path or "").lstrip("/")
        return path_key or object_key or os.path.basename(str(file_name or "media_file"))

    @staticmethod
    def _object_key_from_upload_url(upload_url: str) -> str:
        """从预签名上传 URL 中提取 path 作为兜底对象键，不返回完整 URL。"""
        parsed = urllib.parse.urlparse(str(upload_url or ""))
        return urllib.parse.unquote(parsed.path or "").lstrip("/")

    @staticmethod
    def _quote_s3_path(path: str) -> str:
        return "/" + "/".join(
            urllib.parse.quote(part, safe="")
            for part in str(path or "").strip("/").split("/")
            if part != ""
        )

    @staticmethod
    def _s3_signing_key(secret: str, date_stamp: str, region: str) -> bytes:
        key = ("AWS4" + secret).encode("utf-8")
        for value in (date_stamp, region, "s3", "aws4_request"):
            key = hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()
        return key

    def _upload_file_s3(self, filepath: str, storage: dict, object_key: str = "") -> tuple[bool, str, str]:
        """Upload a file with S3/MinIO path-style SigV4 PUT."""
        try:
            endpoint = str(storage.get("endpoint") or "").rstrip("/")
            bucket = str(storage.get("bucket") or "").strip("/")
            region = str(storage.get("region") or "us-east-1") or "us-east-1"
            credential_node = storage.get("credentials") if isinstance(storage.get("credentials"), dict) else {}
            access_key = str(credential_node.get("access_key_id") or "")
            secret_key = str(credential_node.get("access_key_secret") or "")
            token = str(credential_node.get("security_token") or "")
            if not endpoint or not bucket or not access_key or not secret_key:
                logger.warning(
                    "S3 upload config incomplete: endpoint=%s bucket=%s access_key=%s secret=%s",
                    bool(endpoint),
                    bool(bucket),
                    bool(access_key),
                    bool(secret_key),
                )
                return False, "", object_key

            parsed = urllib.parse.urlparse(endpoint)
            if not parsed.scheme or not parsed.netloc:
                logger.warning("Invalid S3 endpoint: %s", endpoint)
                return False, "", object_key

            file_name = os.path.basename(filepath)
            resolved_key = object_key or self._object_key_from_storage(storage, file_name)
            object_path = f"{bucket}/{resolved_key.strip('/')}"
            canonical_uri = self._quote_s3_path(object_path)
            url = f"{parsed.scheme}://{parsed.netloc}{canonical_uri}"

            # Read bytes once so the payload hash exactly matches the PUT body.
            with open(filepath, "rb") as handle:
                body = handle.read()

            payload_hash = hashlib.sha256(body).hexdigest()
            now = time.gmtime()
            amz_date = time.strftime("%Y%m%dT%H%M%SZ", now)
            date_stamp = time.strftime("%Y%m%d", now)
            content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            headers = {
                "host": parsed.netloc,
                "x-amz-content-sha256": payload_hash,
                "x-amz-date": amz_date,
                "content-type": content_type,
            }
            if token:
                headers["x-amz-security-token"] = token

            signed_header_names = sorted(headers)
            canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in signed_header_names)
            signed_headers = ";".join(signed_header_names)
            canonical_request = "\n".join([
                "PUT",
                canonical_uri,
                "",
                canonical_headers,
                signed_headers,
                payload_hash,
            ])
            scope = f"{date_stamp}/{region}/s3/aws4_request"
            string_to_sign = "\n".join([
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ])
            signature = hmac.new(
                self._s3_signing_key(secret_key, date_stamp, region),
                string_to_sign.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            headers["Authorization"] = (
                "AWS4-HMAC-SHA256 "
                f"Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
            )

            response = requests.put(url, data=body, headers=headers, timeout=300)
            if response.status_code not in {200, 201, 204}:
                logger.error("S3 upload failed: status=%s body=%s", response.status_code, response.text[:500])
                return False, url, resolved_key
            return True, url, resolved_key
        except Exception as e:
            logger.error(f"S3 upload failed: {e}")
            return False, "", object_key

    @staticmethod
    def _credential_expired(credentials: dict) -> bool:
        """校验 OSS 临时凭证是否已过期；小数值按相对 TTL 秒处理。"""
        if not isinstance(credentials, dict):
            return False

        candidates = []
        output = credentials.get("output") if isinstance(credentials.get("output"), dict) else {}
        containers = [
            credentials,
            credentials.get("credentials") if isinstance(credentials.get("credentials"), dict) else {},
            output,
            output.get("credentials") if isinstance(output.get("credentials"), dict) else {},
        ]
        for container in containers:
            if isinstance(container, dict):
                for key in ("expire", "expire_time", "expired_at", "expiration"):
                    if key in container:
                        candidates.append(container.get(key))

        now_seconds = int(time.time())
        now_ms = int(time.time() * 1000)
        for value in candidates:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            expire = int(value)
            if expire <= 0:
                return True
            if expire >= 1_000_000_000_000 and expire <= now_ms:
                return True
            if 1_000_000_000 <= expire < 1_000_000_000_000 and expire <= now_seconds:
                return True
        return False

    @staticmethod
    def _upload_db_id(file_info: dict) -> str:
        """解析媒体数据库主键，只能用它更新 media_files.upload_status。"""
        if not isinstance(file_info, dict):
            return ""
        for key in ("db_id", "id", "media_id"):
            value = file_info.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _storage_target_available(storage_config: dict, upload_url: str = "") -> bool:
        """判断平台是否下发了可用上传目标。"""
        if upload_url:
            return True
        if not isinstance(storage_config, dict):
            return False
        credentials = storage_config.get("credentials") if isinstance(storage_config.get("credentials"), dict) else {}
        return all([
            storage_config.get("endpoint"),
            storage_config.get("bucket"),
            credentials.get("access_key_id"),
            credentials.get("access_key_secret"),
        ])

    def _defer_upload_file(self, file_info: dict, reason: str):
        """临时条件不满足时把文件放回队列，保持 DB pending，等待下一次触发。"""
        file_info["_defer_count"] = int(file_info.get("_defer_count", 0) or 0) + 1
        file_info["_defer_reason"] = reason
        file_name = file_info.get("file_name") or file_info.get("name") or ""
        with self._upload_lock:
            added = self._enqueue_upload_files_locked([file_info])
            self._uploading = False
        logger.warning(
            "Upload deferred and kept pending: name=%s reason=%s defer_count=%s requeued=%s",
            file_name,
            reason,
            file_info["_defer_count"],
            bool(added),
        )

    def _update_upload_progress(
        self,
        file_id: str,
        progress: int,
        status: str,
        *,
        db_id: str = "",
        upload_url: str = "",
        file_item: Optional[dict] = None,
    ):
        """更新上传进度"""
        target_db_id = str(db_id or self._upload_db_id(file_item or {}) or "").strip()

        async def _update_status():
            if not target_db_id:
                return
            await self._init_database()
            AsyncSessionLocal = get_async_session_local()
            if not AsyncSessionLocal:
                return
            async with AsyncSessionLocal() as session:
                updated = await MediaDB.update_upload_status(session, target_db_id, status, upload_url=upload_url)
                if not updated:
                    logger.warning("Media upload status not updated: db_id=%s status=%s", target_db_id, status)
                await session.commit()

        if target_db_id:
            self._run_db_coroutine(_update_status())
        elif status in {"completed", "failed"}:
            logger.warning(
                "Media upload DB status skipped: missing db_id file_id=%s file_name=%s status=%s",
                file_id,
                (file_item or {}).get("file_name") or (file_item or {}).get("name") or "",
                status,
            )

        # 上报进度事件
        if self._event_callback:
            event_item = file_item or self._media_identity_map(self._scan_local_files()).get(file_id, {
                "file_id": file_id,
                "fingerprint": file_id,
                "device_sn": self.gateway_sn,
                "object_key": file_id,
                "size": 0,
            })
            event_files = [event_item]
            event = MessageBuilder.build_event(
                'fileupload_progress',
                upload_progress_event_data(event_files, status=status, progress=progress),
                self.gateway_sn
            )
            self._event_callback(event)

    def _upload_files(self):
        """上传文件主循环"""
        while True:
            with self._upload_lock:
                if not self._upload_queue:
                    self._uploading = False
                    break

                file_info = self._pop_next_upload_file()
                if not file_info:
                    self._uploading = False
                    break

            filepath = file_info.get('file_path')
            file_name = file_info.get('file_name')
            file_size = file_info.get('file_size')
            file_type = file_info.get('file_type')
            file_id = file_info.get('file_id') or file_info.get('fingerprint') or file_name
            db_id = self._upload_db_id(file_info)
            if db_id:
                file_info["db_id"] = db_id

            logger.info(f"Starting upload: {file_name}")
            if not filepath and file_info.get('download_url'):
                try:
                    filepath = self._download_remote_media(file_info['download_url'])
                    file_info['file_path'] = filepath
                    file_info['file_size'] = os.path.getsize(filepath)
                    file_info['size'] = file_info['file_size']
                    file_size = file_info['file_size']
                    logger.info("Remote media cached for upload: name=%s path=%s size=%s", file_name, filepath, file_size)
                except Exception as exc:
                    logger.error("Remote media download failed: name=%s url=%s error=%s", file_name, file_info.get('download_url'), exc)
                    self._update_upload_progress(file_id, 0, 'failed', db_id=db_id, file_item=file_info)
                    continue
            if not filepath or not os.path.exists(filepath):
                logger.error("Upload file is missing: name=%s path=%s", file_name, filepath)
                self._update_upload_progress(file_id, 0, 'failed', db_id=db_id, file_item=file_info)
                continue

            # 请求上传凭证
            credentials = self._request_upload_credentials([file_info])
            if not credentials:
                logger.warning(f"Failed to get credentials for {file_name}")
                self._defer_upload_file(file_info, "upload credentials unavailable")
                break
            if self._credential_expired(credentials):
                logger.warning("Upload credentials expired for %s", file_name)
                self._defer_upload_file(file_info, "upload credentials expired")
                break

            # 执行上传
            storage_config = self._storage_config_from_credentials(credentials)
            upload_url = storage_config.get('upload_url', '')
            if not self._storage_target_available(storage_config, upload_url):
                logger.warning("Upload target unavailable for %s: upload_url and S3 config are empty or incomplete", file_name)
                self._defer_upload_file(file_info, "upload target unavailable")
                break
            uploaded_path = upload_url
            object_key = self._object_key_from_upload_target(storage_config, upload_url, file_name)
            success = False
            for retry in range(self._max_retry_count):
                if upload_url:
                    success = self._upload_file_http(filepath, upload_url)
                    object_key = self._object_key_from_upload_target(storage_config, upload_url, file_name)
                    uploaded_path = upload_url
                else:
                    success, uploaded_path, object_key = self._upload_file_s3(
                        filepath,
                        storage_config,
                        self._object_key_from_storage(storage_config, file_name),
                    )
                if success:
                    break
                logger.warning(f"Upload attempt {retry + 1} failed for {file_name}")
                time.sleep(2)

            # 更新状态
            if success:
                logger.info(f"Upload completed: {file_name}")
                self._update_upload_progress(
                    file_id,
                    100,
                    'completed',
                    db_id=db_id,
                    upload_url=uploaded_path,
                    file_item={**file_info, "object_key": object_key, "path": uploaded_path or filepath},
                )
                self.report_upload_complete({
                    'object_key': object_key,
                    'path': uploaded_path or filepath,
                    'name': file_name,
                    'ext': {
                        'flight_id': file_info.get('flight_id') or file_info.get('mission_id') or '',
                    },
                    'metadata': {},
                    'cloud_to_cloud_id': file_info.get('fingerprint') or file_id or 'DEFAULT',
                })
            else:
                logger.error(f"Upload failed after {self._max_retry_count} attempts: {file_name}")
                self._update_upload_progress(file_id, 0, 'failed', db_id=db_id, file_item=file_info)

    def start_upload(self, flight_id: str = None, target_names: set[str] | None = None):
        """开始上传任务（异步触发）"""
        self._run_db_coroutine(self._async_start_upload(flight_id or "", target_names or set()))

    async def _async_start_upload(self, flight_id: str = "", target_names: set[str] | None = None):
        """异步获取待上传文件并启动上传"""
        target_names = set(target_names or set())
        pending_files = await self._get_pending_files_for_upload(flight_id, target_names)

        if not pending_files:
            logger.info(
                "No pending files to upload: flight_id=%s targets=%s",
                flight_id or "<all>",
                sorted(target_names) or "<all>",
            )
            return

        logger.info(
            "Found %s pending files for upload: flight_id=%s targets=%s",
            len(pending_files),
            flight_id or "<all>",
            sorted(target_names) or "<all>",
        )

        upload_worker_running = False
        with self._upload_lock:
            added = self._enqueue_upload_files_locked(pending_files)
            worker_alive = bool(self._upload_thread and self._upload_thread.is_alive())
            upload_worker_running = self._uploading and worker_alive
            self._uploading = True

            if not upload_worker_running:
                self._upload_thread = threading.Thread(
                    target=self._upload_files, daemon=True
                )
                self._upload_thread.start()

        if added < len(pending_files):
            logger.info("Skipped %s duplicate pending upload queue items", len(pending_files) - added)
        if upload_worker_running:
            logger.info("Upload worker already running; pending files queued: added=%s", added)

    def handle_upload_flighttask_media_prioritize(self, msg: dict) -> dict:
        """
        上传飞行任务媒体优先化 (upload_flighttask_media_prioritize)

        Request: { flight_id: str }
        Response: { result }
        """
        data = msg.get('data', {})
        flight_id = data.get('flight_id') or data.get('flighttask_id', '')
        target_names = self._normalize_upload_target_names(data)
        logger.info("Upload flighttask media prioritize: %s targets=%s", flight_id, sorted(target_names) or "<all>")

        # 开始上传
        self.start_upload(flight_id, target_names)

        return MessageBuilder.build_reply(msg, result=0, data=media_task_output("sent", {
            "flight_id": flight_id,
            "targets": sorted(target_names),
        }))

    # ---- 直播控制 ----

    def _get_video_source(self, video_type: str = 'normal') -> str:
        """
        获取视频源地址

        支持多种输入源：
        - V4L2: /dev/video0, /dev/video1
        - RTSP: rtsp://xxx
        - RTP/UDP: rtp://xxx, udp://xxx
        - 本地视频设备: /dev/video0, /dev/video1
        """
        # 从配置读取视频源映射
        source_map = Config.get('media.video_sources', {
            'normal': '/dev/video0',
            'ir': '/dev/video1',
            'wide': '/dev/video0',
            'zoom': '/dev/video0',
            'gimbal': '/dev/video0',
        })
        return source_map.get(video_type, self.DEFAULT_VIDEO_SOURCE)

    def _live_source_key_from_video_id(self, video_id: str) -> str:
        parts = str(video_id or "").split("/")
        if len(parts) < 3:
            return ""
        camera_index = parts[-2]
        video_index = parts[-1]
        payload_config = Config.get_payload_config()
        devices = payload_config.get("devices", {}) if isinstance(payload_config, dict) else {}
        device_items = devices.items() if isinstance(devices, dict) else []
        for device_id, device in device_items:
            if not isinstance(device, dict) or not device.get("enabled", True):
                continue
            cloud_api = device.get("cloud_api")
            if not isinstance(cloud_api, dict):
                continue
            if payload_index_from_config(device) != camera_index:
                continue
            configured_video_index = str(cloud_api.get("video_index") or "")
            if configured_video_index and configured_video_index != video_index:
                continue
            return str(cloud_api.get("source_key") or device_id)
        return ""

    def _build_ffmpeg_cmd(self, url: str, url_type: int, quality: int, video_type: str) -> list:
        """
        构建 ffmpeg 推流命令

        Args:
            url: 推流地址
            url_type: 0=Agora, 1=RTMP, 3=GB28181, 4=WebRTC
            quality: 画质等级
            video_type: 镜头类型

        Returns:
            list: ffmpeg 命令参数列表
        """
        source = self._get_video_source(video_type)
        preset = self.QUALITY_PRESETS.get(quality, self.QUALITY_PRESETS[0])
        resolution = preset['resolution']
        bitrate = preset['bitrate']
        fps = preset['fps']

        cmd = ['ffmpeg', '-y']

        # 输入源
        if source.startswith('/dev/video'):
            # V4L2 摄像头
            cmd.extend(['-f', 'v4l2', '-i', source])
        elif source.startswith('rtsp://'):
            # RTSP 网络流
            cmd.extend(['-rtsp_transport', 'tcp', '-i', source])
        elif source.startswith('http://') or source.startswith('https://'):
            # HTTP 流
            cmd.extend(['-i', source])
        elif source.startswith('rtp://') or source.startswith('udp://'):
            # Vendor push streams, e.g. YCamSDK realtime video on RTP 23008.
            cmd.extend(['-protocol_whitelist', 'file,udp,rtp,tcp', '-i', source])
        else:
            raise ValueError(f"Unsupported live video source for real chain: {source}")

        # 视频编码参数
        cmd.extend([
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-tune', 'zerolatency',
            '-pix_fmt', 'yuv420p',
            '-s', resolution,
            '-r', str(fps),
            '-b:v', bitrate,
            '-maxrate', bitrate,
            '-bufsize', '2000k',
            '-g', str(fps * 2),  # GOP = 2秒
            '-keyint_min', str(fps),
        ])

        # 音频（如果有）
        cmd.extend(['-an'])  # 禁用音频，减少带宽

        output_url = str(url or "").strip()
        output_url_lower = output_url.lower()

        # 输出格式：当前内置 ffmpeg 真实链路只负责 RTMP/RTMPS。
        # WebRTC、GB28181、Agora 都需要专用适配器，不能用 mpegts/http 伪装。
        if url_type in self.DEDICATED_LIVE_ADAPTER_URL_TYPES:
            protocol = self.DEDICATED_LIVE_ADAPTER_URL_TYPES[url_type]
            raise ValueError(f"{protocol} 真实推流需要专用直播适配器，ffmpeg 不支持原生推流")
        if url_type != 1:
            raise ValueError(f"当前 ffmpeg 真实推流只支持 RTMP url_type=1，不支持 url_type={url_type}")
        if not output_url_lower.startswith(("rtmp://", "rtmps://")):
            raise ValueError("RTMP 真实推流地址必须以 rtmp:// 或 rtmps:// 开头")

        cmd.extend(['-f', 'flv', output_url])

        return cmd

    def _start_ffmpeg(self, url: str, url_type: int, quality: int, video_type: str) -> bool:
        """启动 ffmpeg 推流进程"""
        self._stop_ffmpeg()

        try:
            cmd = self._build_ffmpeg_cmd(url, url_type, quality, video_type)
        except ValueError as exc:
            logger.error(str(exc))
            self._live_streaming = False
            self._live_pipeline.append({
                "step": "build_ffmpeg_command",
                "status": "failed",
                "reason": str(exc),
            })
            return False
        logger.info(f"Starting ffmpeg: {' '.join(cmd)}")
        self._live_pipeline.append({
            "step": "build_ffmpeg_command",
            "status": "ok",
            "command": cmd,
        })

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
            monitor_thread = threading.Thread(
                target=self._ffmpeg_monitor, args=(process,), daemon=True
            )
            with self._ffmpeg_process_lock:
                self._ffmpeg_process = process
                self._ffmpeg_monitor_thread = monitor_thread
            logger.info(f"FFmpeg started, pid={process.pid}")
            self._live_pipeline.append({
                "step": "start_ffmpeg_process",
                "status": "ok",
                "pid": process.pid,
            })

            # 启动监控线程
            monitor_thread.start()
            return True

        except FileNotFoundError:
            logger.error("ffmpeg not found. Please install ffmpeg first.")
            self._live_streaming = False
            self._live_pipeline.append({
                "step": "start_ffmpeg_process",
                "status": "failed",
                "reason": "ffmpeg not found",
            })
            return False
        except Exception as e:
            logger.error(f"Failed to start ffmpeg: {e}")
            self._live_streaming = False
            self._live_pipeline.append({
                "step": "start_ffmpeg_process",
                "status": "failed",
                "reason": str(e),
            })
            return False

    def _last_live_pipeline_failure_reason(self, default: str) -> str:
        """从最近的直播流水线失败节点中取出可回传平台的原因。"""
        for item in reversed(self._live_pipeline):
            if isinstance(item, dict) and item.get("status") == "failed" and item.get("reason"):
                return str(item["reason"])
        return default

    def _stop_ffmpeg(self):
        """停止 ffmpeg 推流进程"""
        with self._ffmpeg_process_lock:
            process = self._ffmpeg_process
            monitor_thread = self._ffmpeg_monitor_thread
        if not process:
            return

        try:
            process.terminate()
            monitor_thread_started = bool(getattr(monitor_thread, "ident", None))
            if monitor_thread and monitor_thread_started:
                if monitor_thread.is_alive() and monitor_thread is not threading.current_thread():
                    monitor_thread.join(timeout=5.0)
                    if monitor_thread.is_alive():
                        process.kill()
                        logger.warning("FFmpeg killed after monitor timeout")
                        monitor_thread.join(timeout=2.0)
                if not monitor_thread.is_alive():
                    logger.info(f"FFmpeg stopped, pid={process.pid}")
            else:
                process.communicate(timeout=5.0)
                logger.info(f"FFmpeg stopped, pid={process.pid}")
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            logger.warning("FFmpeg killed after timeout")
        except Exception as e:
            logger.error(f"Error stopping ffmpeg: {e}")
        finally:
            with self._ffmpeg_process_lock:
                if self._ffmpeg_process is process:
                    self._ffmpeg_process = None
                if self._ffmpeg_monitor_thread is monitor_thread and (
                    not monitor_thread or not monitor_thread.is_alive()
                ):
                    self._ffmpeg_monitor_thread = None

    def _publish_live_status_event(
        self,
        status: str,
        reason: str = "",
        *,
        live_status: Optional[int] = None,
    ):
        data = live_status_event_data(
            status=status,
            video_id=self._live_video_id,
            session_id=self._live_session_id,
            url_type=self._live_protocol,
            video_quality=self._live_quality,
            video_type=self._live_video_type,
            url=self._live_url,
            reason=reason,
            source=self._get_video_source(self._live_source_key),
        )
        if self._event_callback:
            self._event_callback(MessageBuilder.build_event(
                "live_status_notify",
                data,
                self.gateway_sn,
            ))
        if self._state_callback:
            self._state_callback({"property": {"live_status": [
                self._live_status_state_item(data, live_status=live_status)
            ]}})

    def _clear_live_session_state(self):
        self._live_streaming = False
        self._live_session_id = ""
        self._live_video_id = ""
        self._live_url = ""
        self._live_protocol = 4
        self._live_quality = 0
        self._live_video_type = str(Config.get('livestream.default_video_type', 'gimbal') or 'gimbal')
        self._live_source_key = self._live_video_type

    @staticmethod
    def _live_status_state_item(data: dict, live_status: Optional[int] = None) -> dict:
        output = data.get("output", {}) if isinstance(data, dict) else {}
        task_status = output.get("status", "")
        status_value = live_status
        if status_value is None:
            status_value = 1 if task_status == TASK_STATUS_IN_PROGRESS else 0
        return {
            "video_id": data.get("video_id", ""),
            "video_type": data.get("video_type", "normal"),
            "video_quality": data.get("video_quality", 0),
            "status": status_value,
            "error_status": 0 if task_status in {TASK_STATUS_OK, TASK_STATUS_IN_PROGRESS} else 1,
        }

    def _ffmpeg_monitor(self, process: subprocess.Popen):
        """监控 ffmpeg 进程状态"""
        stdout, stderr = process.communicate()
        returncode = process.returncode

        if returncode != 0 and returncode is not None:
            stderr_text = stderr.decode('utf-8', errors='ignore')[-500:] if stderr else ""
            logger.warning(f"FFmpeg exited with code {returncode}: {stderr_text}")

        with self._ffmpeg_process_lock:
            if self._ffmpeg_monitor_thread is threading.current_thread():
                self._ffmpeg_monitor_thread = None
            if self._ffmpeg_process is process and self._live_streaming:
                logger.info("FFmpeg exited, live streaming stopped")
                self._live_streaming = False
                self._live_session_id = ""
                self._ffmpeg_process = None

    def _live_start_failed_reply(self, msg: dict, reason: str, result: int = DOCK_SYSTEM_ERROR) -> dict:
        """开始直播失败时明确回包，并向平台同步失败状态。"""
        logger.error("Live start failed: %s", reason)
        self._live_streaming = False
        self._publish_live_status_event(TASK_STATUS_FAILED, reason=reason, live_status=0)
        failed_video_id = self._live_video_id
        failed_quality = self._live_quality
        failed_video_type = self._live_video_type
        self._clear_live_session_state()
        return MessageBuilder.build_reply(
            msg,
            result=result,
            data=live_task_output(TASK_STATUS_FAILED, {
                "reason": reason,
                "video_id": failed_video_id,
                "video_quality": failed_quality,
                "video_type": failed_video_type,
            }),
            msg_str=reason,
        )

    def _live_restart_failed_reply(self, msg: dict, reason: str) -> dict:
        """直播切流重启失败时，向平台明确返回失败并同步断流状态。"""
        logger.error("Live stream restart failed: %s", reason)
        self._live_streaming = False
        self._publish_live_status_event(TASK_STATUS_FAILED, reason=reason, live_status=0)
        failed_video_id = self._live_video_id
        failed_quality = self._live_quality
        failed_video_type = self._live_video_type
        self._clear_live_session_state()
        return MessageBuilder.build_reply(
            msg,
            result=DOCK_SYSTEM_ERROR,
            data=live_task_output(TASK_STATUS_FAILED, {
                "reason": reason,
                "video_id": failed_video_id,
                "video_quality": failed_quality,
                "video_type": failed_video_type,
            }),
            msg_str=reason,
        )

    def handle_live_start(self, msg: dict) -> dict:
        """
        开始直播 (live_start_push)

        文档格式：
        Request: {
            url_type: 0=Agora, 1=RTMP, 3=GB28181, 4=WebRTC,
            url: '推流地址',
            video_id: '{sn}/{camera_index}/{video_index}',
            video_quality: 0=自适应, 1=流畅, 2=标清, 3=高清, 4=超清
        }
        """
        ok, data, errors = validate_live_start_payload(msg.get('data', {}))
        if not ok:
            logger.warning("Rejected live_start payload: %s", "; ".join(errors))
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=live_task_output(TASK_STATUS_REJECTED, {"errors": errors}),
                msg_str="; ".join(errors),
            )

        url_type = data["url_type"]
        url = data["url"]
        video_id = data["video_id"]
        video_quality = data["video_quality"]
        requested_video_type = data.get("video_type") or self._video_type_from_video_id(video_id)
        requested_source_key = self._live_source_key_from_video_id(video_id)
        live_source_key = self._live_source_key
        live_video_type = self._live_video_type
        if requested_source_key:
            live_source_key = requested_source_key
            live_video_type = requested_video_type or requested_source_key
        elif requested_video_type:
            live_video_type = requested_video_type
            live_source_key = requested_video_type
        selected_source = self._get_video_source(live_source_key)
        self._live_pipeline = [
            {
                "step": "validate_live_start",
                "status": "ok",
                "url_type": url_type,
                "video_id": video_id,
                "video_quality": video_quality,
            },
            {
                "step": "select_video_source",
                "status": "ok",
                "video_type": live_video_type,
                "source_key": live_source_key,
                "source": selected_source,
            },
        ]

        if self._live_streaming:
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=live_task_output(TASK_STATUS_REJECTED, {"reason": "already streaming"}),
                msg_str="Already streaming",
            )

        self._live_streaming = True
        self._live_session_id = str(int(time.time() * 1000))
        self._live_protocol = url_type
        self._live_quality = video_quality
        self._live_video_id = video_id
        self._live_url = url
        self._live_video_type = live_video_type
        self._live_source_key = live_source_key

        logger.info(f"Live start: url_type={url_type}, video_id={video_id}, "
                   f"quality={video_quality}, url={url}")

        if is_mock_live_url(url) and str(Config.get('runtime.mode', 'test')).lower() == 'production':
            return self._live_start_failed_reply(
                msg,
                "Mock live url is disabled in production",
                result=INVALID_COMMAND_FORMAT,
            )

        if is_mock_live_url(url):
            logger.info("Live mock stream enabled: video_id=%s, url=%s", video_id, url)
            self._live_pipeline.append({
                "step": "start_mock_stream",
                "status": "ok",
                "url": url,
            })
        else:
            if url_type not in REALTIME_FFMPEG_URL_TYPES:
                return self._live_start_failed_reply(
                    msg,
                    "Live protocol adapter unavailable for url_type=%s" % url_type,
                    result=DOCK_SYSTEM_ERROR,
                )
            if not self._start_ffmpeg(url, url_type, video_quality, live_source_key):
                reason = self._last_live_pipeline_failure_reason("Live video source unavailable")
                failure_result = DOCK_SYSTEM_ERROR
                if (
                    reason == "Live video source unavailable"
                    or reason.startswith("Unsupported live video source")
                ):
                    failure_result = INVALID_COMMAND_FORMAT
                return self._live_start_failed_reply(
                    msg,
                    reason,
                    result=failure_result,
                )

        self._publish_live_status_event(TASK_STATUS_IN_PROGRESS, live_status=1)
        self._live_pipeline.append({
            "step": "publish_live_status",
            "status": "ok",
            "live_status": 1,
        })
        return MessageBuilder.build_reply(msg, result=0, data=live_task_output(TASK_STATUS_IN_PROGRESS, {
            'session_id': self._live_session_id,
            'video_id': self._live_video_id,
            'url_type': self._live_protocol,
            'video_quality': self._live_quality,
            'video_type': self._live_video_type,
        }))

    def handle_live_start_push(self, msg: dict) -> dict:
        """官方 Dock3 开始直播方法名。"""
        return self.handle_live_start(msg)

    @staticmethod
    def _video_type_from_video_id(video_id: str) -> str:
        value = str(video_id or "").strip()
        if not value:
            return ""
        tail = value.rsplit("/", 1)[-1]
        return tail if tail in {"normal", "wide", "zoom", "ir", "fpv", "gimbal"} else ""

    def handle_live_stop(self, msg: dict) -> dict:
        """
        停止直播 (live_stop_push)

        文档格式：
        Request: { video_id: '{sn}/{camera_index}/{video_index}' }
        """
        data = msg.get('data', {}) if isinstance(msg.get('data', {}), dict) else {}
        video_id = data.get('video_id', self._live_video_id)

        if not self._live_streaming:
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=live_task_output(TASK_STATUS_REJECTED, {"reason": "not streaming"}),
                msg_str="Not streaming",
            )

        logger.info(f"Live stop: video_id={video_id}")

        # 停止 ffmpeg 推流
        self._stop_ffmpeg()

        self._live_video_id = video_id or self._live_video_id
        self._publish_live_status_event(TASK_STATUS_OK, live_status=0)

        stopped_session = self._live_session_id
        self._live_streaming = False
        self._live_session_id = ""
        self._live_video_id = ""
        self._live_url = ""

        return MessageBuilder.build_reply(msg, result=0, data=live_task_output(TASK_STATUS_OK, {
            "session_id": stopped_session,
            "video_id": video_id,
        }))

    def handle_live_stop_push(self, msg: dict) -> dict:
        """官方 Dock3 停止直播方法名。"""
        return self.handle_live_stop(msg)

    def handle_live_camera_change(self, msg: dict) -> dict:
        """
        直播相机切换

        Request: { video_id, camera_position: 0=舱内, 1=舱外 }
        """
        ok, data, errors = validate_live_camera_payload(msg.get('data', {}))
        if not ok:
            logger.warning("Rejected live_camera_change payload: %s", "; ".join(errors))
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=live_task_output(TASK_STATUS_REJECTED, {"errors": errors}),
                msg_str="; ".join(errors),
            )

        self._live_video_id = data["video_id"]
        self._live_camera_position = data["camera_position"]
        logger.info(
            "Live camera change: video_id=%s, camera_position=%s",
            self._live_video_id,
            self._live_camera_position,
        )
        return MessageBuilder.build_reply(msg, result=0, data=live_task_output(TASK_STATUS_OK, {
            "video_id": self._live_video_id,
            "camera_position": self._live_camera_position,
        }))

    def handle_set_livestream_lens(self, msg: dict) -> dict:
        """
        切换直播镜头 (live_lens_change)

        文档格式：
        Request: {
            video_id: '{sn}/{camera_index}/{video_index}',
            video_type: 'ir'/'normal'/'wide'/'zoom'
        }
        """
        ok, data, errors = validate_live_lens_payload(msg.get('data', {}))
        if not ok:
            logger.warning("Rejected live_lens_change payload: %s", "; ".join(errors))
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=live_task_output(TASK_STATUS_REJECTED, {"errors": errors}),
                msg_str="; ".join(errors),
            )

        video_id = data["video_id"] or self._live_video_id
        video_type = data["video_type"]

        logger.info(f"Livestream lens change: video_id={video_id}, type={video_type}")

        self._live_video_type = video_type
        self._live_source_key = video_type

        # 如果正在推流，重启 ffmpeg 切换视频源
        if self._live_streaming and self._live_url and not is_mock_live_url(self._live_url):
            if not self._start_ffmpeg(self._live_url, self._live_protocol, self._live_quality, self._live_source_key):
                return self._live_restart_failed_reply(
                    msg,
                    "Live lens change failed because ffmpeg restart failed",
                )

        self._publish_live_status_event(TASK_STATUS_OK, live_status=1 if self._live_streaming else 0)
        return MessageBuilder.build_reply(msg, result=0, data=live_task_output(TASK_STATUS_OK, {
            "video_id": video_id,
            "video_type": self._live_video_type,
        }))

    def _take_photo_after_lens_change(self, msg: dict, video_id: str, video_type: str) -> dict:
        """切换直播视频源后直接拍照，不切换相机模式。"""
        if not self._camera_controller or not hasattr(self._camera_controller, "handle_camera_photo_take"):
            logger.warning("Live lens changed but camera controller is not available for direct photo")
            return {"status": "skipped", "reason": "camera_controller_unavailable"}

        source_data = msg.get("data", {}) if isinstance(msg.get("data"), dict) else {}
        photo_data = {
            "camera_index": 0,
            "video_id": video_id,
            "video_type": video_type,
            "live_lens_type": video_type,
            "trigger": "live_lens_change",
        }
        # video_type 是直播镜头类型，不等同于底层拍照相机类型。品灵/ViewLink
        # 拍照命令本身固定调用 Photograph；只有平台显式传 camera_type 时才透传。
        explicit_camera_type = str(source_data.get("camera_type") or "").strip()
        if explicit_camera_type:
            photo_data["camera_type"] = explicit_camera_type

        photo_msg = {
            "tid": msg.get("tid"),
            "bid": msg.get("bid"),
            "gateway": msg.get("gateway", self.gateway_sn),
            "method": "camera_photo_take",
            "data": photo_data,
        }
        logger.info(
            "Live lens change triggers direct photo: video_id=%s, video_type=%s",
            video_id,
            video_type,
        )
        try:
            reply = self._camera_controller.handle_camera_photo_take(photo_msg)
        except Exception as exc:
            logger.warning("Direct photo after live lens change failed: %s", exc, exc_info=True)
            return {"status": "failed", "reason": str(exc)}

        data = reply.get("data", {}) if isinstance(reply, dict) else {}
        result = data.get("result") if isinstance(data, dict) else None
        return {
            "status": "ok" if result == 0 else "failed",
            "result": result,
            "reply": reply,
        }

    def handle_set_livestream_quality(self, msg: dict) -> dict:
        """
        设置直播画质 (live_set_quality)

        文档格式：
        Request: {
            video_id: '{sn}/{camera_index}/{video_index}',
            video_quality: 0=自适应, 1=流畅, 2=标清, 3=高清, 4=超清
        }
        """
        ok, data, errors = validate_live_quality_payload(msg.get('data', {}))
        if not ok:
            logger.warning("Rejected live_set_quality payload: %s", "; ".join(errors))
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=live_task_output(TASK_STATUS_REJECTED, {"errors": errors}),
                msg_str="; ".join(errors),
            )

        video_id = data["video_id"]
        video_quality = data["video_quality"]

        self._live_quality = video_quality
        logger.info(f"Livestream quality set: video_id={video_id}, quality={video_quality}")

        # 如果正在推流，重启 ffmpeg 调整画质
        if self._live_streaming and self._live_url and not is_mock_live_url(self._live_url):
            if not self._start_ffmpeg(self._live_url, self._live_protocol, video_quality, self._live_source_key):
                return self._live_restart_failed_reply(
                    msg,
                    "Live quality change failed because ffmpeg restart failed",
                )

        self._publish_live_status_event(TASK_STATUS_OK, live_status=1 if self._live_streaming else 0)
        return MessageBuilder.build_reply(msg, result=0, data=live_task_output(TASK_STATUS_OK, {
            "video_id": video_id,
            "video_quality": self._live_quality,
        }))

    # ---- 存储管理 ----

    def get_storage_info(self) -> dict:
        """获取存储信息"""
        try:
            if os.path.exists(self.storage_path):
                total, used, free = self._get_disk_usage(self.storage_path)
                return {
                    'total_capacity_mb': total // (1024 * 1024),
                    'remain_storage_capacity_mb': free // (1024 * 1024),
                    'used_mb': used // (1024 * 1024)
                }
        except Exception as e:
            logger.error(f"Get storage info error: {e}")

        return {
            'total_capacity_mb': self._total_capacity,
            'remain_storage_capacity_mb': self._total_capacity - self._used_capacity,
            'used_mb': self._used_capacity
        }

    @staticmethod
    def _get_disk_usage(path: str):
        """获取磁盘使用情况"""
        import shutil
        usage = shutil.disk_usage(path)
        return usage.total, usage.used, usage.free

    # ---- 大疆标准媒体管理接口 ----

    def handle_file_list_get(self, msg: dict) -> dict:
        """
        文件列表获取

        Request: { media_type, start_time, end_time, page, page_size }
        """
        data = msg.get('data', {})
        page, page_size = normalize_page(data)

        logger.info(f"File list get: page={page}, size={page_size}")

        filtered = filter_media_files(self._scan_media_files(), data)
        selected = paginate(filtered, page, page_size)

        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=media_task_output("ok", file_list_data(selected, page, page_size, len(filtered))),
        )

    def handle_file_delete(self, msg: dict) -> dict:
        """
        文件删除

        Request: { file_ids: [...] }
        """
        data = msg.get('data', {})
        file_ids, errors = normalize_delete_ids(data)
        if errors:
            logger.warning("Rejected file_delete payload: %s", "; ".join(errors))
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=media_task_output("rejected", {"errors": errors}),
                msg_str="; ".join(errors),
            )

        logger.info(f"File delete: {len(file_ids)} files")
        known = self._scan_media_files()
        by_id = self._media_identity_map(known)
        results = []
        for file_id in file_ids:
            item = by_id.get(file_id)
            if not item:
                results.append({"file_id": file_id, "result": INVALID_COMMAND_FORMAT, "msg": "file not found"})
                continue
            if item.get("source") == "remote_http" and not item.get("file_path"):
                results.append({
                    "file_id": file_id,
                    "result": DEVICE_STATE_UNSUPPORTED,
                    "msg": "remote media delete unsupported",
                })
                continue

            filepath = item.get("file_path", "")
            try:
                if filepath and os.path.exists(filepath):
                    os.remove(filepath)
                self._priority_overrides.pop(item.get("file_id", ""), None)
                results.append({"file_id": file_id, "result": 0})
            except Exception as exc:
                logger.error("Delete media file failed: file_id=%s, path=%s, error=%s", file_id, filepath, exc)
                results.append({"file_id": file_id, "result": INVALID_COMMAND_FORMAT, "msg": str(exc)})

        return MessageBuilder.build_reply(msg, result=0, data=media_task_output("ok", {
            "files": results,
        }))

    def handle_file_download(self, msg: dict) -> dict:
        """
        文件下载到网关本地缓存。

        Request: { file_ids: [...] } 或 { download_url: "http://云台/..." }
        """
        data = msg.get('data', {})
        items, errors = self._resolve_media_items_for_download(data)
        if errors:
            logger.warning("Rejected file_download payload: %s", "; ".join(errors))
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=media_task_output("rejected", {"errors": errors}),
                msg_str="; ".join(errors),
            )

        results = []
        for item in items:
            try:
                downloaded = self._download_media_item(item)
                results.append({"result": 0, **downloaded})
            except Exception as exc:
                logger.error("Download media file failed: item=%s error=%s", item, exc)
                results.append({
                    "result": INVALID_COMMAND_FORMAT,
                    "file_id": item.get("file_id", ""),
                    "name": item.get("name", item.get("file_name", "")),
                    "download_url": item.get("download_url", ""),
                    "msg": str(exc),
                })

        ok = all(item.get("result") == 0 for item in results)
        return MessageBuilder.build_reply(
            msg,
            result=0 if ok else INVALID_COMMAND_FORMAT,
            data=media_task_output("ok" if ok else "failed", {"files": results}),
        )

    def handle_download_media(self, msg: dict) -> dict:
        """兼容 download_media 方法名，实际执行 file_download。"""
        return self.handle_file_download(msg)

    def handle_live_capability_query(self, msg: dict) -> dict:
        """
        直播能力查询

        Request: { }
        """
        logger.info("Live capability query")
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=live_capability_data(self._payload_connection_state_snapshot()),
        )

    def _payload_connection_state_snapshot(self) -> Optional[Dict[str, Any]]:
        provider = getattr(self, "_payload_state_provider", None)
        if not provider:
            return None
        try:
            states = provider() or {}
        except Exception as exc:
            logger.warning("Query live capability payload states failed: %s", exc)
            return None
        return states if isinstance(states, dict) else {}

    def handle_highest_priority_upload_flighttask_media(self, msg: dict) -> dict:
        """
        最高优先级上传任务媒体

        Request: { flight_id }
        """
        data = msg.get('data', {})
        flight_id = data.get('flight_id') or data.get('flighttask_id', '')
        logger.info(f"Highest priority upload: {flight_id}")
        self.report_priority_media(flight_id)
        return MessageBuilder.build_reply(msg, result=0, data=media_task_output("ok", {
            "flight_id": flight_id,
        }))

    def handle_live_lens_change(self, msg: dict) -> dict:
        """Live lens change (live_lens_change) - alias of set_livestream_lens"""
        return self.handle_set_livestream_lens(msg)

    def handle_live_set_quality(self, msg: dict) -> dict:
        """Live set quality (live_set_quality) - alias of set_livestream_quality"""
        return self.handle_set_livestream_quality(msg)
