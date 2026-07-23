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
from src.modules.gimbal_control import VLKFocusMode
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
        self._zoom_level = 1.0
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
        }
        self._last_mapping_camera_cache = dict(cached)
        self._last_mapping_camera_control_route = route
        return cached

    # ---- 拍照API ----

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
            self._save_media_metadata_sync('photo')

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

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
            self._save_media_metadata_sync('photo')
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

        success, _ = self._send_mapped_camera_command('start_photo', data)

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

    def handle_stop_photo(self, msg: dict) -> dict:
        """停止连续拍照"""
        logger.info("Stop photo")

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
        resolution = data.get('resolution', '1080p')

        if self._is_recording:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Already recording")

        logger.info(f"Start record: camera={camera_idx}, resolution={resolution}")

        success = False

        # 优先使用 ViewLink SDK 开始录像
        if self._gimbal_control and self._gimbal_control.is_connected():
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
            self._save_media_metadata_sync('video', duration=recording_time)

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
        mode_str = data.get('mode', 'photo')
        self._log_camera_setting_mapping('set_camera_mode', data)

        mode_map = {'photo': self.MODE_PHOTO, 'video': self.MODE_VIDEO, 'playback': self.MODE_PLAYBACK}
        self._mode = mode_map.get(mode_str, self.MODE_PHOTO)

        logger.info(f"Camera mode: {mode_str}")

        return MessageBuilder.build_reply(msg, result=0)

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

        Request: { zoom_ratio } 或 { zoom_direction, zoom_speed }
        """
        data = msg.get('data', {})
        ratio = data.get('zoom_ratio')
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

    def handle_camera_frame_zoom(self, msg: dict) -> dict:
        """框选变焦 (camera_frame_zoom)，属于 services 负载控制。"""
        data = msg.get('data', {})
        x = int(float(data.get("x", 0) or 0))
        y = int(float(data.get("y", 0) or 0))
        width = max(1, int(float(data.get("width", 1) or 1)))
        height = max(1, int(float(data.get("height", 1) or 1)))
        zoom_ratio = float(data.get("zoom_ratio", self._zoom_level) or self._zoom_level)
        logger.info(
            "Camera frame zoom: payload_index=%s camera_type=%s locked=%s box=(%s,%s,%s,%s) zoom_ratio=%s",
            data.get("payload_index", ""),
            data.get("camera_type", ""),
            data.get("locked", False),
            x,
            y,
            width,
            height,
            zoom_ratio,
        )

        result = {"success": True, "message": "frame zoom accepted locally", "simulated": True}
        if self._gimbal_control and self._gimbal_control.is_connected():
            # 框选变焦只设置镜头倍率；AIM 锁定才会进入 ViewLink 目标跟踪，避免云台本体联动。
            result = self._gimbal_control.zoom_to(zoom_ratio)
            logger.info("ViewLink frame zoom/zoom_to: %s", result)

        success = bool(result.get("success", False))
        if success:
            self._zoom_level = zoom_ratio
        return MessageBuilder.build_reply(msg, result=0 if success else -1, data={
            "locked": bool(data.get("locked", False)),
            "zoom_ratio": zoom_ratio,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "normalized": data.get("normalized", {}),
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

    async def _save_media_metadata(self, file_type: str, duration: float = None):
        """保存媒体文件元数据到数据库"""
        try:
            await self._init_database()
            AsyncSessionLocal = get_async_session_local()

            # 获取当前遥测数据（位置信息）
            telemetry = {}
            if self._telemetry_module and hasattr(self._telemetry_module, 'get_gps'):
                gps = self._telemetry_module.get_gps()
                if gps:
                    telemetry = gps

            capture_time = int(time.time())
            metadata = build_media_metadata(
                file_type,
                telemetry=telemetry,
                context={
                    "mission_id": self._current_mission_id,
                    "device_sn": self.gateway_sn,
                    "capture_time": capture_time,
                },
                duration=duration,
            )
            file_name = metadata["file_name"]

            async with AsyncSessionLocal() as session:
                media_data = {
                    'mission_id': metadata["mission_id"],
                    'device_sn': metadata["device_sn"],
                    'file_name': metadata["file_name"],
                    'file_path': f"/data/media/{file_name}",
                    'file_size': 0,
                    'file_type': metadata["file_type"],
                    'resolution': '1080p',
                    'duration': metadata.get("duration"),
                    'capture_time': metadata["capture_time"],
                    'latitude': metadata["latitude"],
                    'longitude': metadata["longitude"],
                    'altitude': metadata["altitude"],
                    'upload_status': metadata["upload_status"],
                }
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
                        'storage_path': f"/data/media/{file_name}",
                        'metadata': metadata
                    },
                    self.gateway_sn
                )
                self._event_callback(event)

        except Exception as e:
            logger.error(f"Save media metadata failed: {e}")

    def _save_media_metadata_sync(self, file_type: str, duration: float = None):
        """异步保存媒体文件元数据（非阻塞）"""
        self._ensure_event_loop()
        if self._main_loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._save_media_metadata(file_type, duration),
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
        resolution = data.get('resolution', '1080p')
        self._log_camera_setting_mapping('set_resolution', data)
        logger.info(f"Resolution: {resolution}")
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

        别名，调用 zoom
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
