"""
媒体管理模块
处理媒体文件上传、直播控制、存储管理
"""
import time
import os
import subprocess
import threading
from typing import Dict, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder

logger = get_logger("media")


class MediaManager:
    """
    媒体管理器

    职责:
    1. 媒体文件管理(列表、删除)
    2. 上传控制(优先级调整、断点续传)
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
        self.storage_path = Config.get('media.storage_path', '/data/media')

        # 上传队列
        self._upload_queue = []
        self._uploading = False

        # 直播状态
        self._live_streaming = False
        self._live_session_id = ""
        self._live_protocol = 4  # 默认 WebRTC
        self._live_quality = 0   # 默认自适应
        self._live_video_id = ""
        self._live_url = ""
        self._live_video_type = "normal"

        # ffmpeg 推流进程
        self._ffmpeg_process: Optional[subprocess.Popen] = None
        self._ffmpeg_monitor_thread: Optional[threading.Thread] = None

        # 存储状态
        self._total_capacity = Config.get('device.storage_capacity', 64000)  # MB
        self._used_capacity = 0

        # 回调
        self._event_callback: Optional[Callable] = None
        self._request_sender: Optional[Callable] = None

    def set_event_callback(self, callback: Callable):
        """设置事件上报回调"""
        self._event_callback = callback

    def set_request_sender(self, sender: Callable):
        """设置请求发送器（用于 requests 通道）"""
        self._request_sender = sender

    # ---- 媒体文件管理 ----

    def handle_adjust_upload_priority(self, msg: dict) -> dict:
        """
        调整上传优先级

        Request: { files: [{file_id, priority}, ...] }
        """
        data = msg.get('data', {})
        files = data.get('files', [])

        logger.info(f"Adjust upload priority: {len(files)} files")

        # TODO: 重新排序上传队列

        return MessageBuilder.build_reply(msg, result=0)

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
        Response: { endpoint, bucket, access_key, secret_key, token, expiration }
        """
        logger.info("Storage config get (services mode)")

        # TODO: 从云端获取STS临时凭证
        # 或返回本地MinIO配置

        return MessageBuilder.build_reply(msg, result=0, data={
            'endpoint': '',
            'bucket': '',
            'access_key': '',
            'secret_key': '',
            'token': '',
            'expiration': 3600
        })

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
            payload = {
                'file': {
                    'object_key': file_info.get('object_key', ''),
                    'path': file_info.get('path', ''),
                    'name': file_info.get('name', ''),
                    'ext': file_info.get('ext', {}),
                    'metadata': file_info.get('metadata', {}),
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

    # ---- 直播控制 ----

    def _get_video_source(self, video_type: str = 'normal') -> str:
        """
        获取视频源地址

        支持多种输入源：
        - V4L2: /dev/video0, /dev/video1
        - RTSP: rtsp://xxx
        - 测试源: lavfi/testsrc
        """
        # 从配置读取视频源映射
        source_map = Config.get('media.video_sources', {
            'normal': '/dev/video0',
            'ir': '/dev/video1',
            'wide': '/dev/video0',
            'zoom': '/dev/video0',
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
            # 测试源（无摄像头时）
            cmd.extend(['-f', 'lavfi', '-i', f'testsrc=size={resolution}:rate={fps}'])

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

    def _start_ffmpeg(self, url: str, url_type: int, quality: int, video_type: str):
        """启动 ffmpeg 推流进程"""
        self._stop_ffmpeg()

        cmd = self._build_ffmpeg_cmd(url, url_type, quality, video_type)
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

        except FileNotFoundError:
            logger.error("ffmpeg not found. Please install ffmpeg first.")
            self._live_streaming = False
        except Exception as e:
            logger.error(f"Failed to start ffmpeg: {e}")
            self._live_streaming = False

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
        data = msg.get('data', {})
        url_type = data.get('url_type', 4)
        url = data.get('url', '')
        video_id = data.get('video_id', '')
        video_quality = data.get('video_quality', 0)

        if not url:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="URL required")

        if self._live_streaming:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Already streaming")

        self._live_streaming = True
        self._live_session_id = str(int(time.time() * 1000))
        self._live_protocol = url_type
        self._live_quality = video_quality
        self._live_video_id = video_id
        self._live_url = url

        logger.info(f"Live start: url_type={url_type}, video_id={video_id}, "
                   f"quality={video_quality}, url={url}")

        # 启动 ffmpeg 推流
        self._start_ffmpeg(url, url_type, video_quality, self._live_video_type)

        return MessageBuilder.build_reply(msg, result=0, data={
            'session_id': self._live_session_id
        })

    def handle_live_stop(self, msg: dict) -> dict:
        """
        停止直播 (live_stop_push)

        文档格式：
        Request: { video_id: '{sn}/{camera_index}/{video_index}' }
        """
        data = msg.get('data', {})
        video_id = data.get('video_id', '')

        if not self._live_streaming:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Not streaming")

        logger.info(f"Live stop: video_id={video_id}")

        # 停止 ffmpeg 推流
        self._stop_ffmpeg()

        self._live_streaming = False
        self._live_session_id = ""
        self._live_video_id = ""
        self._live_url = ""

        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_livestream_lens(self, msg: dict) -> dict:
        """
        切换直播镜头 (live_lens_change)

        文档格式：
        Request: {
            video_id: '{sn}/{camera_index}/{video_index}',
            video_type: 'ir'/'normal'/'wide'/'zoom'
        }
        """
        data = msg.get('data', {})
        video_id = data.get('video_id', '')
        video_type = data.get('video_type', 'normal')

        logger.info(f"Livestream lens change: video_id={video_id}, type={video_type}")

        self._live_video_type = video_type

        # 如果正在推流，重启 ffmpeg 切换视频源
        if self._live_streaming and self._live_url:
            self._start_ffmpeg(self._live_url, self._live_protocol, self._live_quality, video_type)

        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_livestream_quality(self, msg: dict) -> dict:
        """
        设置直播画质 (live_set_quality)

        文档格式：
        Request: {
            video_id: '{sn}/{camera_index}/{video_index}',
            video_quality: 0=自适应, 1=流畅, 2=标清, 3=高清, 4=超清
        }
        """
        data = msg.get('data', {})
        video_id = data.get('video_id', '')
        video_quality = data.get('video_quality', 0)

        self._live_quality = video_quality
        logger.info(f"Livestream quality set: video_id={video_id}, quality={video_quality}")

        # 如果正在推流，重启 ffmpeg 调整画质
        if self._live_streaming and self._live_url:
            self._start_ffmpeg(self._live_url, self._live_protocol, video_quality, self._live_video_type)

        return MessageBuilder.build_reply(msg, result=0)

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
        media_type = data.get('media_type', 'all')
        page = data.get('page', 1)
        page_size = data.get('page_size', 20)

        logger.info(f"File list get: type={media_type}, page={page}, size={page_size}")

        # TODO: 扫描存储目录获取文件列表

        return MessageBuilder.build_reply(msg, result=0, data={
            'total': 0,
            'page': page,
            'page_size': page_size,
            'files': []
        })

    def handle_file_delete(self, msg: dict) -> dict:
        """
        文件删除

        Request: { file_ids: [...] }
        """
        data = msg.get('data', {})
        file_ids = data.get('file_ids', [])
        logger.info(f"File delete: {len(file_ids)} files")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_live_capability_query(self, msg: dict) -> dict:
        """
        直播能力查询

        Request: { }
        """
        logger.info("Live capability query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'supported_protocols': ['webrtc', 'rtmp', 'rtsp'],
            'supported_resolutions': ['720p', '1080p', '4k'],
            'supported_video_sources': ['wide', 'zoom', 'ir', 'fpv'],
            'max_bitrate': 8000,
            'max_framerate': 60
        })

    def handle_highest_priority_upload_flighttask_media(self, msg: dict) -> dict:
        """
        最高优先级上传任务媒体

        Request: { flighttask_id }
        """
        data = msg.get('data', {})
        flighttask_id = data.get('flighttask_id', '')
        logger.info(f"Highest priority upload: {flighttask_id}")
        self.report_priority_media(flighttask_id)
        return MessageBuilder.build_reply(msg, result=0)

    def handle_upload_flighttask_media_prioritize(self, msg: dict) -> dict:
        """
        上传飞行任务媒体优先化 (upload_flighttask_media_prioritize)

        Request: { flighttask_id: str }
        Response: { result }
        """
        data = msg.get('data', {})
        flighttask_id = data.get('flighttask_id', '')
        logger.info(f"Upload flighttask media prioritize: {flighttask_id}")
        # TODO: 将指定任务的媒体设为最高上传优先级
        return MessageBuilder.build_reply(msg, result=0)

    def handle_live_lens_change(self, msg: dict) -> dict:
        """Live lens change (live_lens_change) - alias of set_livestream_lens"""
        return self.handle_set_livestream_lens(msg)

    def handle_live_set_quality(self, msg: dict) -> dict:
        """Live set quality (live_set_quality) - alias of set_livestream_quality"""
        return self.handle_set_livestream_quality(msg)

