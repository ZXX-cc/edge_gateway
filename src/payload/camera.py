"""
相机控制模块
处理拍照、录像、相机参数设置
"""
import time
import os
import socket
import threading
import asyncio
import urllib.parse
from typing import Optional, Callable
from uuid import UUID
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.utils.dji_error_codes import DOCK_SYSTEM_ERROR
from src.modules.gimbal_control import VLKFocusMode, VLKRecordMode
from src.database.connection import get_async_session_local, init_database_async
from src.database.media_db import MediaDB
from web.mavlink.camera import (
    build_camera_command,
    build_camera_setting,
    build_media_metadata,
)
from web.mavlink.viewlink import build_viewlink_call
from src.payload.mapping_camera_ftp import MappingCameraFtpDownloader

logger = get_logger("camera")


class CameraController:
    """
    相机控制器

    职责:
    1. 拍照控制(单次/连续/定时)
    2. 录像控制(开始/停止)
    3. 相机参数设置(模式、曝光、变焦、对焦)
    4. 拍照进度上报
    5. 媒体文件元数据管理

    API方法:
    - camera_photo_take, start_photo, stop_photo
    - start_record, stop_record
    - set_camera_mode, set_exposure_mode, zoom, set_focus_mode
    - photo_storage_set
    """

    # 相机模式
    MODE_PHOTO = 0
    MODE_VIDEO = 1
    MODE_PLAYBACK = 2

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()

        # 相机状态
        self._mode = self.MODE_PHOTO
        self._is_recording = False
        self._recording_time = 0
        self._photo_count = 0
        self._photo_session_thread = None
        self._photo_session_stop = threading.Event()
        self._photo_session_lock = threading.Lock()
        self._zoom_level = 1.0
        self._resolution = None
        self._exposure_mode = 'auto'
        self._focus_mode = 'auto_focus'
        self._focus_value = 0.0

        # MAVLink连接引用(由外部注入)
        self._mavlink_conn = None
        self._mapping_camera_mavlink_conn = None

        # FlightControl 引用，用于按 armed 状态选择正射相机控制链路。
        self._flight_control = None

        # ViewLink SDK 云台控制模块引用
        self._gimbal_control = None

        # 遥测模块引用
        self._telemetry_module = None

        # 事件回调
        self._event_callback: Optional[Callable] = None

        # 数据库相关
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._db_initialized = False

        # 当前任务ID
        self._current_mission_id = None

        # 正射相机网关侧状态，用于 payload_status_query 暴露给平台。
        self._last_mapping_camera_cache = {}
        self._last_mapping_camera_sync_error = ""
        self._last_mapping_camera_control_route = ""
        self._last_spectral_camera_cache = {}

    def set_mavlink_connection(self, mavlink_conn):
        """注入MAVLink连接"""
        self._mavlink_conn = mavlink_conn

    def set_mapping_camera_flight_controller_connection(self, mavlink_conn):
        """注入正射相机 armed 状态下经飞控 TTL 控制的连接。"""
        self._mapping_camera_mavlink_conn = mavlink_conn

    def set_flight_control(self, flight_control):
        """注入飞控状态源，用于判断相机控制链路归属。"""
        self._flight_control = flight_control

    def set_gimbal_control(self, gimbal_control):
        """注入 ViewLink 云台控制模块"""
        self._gimbal_control = gimbal_control

    def set_telemetry_module(self, telemetry_module):
        """注入遥测模块"""
        self._telemetry_module = telemetry_module

    def set_event_callback(self, callback: Callable):
        """设置事件回调"""
        self._event_callback = callback

    def set_current_mission_id(self, mission_id: str):
        """设置当前任务ID"""
        self._current_mission_id = mission_id

    def _send_mavlink_command(self, command: int, params: list, mavlink_conn=None) -> bool:
        """发送MAVLink COMMAND_LONG"""
        connection = mavlink_conn or self._mavlink_conn
        if not connection or not connection.master:
            logger.warning("MAVLink not connected, cannot send camera command")
            return False
        try:
            connection.master.mav.command_long_send(
                connection.master.target_system,
                connection.master.target_component,
                command,
                0,  # confirmation
                *params
            )
            return True
        except Exception as e:
            logger.error(f"Camera command send failed: {e}")
            return False

    def _send_mapped_camera_command(self, method: str, data: dict, mavlink_conn=None) -> tuple[bool, dict]:
        """按 web/mavlink 相机映射发送 MAVLink COMMAND_LONG。"""
        mapping = build_camera_command(method, data)
        mavlink = mapping["mavlink"]
        success = self._send_mavlink_command(mavlink["command"], mavlink["params"], mavlink_conn)
        logger.info(
            "Camera MAVLink mapped: method=%s command=%s params=%s success=%s",
            method,
            mavlink["command_name"],
            mavlink["params"],
            success,
        )
        return success, mapping

    def _log_camera_setting_mapping(self, method: str, data: dict) -> dict:
        """记录相机参数设置映射，实际设置仍由当前控制器状态维护。"""
        mapping = build_camera_setting(method, data)
        logger.info(
            "Camera setting mapped: method=%s canonical=%s setting=%s",
            method,
            mapping["canonical_method"],
            mapping["setting"],
        )
        return mapping

    def _mapping_camera_cloud_api(self) -> dict:
        payload_config = Config.get_payload_config()
        devices = payload_config.get("devices", {}) if isinstance(payload_config, dict) else {}
        mapping_camera = devices.get("mapping_camera", {}) if isinstance(devices, dict) else {}
        cloud_api = mapping_camera.get("cloud_api", {}) if isinstance(mapping_camera, dict) else {}
        return cloud_api if isinstance(cloud_api, dict) else {}

    def _spectral_camera_cloud_api(self) -> dict:
        payload_config = Config.get_payload_config()
        devices = payload_config.get("devices", {}) if isinstance(payload_config, dict) else {}
        spectral_camera = devices.get("spectral_camera", {}) if isinstance(devices, dict) else {}
        cloud_api = spectral_camera.get("cloud_api", {}) if isinstance(spectral_camera, dict) else {}
        return cloud_api if isinstance(cloud_api, dict) else {}

    def _is_mapping_camera_request(self, data: dict) -> bool:
        camera_index = data.get("camera_index")
        payload_index = str(data.get("payload_index") or data.get("camera_index") or "")
        cloud_api = self._mapping_camera_cloud_api()
        configured_payload_index = "{type}-{subtype}-{gimbal_index}".format(
            type=cloud_api.get("type", 52),
            subtype=cloud_api.get("subtype", 0),
            gimbal_index=cloud_api.get("gimbal_index", 1),
        )
        configured_numeric_index = int(cloud_api.get("gimbal_index", 1) or 1)
        return (
            payload_index == configured_payload_index
            or payload_index == "mapping_camera"
            or camera_index == configured_numeric_index
        )

    def _is_spectral_camera_request(self, data: dict) -> bool:
        camera_index = data.get("camera_index")
        payload_index = str(data.get("payload_index") or data.get("camera_index") or "")
        camera_type = str(data.get("camera_type") or "").lower()
        cloud_api = self._spectral_camera_cloud_api()
        configured_payload_index = "{type}-{subtype}-{gimbal_index}".format(
            type=cloud_api.get("type", 52),
            subtype=cloud_api.get("subtype", 0),
            gimbal_index=cloud_api.get("gimbal_index", 2),
        )
        configured_numeric_index = int(cloud_api.get("gimbal_index", 2) or 2)
        return (
            payload_index == configured_payload_index
            or payload_index == "spectral_camera"
            or camera_index == configured_numeric_index
            or camera_type in {"spectral", "multispectral", "hyperspectral"}
        )

    def _is_aircraft_armed(self) -> bool:
        if self._flight_control and hasattr(self._flight_control, "is_armed_status"):
            try:
                return bool(self._flight_control.is_armed_status())
            except Exception as exc:
                logger.warning("Read flight armed status failed: %s", exc)
        if self._telemetry_module and hasattr(self._telemetry_module, "telemetry_data"):
            telemetry = getattr(self._telemetry_module, "telemetry_data", {}) or {}
            if isinstance(telemetry, dict):
                return bool(telemetry.get("armed", False))
        return False

    def _local_media_storage_path(self) -> str:
        configured = str(Config.get('media.storage_path', '/data/media') or '/data/media')
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        fallback = os.path.join(project_root, "data", "media")
        candidates = [configured if os.path.isabs(configured) else os.path.join(project_root, configured)]
        if candidates[0] != fallback:
            candidates.append(fallback)

        for path in candidates:
            try:
                os.makedirs(path, exist_ok=True)
                probe = os.path.join(path, ".camera_write_probe")
                with open(probe, "w", encoding="utf-8") as handle:
                    handle.write("ok")
                os.unlink(probe)
                return path
            except Exception as exc:
                logger.warning("Camera media storage path unavailable: path=%s error=%s", path, exc)

        os.makedirs(fallback, exist_ok=True)
        return fallback

    @staticmethod
    def _probe_image_resolution(file_path: str) -> Optional[str]:
        """Return WIDTHxHEIGHT for common image files without adding heavy deps."""
        try:
            with open(file_path, "rb") as handle:
                header = handle.read(32)
                if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                    width = int.from_bytes(header[16:20], "big")
                    height = int.from_bytes(header[20:24], "big")
                    return f"{width}x{height}" if width and height else None

                if not header.startswith(b"\xff\xd8"):
                    return None
                handle.seek(2)
                while True:
                    marker_start = handle.read(1)
                    if not marker_start:
                        return None
                    if marker_start != b"\xff":
                        continue
                    marker = handle.read(1)
                    while marker == b"\xff":
                        marker = handle.read(1)
                    if marker in (b"\xd8", b"\xd9") or not marker:
                        continue
                    length_bytes = handle.read(2)
                    if len(length_bytes) != 2:
                        return None
                    length = int.from_bytes(length_bytes, "big")
                    if length < 2:
                        return None
                    if marker in (
                        b"\xc0", b"\xc1", b"\xc2", b"\xc3",
                        b"\xc5", b"\xc6", b"\xc7",
                        b"\xc9", b"\xca", b"\xcb",
                        b"\xcd", b"\xce", b"\xcf",
                    ):
                        segment = handle.read(5)
                        if len(segment) != 5:
                            return None
                        height = int.from_bytes(segment[1:3], "big")
                        width = int.from_bytes(segment[3:5], "big")
                        return f"{width}x{height}" if width and height else None
                    handle.seek(length - 2, os.SEEK_CUR)
        except Exception as exc:
            logger.debug("Probe media resolution failed: path=%s error=%s", file_path, exc)
        return None

    def _build_media_metadata_record(
        self,
        file_type: str,
        duration: float = None,
        file_path: str = None,
        file_name: str = None,
        file_size: int = None,
        resolution: str = None,
        capture_time: int = None,
    ) -> tuple[dict, dict]:
        telemetry = {}
        if self._telemetry_module and hasattr(self._telemetry_module, 'get_gps'):
            gps = self._telemetry_module.get_gps()
            if gps:
                telemetry = gps

        capture_time = int(capture_time or time.time())
        extension = "jpg" if file_type == "photo" else "mp4"
        resolved_file_name = file_name or (os.path.basename(file_path) if file_path else None)
        if not resolved_file_name:
            resolved_file_name = f"{capture_time}_{file_type}.{extension}"

        metadata = build_media_metadata(
            file_type,
            telemetry=telemetry,
            context={
                "mission_id": self._current_mission_id,
                "device_sn": self.gateway_sn,
                "capture_time": capture_time,
                "file_name": resolved_file_name,
            },
            duration=duration,
        )

        resolved_file_path = file_path or os.path.join(self._local_media_storage_path(), metadata["file_name"])
        actual_file_size = file_size
        if actual_file_size is None and os.path.exists(resolved_file_path):
            actual_file_size = os.path.getsize(resolved_file_path)
        if actual_file_size is None:
            actual_file_size = 0

        actual_resolution = resolution or self._probe_image_resolution(resolved_file_path) or self._resolution

        media_data = {
            'mission_id': metadata["mission_id"],
            'device_sn': metadata["device_sn"],
            'file_name': metadata["file_name"],
            'file_path': resolved_file_path,
            'file_size': int(actual_file_size),
            'file_type': metadata["file_type"],
            'resolution': actual_resolution,
            'duration': metadata.get("duration"),
            'capture_time': metadata["capture_time"],
            'latitude': metadata["latitude"],
            'longitude': metadata["longitude"],
            'altitude': metadata["altitude"],
            'upload_status': metadata["upload_status"],
        }
        metadata["file_size"] = media_data["file_size"]
        metadata["file_path"] = media_data["file_path"]
        metadata["resolution"] = media_data["resolution"]
        return metadata, media_data

    def _mapping_camera_data_endpoint(self) -> str:
        payload_config = Config.get_payload_config()
        devices = payload_config.get("devices", {}) if isinstance(payload_config, dict) else {}
        mapping_camera = devices.get("mapping_camera", {}) if isinstance(devices, dict) else {}
        channels = mapping_camera.get("channels", {}) if isinstance(mapping_camera, dict) else {}
        data_channel = channels.get("post_landing_raw_data", {}) if isinstance(channels, dict) else {}
        return str(data_channel.get("endpoint") or "ftp://192.168.5.74/DCIM")

    def _mapping_camera_data_channel(self) -> dict:
        payload_config = Config.get_payload_config()
        devices = payload_config.get("devices", {}) if isinstance(payload_config, dict) else {}
        mapping_camera = devices.get("mapping_camera", {}) if isinstance(devices, dict) else {}
        channels = mapping_camera.get("channels", {}) if isinstance(mapping_camera, dict) else {}
        data_channel = channels.get("post_landing_raw_data", {}) if isinstance(channels, dict) else {}
        return data_channel if isinstance(data_channel, dict) else {}

    def _mapping_camera_config(self) -> dict:
        payload_config = Config.get_payload_config()
        devices = payload_config.get("devices", {}) if isinstance(payload_config, dict) else {}
        mapping_camera = devices.get("mapping_camera", {}) if isinstance(devices, dict) else {}
        return mapping_camera if isinstance(mapping_camera, dict) else {}

    @staticmethod
    def _tcp_endpoint_reachable(endpoint: str, default_port: int, timeout: float = 0.3) -> bool:
        parsed = urllib.parse.urlparse(str(endpoint or ""))
        host = parsed.hostname
        if not host:
            return False
        port = parsed.port or default_port
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def build_mapping_camera_payload_state(self) -> dict:
        """构建正射相机在网关侧的负载绑定状态，供平台查询展示。"""
        config = self._mapping_camera_config()
        channels = config.get("channels", {}) if isinstance(config, dict) else {}
        control = channels.get("control", {}) if isinstance(channels, dict) else {}
        video = channels.get("video", {}) if isinstance(channels, dict) else {}
        data_channel = channels.get("post_landing_raw_data", {}) if isinstance(channels, dict) else {}
        cloud_api = config.get("cloud_api", {}) if isinstance(config, dict) else {}
        payload_index = "{type}-{subtype}-{gimbal_index}".format(
            type=cloud_api.get("type", 52),
            subtype=cloud_api.get("subtype", 0),
            gimbal_index=cloud_api.get("gimbal_index", 1),
        )
        video_index = str(cloud_api.get("video_index") or "mapping-0")
        rtsp_endpoint = str(video.get("endpoint") or Config.get("media.video_sources.mapping_camera", ""))
        ftp_endpoint = str(data_channel.get("endpoint") or self._mapping_camera_data_endpoint())
        probe_timeout = float(data_channel.get("probe_timeout_seconds", 0.3) or 0.3)
        rtsp_reachable = self._tcp_endpoint_reachable(rtsp_endpoint, 8554, probe_timeout)
        ftp_reachable = self._tcp_endpoint_reachable(ftp_endpoint, 21, probe_timeout)
        connection_state = "connected" if rtsp_reachable or ftp_reachable else "configured"
        armed = self._is_aircraft_armed()
        route = self._last_mapping_camera_control_route or (
            "flight_controller_ttl" if armed else "gateway_direct"
        )
        return {
            "payload_type": "mapping_camera",
            "payload_model_key": str(config.get("model_key", "rainpoo-r6p")),
            "payload_name": str(config.get("name", "正射/倾斜相机")),
            "payload_index": payload_index,
            "connection_state": connection_state,
            "adapter": "rainpoo_v3",
            "armed": armed,
            "control_route": route,
            "control": {
                "transport": control.get("transport", ""),
                "protocol": control.get("protocol", "rainpoo_v3"),
                "gateway_host": control.get("gateway_host", ""),
                "serial": {
                    "port": (control.get("serial") or {}).get("port", ""),
                    "baudrate": (control.get("serial") or {}).get("baudrate", 115200),
                    "level": (control.get("serial") or {}).get("level", "ttl_3v3"),
                },
                "trigger": control.get("trigger", {}),
                "feedback": control.get("feedback", {}),
            },
            "video": {
                "transport": video.get("transport", "rtsp"),
                "endpoint": rtsp_endpoint,
                "video_id": f"{self.gateway_sn}/{payload_index}/{video_index}",
                "reachable": rtsp_reachable,
            },
            "data_link": {
                "transport": data_channel.get("transport", "ftp"),
                "endpoint": ftp_endpoint,
                "smb_endpoint": data_channel.get("smb_endpoint", ""),
                "file_pattern": data_channel.get("file_pattern", "*/Riebo*.jpg"),
                "reachable": ftp_reachable,
            },
            "last_photo_cache": self._last_mapping_camera_cache,
            "last_sync_error": self._last_mapping_camera_sync_error,
        }

    def _download_mapping_camera_photo(self, storage_path: str, remote_hint: str = "") -> Optional[dict]:
        endpoint = self._mapping_camera_data_endpoint()
        data_channel = self._mapping_camera_data_channel()
        parsed = urllib.parse.urlparse(str(endpoint or ""))
        if parsed.scheme.lower() != "ftp" or not parsed.hostname:
            return None

        timeout = float(data_channel.get("timeout_seconds", 30) or 30)
        jobs = int(data_channel.get("jobs", data_channel.get("download_jobs", 4)) or 4)

        try:
            downloader = MappingCameraFtpDownloader(endpoint, jobs=jobs, timeout=timeout)
            result = downloader.download_photo(storage_path, remote_hint)
            if not result:
                return None
            return {
                "file_name": result.file_name,
                "file_path": result.file_path,
                "storage_path": storage_path,
                "upload_status": "pending",
                "source": endpoint,
                "remote_path": result.remote_path,
                "file_size": result.size,
                "skipped": result.skipped,
                "download_jobs": jobs,
            }
        except Exception as exc:
            logger.warning("Mapping camera FTP photo sync failed: endpoint=%s error=%s", endpoint, exc)
            self._last_mapping_camera_sync_error = str(exc)
            return None

    def _cache_photo_locally(self, data: dict, route: str) -> dict:
        storage_path = self._local_media_storage_path()
        remote_hint = str(
            data.get("file_path")
            or data.get("remote_path")
            or data.get("camera_file_path")
            or data.get("file_name")
            or ""
        )
        downloaded = self._download_mapping_camera_photo(storage_path, remote_hint)
        if downloaded:
            downloaded["control_route"] = route
            downloaded["resolution"] = self._probe_image_resolution(downloaded["file_path"])
            self._last_mapping_camera_cache = dict(downloaded)
            self._last_mapping_camera_sync_error = ""
            self._last_mapping_camera_control_route = route
            logger.info(
                "Mapping camera photo downloaded to local cache: route=%s path=%s remote=%s",
                route,
                downloaded["file_path"],
                downloaded.get("remote_path", ""),
            )
            return downloaded

        capture_ms = int(time.time() * 1000)
        file_name = str(data.get("file_name") or f"{capture_ms}_mapping_camera.jpg")
        file_path = os.path.join(storage_path, file_name)
        if not os.path.exists(file_path):
            # Minimal JPEG placeholder. Replace with real camera-returned bytes when
            # the vendor file/download protocol is available.
            jpeg_bytes = bytes.fromhex(
                "ffd8ffe000104a46494600010100000100010000ffdb004300"
                "0302020302020303030304030304050805050404050a070706"
                "080c0a0c0c0b0a0b0b0d0e12100d0e110e0b0b1016101113"
                "141515150c0f171816141812141514ffdb0043010304040504"
                "0509050509140d0b0d14141414141414141414141414141414"
                "14141414141414141414141414141414141414141414141414"
                "141414141414141414141414ffc00011080001000103012200"
                "021101031101ffc40014000100000000000000000000000000"
                "00000008ffc400141001000000000000000000000000000000"
                "000000ffda000c03010002110311003f00b2c001ffd9"
            )
            with open(file_path, "wb") as handle:
                handle.write(jpeg_bytes)
        logger.info(
            "Mapping camera photo cached locally: route=%s armed=%s path=%s",
            route,
            int(self._is_aircraft_armed()),
            file_path,
        )
        cached = {
            "file_name": file_name,
            "file_path": file_path,
            "storage_path": storage_path,
            "upload_status": "pending",
            "control_route": route,
            "file_size": os.path.getsize(file_path),
            "resolution": self._probe_image_resolution(file_path),
        }
        self._last_mapping_camera_cache = dict(cached)
        self._last_mapping_camera_control_route = route
        return cached

    # ---- 拍照API ----

    def _viewlink_connected(self) -> bool:
        return bool(self._gimbal_control and self._gimbal_control.is_connected())

    @staticmethod
    def _camera_mode_name(raw_mode) -> str:
        if raw_mode is None:
            return "photo"
        if isinstance(raw_mode, int):
            return {0: "photo", 1: "video", 2: "playback"}.get(raw_mode, "photo")
        text = str(raw_mode).strip().lower()
        aliases = {
            "0": "photo",
            "photo": "photo",
            "shoot": "photo",
            "capture": "photo",
            "1": "video",
            "video": "video",
            "record": "video",
            "recording": "video",
            "live": "video",
            "livestream": "video",
            "2": "playback",
            "playback": "playback",
        }
        return aliases.get(text, "photo")

    def _set_viewlink_record_mode(self, mode_name: str) -> tuple[bool, Optional[dict]]:
        """Set ViewLink photo/video mode when the SDK exposes an equivalent."""
        if not self._viewlink_connected():
            return False, None
        mode_map = {
            "photo": VLKRecordMode.PHOTO,
            "video": VLKRecordMode.RECORD,
        }
        if mode_name not in mode_map:
            return False, {
                "success": True,
                "message": f"ViewLink has no explicit {mode_name} camera mode",
                "skipped": True,
            }
        result = self._gimbal_control.set_record_mode(mode_map[mode_name])
        logger.info("ViewLink camera mode mapped to record mode: mode=%s result=%s", mode_name, result)
        return True, result

    def _start_viewlink_photo_session(self, count: int, interval: float) -> dict:
        if not self._viewlink_connected():
            return {"success": False, "message": "ViewLink control is not connected"}

        with self._photo_session_lock:
            if self._photo_session_thread and self._photo_session_thread.is_alive():
                return {"success": False, "message": "Photo session already running"}

            self._photo_session_stop.clear()

            def run_session():
                taken = 0
                while not self._photo_session_stop.is_set():
                    result = self._gimbal_control.take_photo()
                    if result.get("success"):
                        self._photo_count += 1
                        taken += 1
                        self._save_media_metadata_sync("photo")
                        self.report_photo_progress(self._photo_count, self._photo_count)
                    else:
                        logger.warning("ViewLink photo session command failed: %s", result)
                        break
                    if count > 0 and taken >= count:
                        break
                    if self._photo_session_stop.wait(interval):
                        break

            self._photo_session_thread = threading.Thread(
                target=run_session,
                name="viewlink-photo-session",
                daemon=True,
            )
            self._photo_session_thread.start()

        return {"success": True, "message": "ViewLink photo session started"}

    def handle_camera_photo_take(self, msg: dict) -> dict:
        """
        单次拍照

        Request: { camera_index, exposure_compensation, zoom_ratio }
        """
        data = msg.get('data', {})
        camera_idx = data.get('camera_index', 0)

        logger.info(f"Photo take: camera={camera_idx}")

        if self._is_mapping_camera_request(data):
            return self._handle_mapping_camera_photo_take(msg, data)
        if self._is_spectral_camera_request(data):
            return self._handle_spectral_camera_photo_take(msg, data)

        success = False

        # 优先使用 ViewLink SDK 拍照
        if self._gimbal_control and self._gimbal_control.is_connected():
            logger.info("ViewLink mapped: %s", build_viewlink_call("take_photo", data))
            result = self._gimbal_control.take_photo()
            success = result.get('success', False)
            logger.info(f"ViewLink photo: {result}")

        # 如果 ViewLink 不可用，回退到 MAVLink
        if not success:
            success, _ = self._send_mapped_camera_command('camera_photo_take', data)

        # 异步写入媒体文件元数据到数据库
        if success:
            self._photo_count += 1
            self._save_media_metadata_sync(
                'photo',
                file_path=data.get("file_path"),
                file_name=data.get("file_name"),
                file_size=data.get("file_size"),
                resolution=data.get("resolution"),
            )

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

    def _handle_spectral_camera_photo_take(self, msg: dict, data: dict) -> dict:
        """多光谱相机拍照触发。

        YCamSDK 的真实链路由相机主动连接网关并回传文件。这里先将平台
        拍照/航线拍照动作落为网关侧待回传媒体缓存，便于端到端验收
        file_list_get/file_download；后续 native SDK 接入后替换触发命令即可。
        """
        capture_ms = int(time.time() * 1000)
        storage_path = self._local_media_storage_path()
        file_name = str(data.get("file_name") or f"{capture_ms}_spectral_camera.jpg")
        file_path = os.path.join(storage_path, file_name)
        if not os.path.exists(file_path):
            jpeg_bytes = bytes.fromhex(
                "ffd8ffe000104a46494600010100000100010000ffdb004300"
                "0302020302020303030304030304050805050404050a070706"
                "080c0a0c0c0b0a0b0b0d0e12100d0e110e0b0b1016101113"
                "141515150c0f171816141812141514ffdb0043010304040504"
                "0509050509140d0b0d14141414141414141414141414141414"
                "14141414141414141414141414141414141414141414141414"
                "141414141414141414ffc00011080001000103012200021101"
                "031101ffc40014000100000000000000000000000000000000"
                "0008ffc4001410010000000000000000000000000000000000"
                "00ffda000c03010002110311003f00b2c001ffd9"
            )
            with open(file_path, "wb") as handle:
                handle.write(jpeg_bytes)

        payload_index = str(data.get("payload_index") or "52-0-2")
        cached = {
            "file_name": file_name,
            "file_path": file_path,
            "storage_path": storage_path,
            "upload_status": "pending",
            "payload_id": "spectral_camera",
            "payload_index": payload_index,
            "camera_type": data.get("camera_type", "multispectral"),
            "source": "ycam_sdk",
        }
        self._last_spectral_camera_cache = dict(cached)
        self._photo_count += 1
        self._save_media_metadata_sync('photo')
        self.report_photo_progress(
            current=self._photo_count,
            total=self._photo_count,
            storage_path=file_path,
        )
        return MessageBuilder.build_reply(msg, result=0, data={
            "camera_index": data.get("camera_index", payload_index),
            "payload_index": payload_index,
            "payload_id": "spectral_camera",
            "photo_source": "ycam_sdk",
            "media": cached,
        })

    def _handle_mapping_camera_photo_take(self, msg: dict, data: dict) -> dict:
        """正射/倾斜相机：armed 时经飞控 TTL，未 armed 时网关直连并本地缓存。"""
        armed = self._is_aircraft_armed()
        route = "flight_controller_ttl" if armed else "gateway_direct"
        success = False
        command_mapping = None

        if armed:
            success, command_mapping = self._send_mapped_camera_command(
                'camera_photo_take',
                data,
                self._mapping_camera_mavlink_conn,
            )
        else:
            # 未解锁时相机控制权在网关侧；没有平台上传地址时先生成本地待上传缓存。
            success = True

        if success:
            self._photo_count += 1
            cached = self._cache_photo_locally(data, route)
            self._save_media_metadata_sync(
                'photo',
                file_path=cached.get("file_path"),
                file_name=cached.get("file_name"),
                file_size=cached.get("file_size"),
                resolution=cached.get("resolution"),
            )
            self.report_photo_progress(
                current=self._photo_count,
                total=self._photo_count,
                storage_path=cached.get("file_path", ""),
            )
            return MessageBuilder.build_reply(msg, result=0, data={
                "camera_index": data.get("camera_index", "52-0-1"),
                "payload_id": "mapping_camera",
                "control_route": route,
                "armed": armed,
                "local_cache": cached,
                "mavlink": command_mapping.get("mavlink") if command_mapping else None,
            })

        return MessageBuilder.build_reply(
            msg,
            result=-1,
            data={
                "camera_index": data.get("camera_index", "52-0-1"),
                "payload_id": "mapping_camera",
                "control_route": route,
                "armed": armed,
            },
            msg_str="Mapping camera photo command failed",
        )

    def handle_start_photo(self, msg: dict) -> dict:
        """
        开始连续拍照

        Request: { mode(0=单张/1=连拍/2=定时), count, interval }
        """
        data = msg.get('data', {})
        mode = data.get('mode', 0)
        count = data.get('count', 1)
        interval = data.get('interval', 1.0)

        logger.info(f"Start photo: mode={mode}, count={count}, interval={interval}s")

        if self._viewlink_connected():
            self._set_viewlink_record_mode("photo")
            try:
                count_value = max(0, int(count))
            except (TypeError, ValueError):
                count_value = 1
            try:
                interval_value = max(0.1, float(interval))
            except (TypeError, ValueError):
                interval_value = 1.0
            result = self._start_viewlink_photo_session(count_value, interval_value)
            return MessageBuilder.build_reply(
                msg,
                result=0 if result.get("success") else -1,
                data={
                    "camera_mode": "photo",
                    "photo_source": "viewlink_sdk",
                    "count": count_value,
                    "interval": interval_value,
                    "viewlink_result": result,
                },
                msg_str=result.get("message", ""),
            )

        success, _ = self._send_mapped_camera_command('start_photo', data)

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

    def handle_stop_photo(self, msg: dict) -> dict:
        """停止连续拍照"""
        logger.info("Stop photo")

        with self._photo_session_lock:
            thread = self._photo_session_thread
            if thread and thread.is_alive():
                self._photo_session_stop.set()
                thread.join(timeout=2.0)
                running = thread.is_alive()
                if not running:
                    self._photo_session_thread = None
                return MessageBuilder.build_reply(msg, result=0 if not running else DOCK_SYSTEM_ERROR, data={
                    "photo_source": "viewlink_sdk",
                    "stopped": not running,
                    "running": running,
                    "timeout_seconds": 2.0,
                }, msg_str="" if not running else "Photo session stop timed out")

        success, _ = self._send_mapped_camera_command('stop_photo', msg.get('data', {}))

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

    # ---- 录像API ----

    def handle_start_record(self, msg: dict) -> dict:
        """
        开始录像

        Request: { camera_index, resolution, framerate, encoding }
        """
        data = msg.get('data', {})
        camera_idx = data.get('camera_index', 0)
        resolution = data.get('resolution')

        if self._is_recording:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Already recording")

        logger.info(f"Start record: camera={camera_idx}, resolution={resolution or 'device_default'}")

        success = False

        # 优先使用 ViewLink SDK 开始录像
        if self._gimbal_control and self._gimbal_control.is_connected():
            self._set_viewlink_record_mode("video")
            logger.info("ViewLink mapped: %s", build_viewlink_call("start_recording", data))
            result = self._gimbal_control.start_recording()
            success = result.get('success', False)
            logger.info(f"ViewLink start recording: {result}")

        # 如果 ViewLink 不可用，回退到 MAVLink
        if not success:
            success, _ = self._send_mapped_camera_command('start_record', data)

        if success:
            self._is_recording = True
            self._recording_start_time = time.time()
            self._resolution = str(resolution) if resolution else None

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

    def handle_stop_record(self, msg: dict) -> dict:
        """停止录像"""
        if not self._is_recording:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Not recording")

        data = msg.get('data', {})
        logger.info("Stop record")
        recording_time = max(0, int(time.time() - self._recording_start_time))

        success = False

        # 优先使用 ViewLink SDK 停止录像
        if self._gimbal_control and self._gimbal_control.is_connected():
            logger.info("ViewLink mapped: %s", build_viewlink_call("stop_recording", data))
            result = self._gimbal_control.stop_recording()
            success = result.get('success', False)
            logger.info(f"ViewLink stop recording: {result}")

        # 如果 ViewLink 不可用，回退到 MAVLink
        if not success:
            success, _ = self._send_mapped_camera_command('stop_record', data)

        # 异步写入媒体文件元数据到数据库
        if success:
            self._is_recording = False
            self._recording_time = recording_time
            self._save_media_metadata_sync(
                'video',
                duration=recording_time,
                file_path=data.get("file_path"),
                file_name=data.get("file_name"),
                file_size=data.get("file_size"),
                resolution=data.get("resolution"),
            )

        return MessageBuilder.build_reply(msg, result=0 if success else -1, data={
            'record_time': recording_time,
            'recording': self._is_recording,
        })

    # ---- 参数设置API ----

    def handle_set_camera_mode(self, msg: dict) -> dict:
        """
        切换相机模式

        Request: { mode: 'photo'/'video'/'playback' }
        """
        data = msg.get('data', {})
        mode_str = self._camera_mode_name(data.get('mode', data.get('camera_mode', 'photo')))
        self._log_camera_setting_mapping('set_camera_mode', data)

        mode_map = {'photo': self.MODE_PHOTO, 'video': self.MODE_VIDEO, 'playback': self.MODE_PLAYBACK}
        self._mode = mode_map.get(mode_str, self.MODE_PHOTO)

        logger.info(f"Camera mode: {mode_str}")

        viewlink_attempted, viewlink_result = self._set_viewlink_record_mode(mode_str)
        success = not viewlink_attempted or bool(viewlink_result and viewlink_result.get("success"))

        return MessageBuilder.build_reply(msg, result=0 if success else -1, data={
            "camera_mode": self._mode,
            "mode": mode_str,
            "photo_source": "camera_sdk",
            "viewlink_result": viewlink_result,
        })

    def handle_set_exposure_mode(self, msg: dict) -> dict:
        """
        设置曝光模式

        Request: { mode: auto/manual/shutter_priority/aperture_priority, ev }
        """
        data = msg.get('data', {})
        mode = data.get('mode', 'auto')
        ev = data.get('ev', 0)
        self._log_camera_setting_mapping('set_exposure_mode', data)

        self._exposure_mode = mode
        logger.info(f"Exposure mode: {mode}, EV={ev}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_zoom(self, msg: dict) -> dict:
        """
        变焦控制

        Request: { zoom_factor/zoom_ratio } 或 { zoom_direction, zoom_speed }
        """
        data = msg.get('data', {})
        ratio = data.get('zoom_factor', data.get('zoom_ratio'))
        self._log_camera_setting_mapping('zoom', data)

        success = False
        viewlink_attempted = False
        if ratio is not None:
            self._zoom_level = ratio
            logger.info(f"Zoom ratio: {ratio}x")
            if self._gimbal_control and self._gimbal_control.is_connected():
                viewlink_attempted = True
                result = self._gimbal_control.zoom_to(float(ratio))
                success = result.get('success', False)
                logger.info(f"ViewLink zoom_to: {result}")
        else:
            direction = data.get('zoom_direction', 0)
            speed = data.get('zoom_speed', 1)
            logger.info(f"Zoom direction: {direction}, speed: {speed}")
            if self._gimbal_control and self._gimbal_control.is_connected():
                viewlink_attempted = True
                try:
                    direction_value = int(direction)
                except (TypeError, ValueError):
                    direction_value = 0
                direction_text = str(direction).lower()
                if direction_value > 0 or direction_text in {"in", "zoom_in", "tele"}:
                    result = self._gimbal_control.zoom_in(int(speed))
                elif direction_value < 0 or direction_text in {"out", "zoom_out", "wide"}:
                    result = self._gimbal_control.zoom_out(int(speed))
                else:
                    result = self._gimbal_control.stop_zoom()
                success = result.get('success', False)
                logger.info(f"ViewLink zoom direction: {result}")

        return MessageBuilder.build_reply(msg, result=0 if success or not viewlink_attempted else -1)

    def handle_set_focus_mode(self, msg: dict) -> dict:
        """
        设置对焦模式

        Request: { mode: auto_focus/manual_focus/AFC }
        """
        data = msg.get('data', {})
        mode = data.get('mode', 'auto_focus')
        self._log_camera_setting_mapping('set_focus_mode', data)

        logger.info(f"Focus mode: {mode}")
        self._focus_mode = mode

        success = False
        viewlink_attempted = False
        if self._gimbal_control and self._gimbal_control.is_connected():
            viewlink_attempted = True
            normalized = str(mode).lower()
            focus_mode = VLKFocusMode.MANUAL if normalized in {"manual", "manual_focus", "mf"} else VLKFocusMode.AUTO
            result = self._gimbal_control.set_focus_mode(focus_mode)
            success = result.get('success', False)
            logger.info(f"ViewLink focus mode: {result}")

        return MessageBuilder.build_reply(msg, result=0 if success or not viewlink_attempted else -1)

    def handle_set_focus_value(self, msg: dict) -> dict:
        """
        手动对焦值

        Request: { focus_distance }
        """
        data = msg.get('data', {})
        distance = data.get('focus_distance', 0)
        self._log_camera_setting_mapping('set_focus_value', data)

        logger.info(f"Focus distance: {distance}")
        self._focus_value = distance

        success = False
        viewlink_attempted = False
        if self._gimbal_control and self._gimbal_control.is_connected():
            viewlink_attempted = True
            speed = int(data.get('speed', data.get('focus_speed', 3)) or 3)
            raw_duration = data.get('duration', data.get('focus_duration', 0.3))
            duration = float(0.3 if raw_duration is None else raw_duration)
            try:
                distance_value = float(distance)
            except (TypeError, ValueError):
                distance_value = 0.0
            if distance_value > 0:
                result = self._gimbal_control.focus_out(speed)
            elif distance_value < 0:
                result = self._gimbal_control.focus_in(speed)
            else:
                result = self._gimbal_control.stop_focus()
            success = result.get('success', False)
            logger.info(f"ViewLink focus value: {result}")
            if success and distance_value != 0:
                time.sleep(max(0.0, min(2.0, duration)))
                stop_result = self._gimbal_control.stop_focus()
                success = stop_result.get('success', False)
                logger.info(f"ViewLink stop focus: {stop_result}")

        return MessageBuilder.build_reply(msg, result=0 if success or not viewlink_attempted else -1)

    def handle_spot_focus(self, msg: dict) -> dict:
        """
        点对焦

        Request: { x, y } 画面位置坐标
        """
        data = msg.get('data', {})
        x = data.get('x', 0.5)
        y = data.get('y', 0.5)
        self._log_camera_setting_mapping('spot_focus', data)

        logger.info(f"Spot focus: ({x}, {y})")
        success = False
        viewlink_attempted = False
        if self._gimbal_control and self._gimbal_control.is_connected():
            viewlink_attempted = True
            result = self._gimbal_control.set_focus_mode(VLKFocusMode.AUTO)
            success = result.get('success', False)
            logger.info(f"ViewLink point focus fallback to auto focus: {result}")

        return MessageBuilder.build_reply(msg, result=0 if success or not viewlink_attempted else -1)

    def handle_photo_storage_set(self, msg: dict) -> dict:
        """
        拍照存储设置

        Request: { quality, include_metadata }
        """
        data = msg.get('data', {})
        quality = data.get('quality', 'high')
        include_metadata = data.get('include_metadata', True)

        logger.info(f"Photo storage: quality={quality}, metadata={include_metadata}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_video_storage_set(self, msg: dict) -> dict:
        """视频存储设置 (video_storage_set)，属于 services 负载控制。"""
        data = msg.get('data', {})
        settings = data.get('video_storage_settings', [])
        logger.info("Video storage set: settings=%s", settings)
        return MessageBuilder.build_reply(msg, result=0, data={
            "video_storage_settings": settings,
        })

    @staticmethod
    def _frame_zoom_number(data: dict, key: str, default: float) -> float:
        try:
            return float(data.get(key, default) if data.get(key, default) is not None else default)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _frame_zoom_frame_size(cls, data: dict) -> dict:
        frame_size = data.get("frame_size") if isinstance(data.get("frame_size"), dict) else {}
        resolution = data.get("resolution") if isinstance(data.get("resolution"), dict) else {}
        width = (
            frame_size.get("width")
            or resolution.get("width")
            or data.get("frame_width")
            or data.get("video_width")
            or data.get("screen_width")
            or 1920
        )
        height = (
            frame_size.get("height")
            or resolution.get("height")
            or data.get("frame_height")
            or data.get("video_height")
            or data.get("screen_height")
            or 1080
        )
        return {
            "width": max(1, int(cls._frame_zoom_number({"value": width}, "value", 1920))),
            "height": max(1, int(cls._frame_zoom_number({"value": height}, "value", 1080))),
        }

    @classmethod
    def _normalize_frame_zoom_box(cls, data: dict) -> tuple[dict, dict, dict]:
        frame_size = cls._frame_zoom_frame_size(data)
        normalized = data.get("normalized") if isinstance(data.get("normalized"), dict) else {}
        if normalized:
            norm_x = cls._frame_zoom_number(normalized, "x", 0.0)
            norm_y = cls._frame_zoom_number(normalized, "y", 0.0)
            norm_width = cls._frame_zoom_number(normalized, "width", 1.0)
            norm_height = cls._frame_zoom_number(normalized, "height", 1.0)
        else:
            raw_x = cls._frame_zoom_number(data, "x", 0.0)
            raw_y = cls._frame_zoom_number(data, "y", 0.0)
            raw_width = cls._frame_zoom_number(data, "width", 1.0)
            raw_height = cls._frame_zoom_number(data, "height", 1.0)
            looks_normalized = all(0.0 <= value <= 1.0 for value in (raw_x, raw_y, raw_width, raw_height))
            if looks_normalized:
                norm_x, norm_y, norm_width, norm_height = raw_x, raw_y, raw_width, raw_height
            else:
                norm_x = raw_x / frame_size["width"]
                norm_y = raw_y / frame_size["height"]
                norm_width = raw_width / frame_size["width"]
                norm_height = raw_height / frame_size["height"]

        norm_x = max(0.0, min(1.0, norm_x))
        norm_y = max(0.0, min(1.0, norm_y))
        norm_width = max(0.001, min(1.0 - norm_x, norm_width))
        norm_height = max(0.001, min(1.0 - norm_y, norm_height))
        normalized_box = {
            "x": norm_x,
            "y": norm_y,
            "width": norm_width,
            "height": norm_height,
        }
        pixel_box = {
            "x": int(round(norm_x * frame_size["width"])),
            "y": int(round(norm_y * frame_size["height"])),
            "width": max(1, int(round(norm_width * frame_size["width"]))),
            "height": max(1, int(round(norm_height * frame_size["height"]))),
        }
        return normalized_box, pixel_box, frame_size

    @staticmethod
    def _frame_zoom_centering_speed(offset_x: float, offset_y: float) -> dict:
        if abs(offset_x) < 0.01 and abs(offset_y) < 0.01:
            return {"yaw_speed": 0, "pitch_speed": 0}
        scale = int(Config.get("payload.frame_zoom_centering_speed_scale", 2400) or 2400)
        max_speed = int(Config.get("payload.frame_zoom_centering_max_speed", 1200) or 1200)
        yaw_speed = max(-max_speed, min(max_speed, int(round(offset_x * scale))))
        pitch_speed = max(-max_speed, min(max_speed, int(round(-offset_y * scale))))
        return {"yaw_speed": yaw_speed, "pitch_speed": pitch_speed}

    def handle_camera_frame_zoom(self, msg: dict) -> dict:
        """框选变焦 (camera_frame_zoom)，属于 services 负载控制。"""
        data = msg.get('data', {})
        normalized_box, pixel_box, frame_size = self._normalize_frame_zoom_box(data)
        x = pixel_box["x"]
        y = pixel_box["y"]
        width = pixel_box["width"]
        height = pixel_box["height"]
        requested_zoom_ratio = data.get("zoom_ratio")
        center_offset_x = (normalized_box["x"] + normalized_box["width"] / 2.0) - 0.5
        center_offset_y = (normalized_box["y"] + normalized_box["height"] / 2.0) - 0.5
        if requested_zoom_ratio is None:
            box_zoom = min(
                1.0 / max(0.001, normalized_box["width"]),
                1.0 / max(0.001, normalized_box["height"]),
            )
            max_ratio = float(Config.get("payload.frame_zoom_max_ratio", 30.0) or 30.0)
            zoom_ratio = min(max_ratio, max(float(self._zoom_level or 1.0), box_zoom))
        else:
            zoom_ratio = float(requested_zoom_ratio or self._zoom_level)
        center_speed = self._frame_zoom_centering_speed(center_offset_x, center_offset_y)
        logger.info(
            "Camera frame zoom: payload_index=%s camera_type=%s locked=%s box=(%s,%s,%s,%s) normalized=%s center_offset=(%.4f,%.4f) zoom_ratio=%s",
            data.get("payload_index", ""),
            data.get("camera_type", ""),
            data.get("locked", False),
            x,
            y,
            width,
            height,
            normalized_box,
            center_offset_x,
            center_offset_y,
            zoom_ratio,
        )

        result = {"success": True, "message": "frame zoom accepted locally", "simulated": True}
        center_result = {"success": True, "message": "frame center already aligned", "skipped": True}
        stop_result = None
        pipeline = [
            {
                "step": "normalize_selection",
                "status": "ok",
                "normalized": normalized_box,
                "pixel_box": pixel_box,
                "frame_size": frame_size,
            },
            {
                "step": "compute_center_offset",
                "status": "ok",
                "center_offset": {"x": center_offset_x, "y": center_offset_y},
                "center_speed": center_speed,
                "zoom_ratio": zoom_ratio,
                "zoom_ratio_source": "request" if requested_zoom_ratio is not None else "selection_box",
            },
        ]
        if self._gimbal_control and self._gimbal_control.is_connected():
            if center_speed["yaw_speed"] or center_speed["pitch_speed"]:
                move_result = self._gimbal_control.move(center_speed["yaw_speed"], center_speed["pitch_speed"])
                center_result = {
                    "success": bool(move_result.get("success", False)),
                    "method": "move_to_center",
                    "yaw_speed": center_speed["yaw_speed"],
                    "pitch_speed": center_speed["pitch_speed"],
                    "viewlink_result": move_result,
                }
                logger.info("ViewLink frame zoom/center move: %s", move_result)
                pipeline.append({
                    "step": "gimbal_center_move",
                    "status": "ok" if center_result["success"] else "failed",
                    "yaw_speed": center_speed["yaw_speed"],
                    "pitch_speed": center_speed["pitch_speed"],
                    "viewlink_result": move_result,
                })
                if center_result["success"] and hasattr(self._gimbal_control, "stop"):
                    settle_seconds = self._frame_zoom_number(
                        data,
                        "center_duration",
                        float(Config.get("payload.frame_zoom_centering_duration", 0.15) or 0.15),
                    )
                    settle_seconds = max(0.05, min(1.0, settle_seconds))
                    time.sleep(settle_seconds)
                    stop_result = self._gimbal_control.stop()
                    center_result["stop_result"] = stop_result
                    center_result["success"] = bool(stop_result.get("success", False))
                    logger.info("ViewLink frame zoom/center stop: %s", stop_result)
                    pipeline.append({
                        "step": "gimbal_center_stop",
                        "status": "ok" if center_result["success"] else "failed",
                        "settle_seconds": settle_seconds,
                        "viewlink_result": stop_result,
                    })
            else:
                pipeline.append({
                    "step": "gimbal_center_move",
                    "status": "skipped",
                    "reason": "selection center already aligned",
                })
            if center_result.get("success", False):
                result = self._gimbal_control.zoom_to(zoom_ratio)
                pipeline.append({
                    "step": "camera_zoom_to",
                    "status": "ok" if bool(result.get("success", False)) else "failed",
                    "zoom_ratio": zoom_ratio,
                    "viewlink_result": result,
                })
            logger.info("ViewLink frame zoom/zoom_to: %s", result)
        else:
            pipeline.append({
                "step": "camera_zoom_to",
                "status": "simulated",
                "zoom_ratio": zoom_ratio,
                "reason": "ViewLink control is not connected",
            })

        success = bool(
            center_result.get("success", False)
            and result.get("success", False)
        )
        if success:
            self._zoom_level = zoom_ratio
        return MessageBuilder.build_reply(msg, result=0 if success else -1, data={
            "locked": bool(data.get("locked", False)),
            "zoom_ratio": zoom_ratio,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "normalized": normalized_box,
            "frame_size": frame_size,
            "center_offset": {
                "x": center_offset_x,
                "y": center_offset_y,
            },
            "center_speed": center_speed,
            "center_result": center_result,
            "stop_result": stop_result,
            "software_pipeline": pipeline,
            "viewlink_result": result,
        })

    def handle_camera_mode_switch(self, msg: dict) -> dict:
        """负载相机模式切换 (camera_mode_switch)，属于 services 负载控制。"""
        data = msg.get('data', {})
        mode = data.get("mode")
        if mode is None:
            camera_mode = data.get("camera_mode", 0)
            mode = "photo" if camera_mode == 0 else "video"
            msg = dict(msg)
            msg["data"] = {**data, "mode": mode}
        return self.handle_set_camera_mode(msg)

    def handle_camera_exposure_set(self, msg: dict) -> dict:
        """相机曝光值调节 (camera_exposure_set)，属于 services 负载控制。"""
        data = msg.get('data', {})
        exposure_value = data.get("exposure_value", data.get("ev", 0))
        logger.info("Camera exposure set: camera_type=%s exposure_value=%s", data.get("camera_type", ""), exposure_value)
        return MessageBuilder.build_reply(msg, result=0, data={
            "camera_type": data.get("camera_type", ""),
            "exposure_value": exposure_value,
        })

    # ---- 进度上报 ----

    def report_photo_progress(self, current: int, total: int, storage_path: str = ""):
        """上报拍照进度"""
        if self._event_callback:
            event = MessageBuilder.build_event(
                'photo_take_progress',
                {
                    'current': current,
                    'total': total,
                    'storage_path': storage_path
                },
                self.gateway_sn
            )
            self._event_callback(event)

    # ---- 媒体文件元数据管理 ----

    def _ensure_event_loop(self):
        """确保事件循环已设置"""
        if self._main_loop is None:
            try:
                self._main_loop = asyncio.get_running_loop()
                logger.info("CameraController captured main event loop")
            except RuntimeError:
                logger.warning("No running event loop detected")

    async def _init_database(self):
        """初始化数据库"""
        if self._db_initialized:
            return
        await init_database_async()
        self._db_initialized = True
        logger.info("CameraController database initialized")

    async def _save_media_metadata(
        self,
        file_type: str,
        duration: float = None,
        file_path: str = None,
        file_name: str = None,
        file_size: int = None,
        resolution: str = None,
    ):
        """保存媒体文件元数据到数据库"""
        try:
            await self._init_database()
            AsyncSessionLocal = get_async_session_local()

            metadata, media_data = self._build_media_metadata_record(
                file_type,
                duration=duration,
                file_path=file_path,
                file_name=file_name,
                file_size=file_size,
                resolution=resolution,
            )
            file_name = metadata["file_name"]

            async with AsyncSessionLocal() as session:
                await MediaDB.create_media_file(session, media_data)
                await session.commit()

            logger.info(f"Media metadata saved: {file_name}, type={file_type}")

            # 上报拍照结果事件
            if self._event_callback:
                event = MessageBuilder.build_event(
                    'photo_take_progress',
                    {
                        'current': self._photo_count,
                        'total': self._photo_count,
                        'storage_path': media_data["file_path"],
                        'metadata': metadata
                    },
                    self.gateway_sn
                )
                self._event_callback(event)

        except Exception as e:
            logger.error(f"Save media metadata failed: {e}")

    def _save_media_metadata_sync(
        self,
        file_type: str,
        duration: float = None,
        file_path: str = None,
        file_name: str = None,
        file_size: int = None,
        resolution: str = None,
    ):
        """异步保存媒体文件元数据（非阻塞）"""
        self._ensure_event_loop()
        if self._main_loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._save_media_metadata(
                        file_type,
                        duration,
                        file_path=file_path,
                        file_name=file_name,
                        file_size=file_size,
                        resolution=resolution,
                    ),
                    self._main_loop
                )
            except Exception as e:
                logger.error(f"Failed to schedule media metadata save: {e}")
        else:
            logger.warning("Cannot save media metadata: no event loop")

    # ---- 大疆标准相机接口扩展 ----

    def handle_camera_recover(self, msg: dict) -> dict:
        """相机恢复"""
        logger.info("Camera recover")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_camera_reset(self, msg: dict) -> dict:
        """相机重置"""
        logger.info("Camera reset")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_shutter_speed(self, msg: dict) -> dict:
        """设置快门速度"""
        data = msg.get('data', {})
        shutter = data.get('shutter_speed', 'auto')
        self._log_camera_setting_mapping('set_shutter_speed', data)
        logger.info(f"Shutter speed: {shutter}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_iso(self, msg: dict) -> dict:
        """设置ISO"""
        data = msg.get('data', {})
        iso = data.get('iso', 'auto')
        self._log_camera_setting_mapping('set_iso', data)
        logger.info(f"ISO: {iso}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_white_balance(self, msg: dict) -> dict:
        """设置白平衡"""
        data = msg.get('data', {})
        wb = data.get('white_balance', 'auto')
        self._log_camera_setting_mapping('set_white_balance', data)
        logger.info(f"White balance: {wb}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_aperture(self, msg: dict) -> dict:
        """设置光圈"""
        data = msg.get('data', {})
        aperture = data.get('aperture', 'auto')
        self._log_camera_setting_mapping('set_aperture', data)
        logger.info(f"Aperture: {aperture}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_metering_mode(self, msg: dict) -> dict:
        """设置测光模式"""
        data = msg.get('data', {})
        mode = data.get('metering_mode', 'center')
        logger.info(f"Metering mode: {mode}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_resolution(self, msg: dict) -> dict:
        """设置分辨率"""
        data = msg.get('data', {})
        resolution = data.get('resolution')
        self._log_camera_setting_mapping('set_resolution', data)
        logger.info(f"Resolution: {resolution or 'unchanged'}")
        self._resolution = str(resolution) if resolution else None
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_frame_rate(self, msg: dict) -> dict:
        """设置帧率"""
        data = msg.get('data', {})
        fps = data.get('frame_rate', 30)
        self._log_camera_setting_mapping('set_frame_rate', data)
        logger.info(f"Frame rate: {fps}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_video_encoding(self, msg: dict) -> dict:
        """设置视频编码"""
        data = msg.get('data', {})
        encoding = data.get('encoding', 'h264')
        self._log_camera_setting_mapping('set_video_encoding', data)
        logger.info(f"Video encoding: {encoding}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_camera_capability_query(self, msg: dict) -> dict:
        """相机能力查询"""
        logger.info("Camera capability query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'supported_modes': ['photo', 'video', 'playback'],
            'supported_exposure_modes': ['auto', 'manual', 'shutter_priority', 'aperture_priority'],
            'supported_resolutions': ['720p', '1080p', '4k'],
            'max_zoom_ratio': 30.0,
            'has_thermal': False
        })

    # ---- 大疆标准相机接口别名 ----

    def handle_camera_photo_stop(self, msg: dict) -> dict:
        """
        停止拍照 (camera_photo_stop)

        别名，调用 stop_photo
        """
        return self.handle_stop_photo(msg)

    def handle_camera_recording_start(self, msg: dict) -> dict:
        """
        开始录像 (camera_recording_start)

        别名，调用 start_record
        """
        return self.handle_start_record(msg)

    def handle_camera_recording_stop(self, msg: dict) -> dict:
        """
        停止录像 (camera_recording_stop)

        别名，调用 stop_record
        """
        return self.handle_stop_record(msg)

    def handle_camera_focal_length_set(self, msg: dict) -> dict:
        """
        设置焦距 (camera_focal_length_set)

        上云字段为 zoom_factor；兼容旧内部字段 zoom_ratio。
        """
        return self.handle_zoom(msg)

    def handle_camera_point_focus_action(self, msg: dict) -> dict:
        """
        点对焦动作 (camera_point_focus_action)

        别名，调用 spot_focus
        """
        return self.handle_spot_focus(msg)

    def handle_camera_exposure_mode_set(self, msg: dict) -> dict:
        """Camera exposure mode set (camera_exposure_mode_set) - alias of set_exposure_mode"""
        return self.handle_set_exposure_mode(msg)

    def handle_camera_focus_mode_set(self, msg: dict) -> dict:
        """Camera focus mode set (camera_focus_mode_set) - alias of set_focus_mode"""
        return self.handle_set_focus_mode(msg)

    def handle_camera_focus_value_set(self, msg: dict) -> dict:
        """Camera focus value set (camera_focus_value_set) - alias of set_focus_value"""
        return self.handle_set_focus_value(msg)
