"""
媒体管理模块
处理媒体文件上传、直播控制、存储管理
"""
import time
import os
import glob
import html.parser
import subprocess
import threading
import asyncio
import urllib.parse
import requests
from pathlib import Path
from typing import Dict, Optional, Callable, List
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from src.cloud_api.dock3_media import (
    build_media_file,
    build_remote_media_file,
    file_list_data,
    filter_media_files,
    media_task_output,
    normalize_delete_ids,
    normalize_page,
    normalize_priority_items,
    paginate,
    storage_config_output,
    upload_progress_event_data,
)
from src.cloud_api.dock3_live import (
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
    - adjust_upload_priority, storage_config_get
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
        self._live_video_type = str(Config.get('livestream.default_video_type', 'gimbal') or 'gimbal')
        self._live_camera_position = 0

        # ffmpeg 推流进程
        self._ffmpeg_process: Optional[subprocess.Popen] = None
        self._ffmpeg_monitor_thread: Optional[threading.Thread] = None

        # 存储状态
        self._total_capacity = Config.get('device.storage_capacity', 64000)  # MB
        self._used_capacity = 0

        # 回调
        self._event_callback: Optional[Callable] = None
        self._request_sender: Optional[Callable] = None

        # 数据库相关
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._db_initialized = False

        # 上传重试配置
        self._max_retry_count = 3

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

    def set_request_sender(self, sender: Callable):
        """设置请求发送器（用于 requests 通道）"""
        self._request_sender = sender

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
        请求存储配置(OSS临时凭证) - services 兼容模式

        Request: { }
        Response: { result, output: { bucket, credentials, endpoint, provider, region, object_key_prefix } }
        """
        logger.info("Storage config get (services mode)")

        # 官方 Dock3 媒体文档要求云端在 requests_reply 中下发 output.credentials。
        # 这里仅从真实配置透传，不生成临时密钥，避免测试值混入真实链路。
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={"output": storage_config_output(Config.get('media.oss', {}))},
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
                    'object_key': file_info.get('object_key', ''),
                    'path': file_info.get('path', ''),
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

            # 扫描常见媒体文件格式
            extensions = ['*.jpg', '*.jpeg', '*.png', '*.mp4', '*.mov', '*.avi']
            for ext in extensions:
                pattern = os.path.join(self.storage_path, ext)
                for filepath in glob.glob(pattern):
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

    def _scan_media_files(self) -> List[dict]:
        return self._scan_local_files() + self._scan_remote_media_files()

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

    async def _get_pending_files_for_upload(self) -> List[dict]:
        """获取待上传的文件列表（与数据库比对）"""
        await self._init_database()
        AsyncSessionLocal = get_async_session_local()

        async with AsyncSessionLocal() as session:
            db_files = await MediaDB.get_pending_uploads(session, self.gateway_sn)
            db_file_names = {f.file_name for f in db_files}

            local_files = self._scan_local_files()
            pending_files = []

            for local_file in local_files:
                if local_file['file_name'] not in db_file_names:
                    pending_files.append(local_file)

            return pending_files

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

        # 同步等待响应
        result = {}

        def callback(response):
            nonlocal result
            result = response

        request_id = self._request_sender('storage_config_get', request_data, callback)

        # 等待响应（最多等待10秒）
        for _ in range(20):
            if result:
                break
            time.sleep(0.5)

        return result.get('data') if result else None

    def _upload_file_http(self, filepath: str, upload_url: str) -> bool:
        """通过HTTP上传文件到对象存储"""
        try:
            with open(filepath, 'rb') as f:
                response = requests.put(upload_url, data=f, timeout=300)
                return response.status_code == 200
        except Exception as e:
            logger.error(f"HTTP upload failed: {e}")
            return False

    def _update_upload_progress(self, file_id: str, progress: int, status: str):
        """更新上传进度"""
        async def _update_status():
            await self._init_database()
            AsyncSessionLocal = get_async_session_local()
            if not AsyncSessionLocal:
                return
            async with AsyncSessionLocal() as session:
                await MediaDB.update_upload_status(session, file_id, status)
                await session.commit()

        self._run_db_coroutine(_update_status())

        # 上报进度事件
        if self._event_callback:
            event_files = [self._media_identity_map(self._scan_local_files()).get(file_id, {
                "file_id": file_id,
                "fingerprint": file_id,
                "device_sn": self.gateway_sn,
                "object_key": file_id,
                "size": 0,
            })]
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

                file_info = self._upload_queue.pop(0)

            filepath = file_info.get('file_path')
            file_name = file_info.get('file_name')
            file_size = file_info.get('file_size')
            file_type = file_info.get('file_type')

            logger.info(f"Starting upload: {file_name}")

            # 请求上传凭证
            credentials = self._request_upload_credentials([file_info])
            if not credentials:
                logger.warning(f"Failed to get credentials for {file_name}")
                time.sleep(5)
                continue

            upload_url = credentials.get('upload_url', '')
            if not upload_url:
                logger.warning(f"No upload URL for {file_name}")
                time.sleep(5)
                continue

            # 执行上传
            success = False
            for retry in range(self._max_retry_count):
                success = self._upload_file_http(filepath, upload_url)
                if success:
                    break
                logger.warning(f"Upload attempt {retry + 1} failed for {file_name}")
                time.sleep(2)

            # 更新状态
            if success:
                logger.info(f"Upload completed: {file_name}")
                self._update_upload_progress(file_name, 100, 'completed')
                self.report_upload_complete({
                    'object_key': upload_url,
                    'path': filepath,
                    'name': file_name,
                    'ext': {},
                    'metadata': {}
                })
            else:
                logger.error(f"Upload failed after {self._max_retry_count} attempts: {file_name}")
                self._update_upload_progress(file_name, 0, 'failed')

    def start_upload(self, flight_id: str = None):
        """开始上传任务（异步触发）"""
        self._run_db_coroutine(self._async_start_upload())

    async def _async_start_upload(self):
        """异步获取待上传文件并启动上传"""
        pending_files = await self._get_pending_files_for_upload()

        if not pending_files:
            logger.info("No pending files to upload")
            return

        logger.info(f"Found {len(pending_files)} pending files for upload")

        with self._upload_lock:
            self._upload_queue.extend(pending_files)
            self._uploading = True

        if not self._upload_thread or not self._upload_thread.is_alive():
            self._upload_thread = threading.Thread(
                target=self._upload_files, daemon=True
            )
            self._upload_thread.start()

    def handle_upload_flighttask_media_prioritize(self, msg: dict) -> dict:
        """
        上传飞行任务媒体优先化 (upload_flighttask_media_prioritize)

        Request: { flight_id: str }
        Response: { result }
        """
        data = msg.get('data', {})
        flight_id = data.get('flight_id') or data.get('flighttask_id', '')
        logger.info(f"Upload flighttask media prioritize: {flight_id}")

        # 开始上传
        self.start_upload(flight_id)

        return MessageBuilder.build_reply(msg, result=0, data=media_task_output("sent", {
            "flight_id": flight_id,
        }))

    # ---- 直播控制 ----

    def _get_video_source(self, video_type: str = 'normal') -> str:
        """
        获取视频源地址

        支持多种输入源：
        - V4L2: /dev/video0, /dev/video1
        - RTSP: rtsp://xxx
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

        # 输出格式
        if url_type == 1 or 'rtmp://' in url:
            # RTMP
            cmd.extend(['-f', 'flv', url])
        elif url_type == 4 or url.startswith('http'):
            # WebRTC / HTTP
            cmd.extend(['-f', 'mpegts', url])
        else:
            # 默认 RTMP
            cmd.extend(['-f', 'flv', url])

        return cmd

    def _start_ffmpeg(self, url: str, url_type: int, quality: int, video_type: str) -> bool:
        """启动 ffmpeg 推流进程"""
        self._stop_ffmpeg()

        try:
            cmd = self._build_ffmpeg_cmd(url, url_type, quality, video_type)
        except ValueError as exc:
            logger.error(str(exc))
            self._live_streaming = False
            return False
        logger.info(f"Starting ffmpeg: {' '.join(cmd)}")

        try:
            self._ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
            logger.info(f"FFmpeg started, pid={self._ffmpeg_process.pid}")

            # 启动监控线程
            self._ffmpeg_monitor_thread = threading.Thread(
                target=self._ffmpeg_monitor, daemon=True
            )
            self._ffmpeg_monitor_thread.start()
            return True

        except FileNotFoundError:
            logger.error("ffmpeg not found. Please install ffmpeg first.")
            self._live_streaming = False
            return False
        except Exception as e:
            logger.error(f"Failed to start ffmpeg: {e}")
            self._live_streaming = False
            return False

    def _stop_ffmpeg(self):
        """停止 ffmpeg 推流进程"""
        if self._ffmpeg_process:
            try:
                self._ffmpeg_process.terminate()
                self._ffmpeg_process.wait(timeout=5.0)
                logger.info(f"FFmpeg stopped, pid={self._ffmpeg_process.pid}")
            except subprocess.TimeoutExpired:
                self._ffmpeg_process.kill()
                logger.warning("FFmpeg killed after timeout")
            except Exception as e:
                logger.error(f"Error stopping ffmpeg: {e}")
            finally:
                self._ffmpeg_process = None

    def _publish_live_status_event(self, status: str, reason: str = ""):
        if not self._event_callback:
            return
        self._event_callback(MessageBuilder.build_event(
            "live_status_notify",
            live_status_event_data(
                status=status,
                video_id=self._live_video_id,
                session_id=self._live_session_id,
                url_type=self._live_protocol,
                video_quality=self._live_quality,
                video_type=self._live_video_type,
                url=self._live_url,
                reason=reason,
                source=self._get_video_source(self._live_video_type),
            ),
            self.gateway_sn,
        ))

    def _ffmpeg_monitor(self):
        """监控 ffmpeg 进程状态"""
        if not self._ffmpeg_process:
            return

        stdout, stderr = self._ffmpeg_process.communicate()
        returncode = self._ffmpeg_process.returncode

        if returncode != 0 and returncode is not None:
            stderr_text = stderr.decode('utf-8', errors='ignore')[-500:] if stderr else ""
            logger.warning(f"FFmpeg exited with code {returncode}: {stderr_text}")

        if self._live_streaming:
            logger.info("FFmpeg exited, live streaming stopped")
            self._live_streaming = False
            self._live_session_id = ""

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
        if requested_video_type:
            self._live_video_type = requested_video_type

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

        logger.info(f"Live start: url_type={url_type}, video_id={video_id}, "
                   f"quality={video_quality}, url={url}")

        if is_mock_live_url(url) and str(Config.get('runtime.mode', 'test')).lower() == 'production':
            self._live_streaming = False
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=live_task_output(TASK_STATUS_REJECTED, {"reason": "mock live url is disabled in production"}),
                msg_str="Mock live url is disabled in production",
            )

        if is_mock_live_url(url):
            logger.info("Live mock stream enabled: video_id=%s, url=%s", video_id, url)
        else:
            if not self._start_ffmpeg(url, url_type, video_quality, self._live_video_type):
                self._live_session_id = ""
                self._live_video_id = ""
                self._live_url = ""
                return MessageBuilder.build_reply(
                    msg,
                    result=INVALID_COMMAND_FORMAT,
                    data=live_task_output(TASK_STATUS_REJECTED, {"reason": "live video source unavailable"}),
                    msg_str="Live video source unavailable",
                )

        self._publish_live_status_event(TASK_STATUS_IN_PROGRESS)
        return MessageBuilder.build_reply(msg, result=0, data=live_task_output(TASK_STATUS_IN_PROGRESS, {
            'session_id': self._live_session_id,
            'video_id': self._live_video_id,
            'url_type': self._live_protocol,
            'video_quality': self._live_quality,
            'video_type': self._live_video_type,
            'source': self._get_video_source(self._live_video_type),
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
        self._publish_live_status_event(TASK_STATUS_OK)

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

        # 如果正在推流，重启 ffmpeg 切换视频源
        if self._live_streaming and self._live_url and not is_mock_live_url(self._live_url):
            self._start_ffmpeg(self._live_url, self._live_protocol, self._live_quality, video_type)

        self._publish_live_status_event(TASK_STATUS_OK)
        return MessageBuilder.build_reply(msg, result=0, data=live_task_output(TASK_STATUS_OK, {
            "video_id": video_id,
            "video_type": self._live_video_type,
        }))

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
            self._start_ffmpeg(self._live_url, self._live_protocol, video_quality, self._live_video_type)

        self._publish_live_status_event(TASK_STATUS_OK)
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
        known = self._scan_local_files()
        by_id = self._media_identity_map(known)
        results = []
        for file_id in file_ids:
            item = by_id.get(file_id)
            if not item:
                results.append({"file_id": file_id, "result": INVALID_COMMAND_FORMAT, "msg": "file not found"})
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

    def handle_live_capability_query(self, msg: dict) -> dict:
        """
        直播能力查询

        Request: { }
        """
        logger.info("Live capability query")
        return MessageBuilder.build_reply(msg, result=0, data=live_capability_data())

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
