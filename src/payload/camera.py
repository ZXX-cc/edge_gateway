"""
相机控制模块
处理拍照、录像、相机参数设置
"""
import time
import threading
import asyncio
from typing import Optional, Callable
from uuid import UUID
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.database.connection import get_async_session_local, init_database_async
from src.database.media_db import MediaDB
from web.mavlink.camera import (
    build_camera_command,
    build_camera_setting,
    build_media_metadata,
)
from web.mavlink.viewlink import build_viewlink_call

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

        # MAVLink连接引用(由外部注入)
        self._mavlink_conn = None

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

    def set_mavlink_connection(self, mavlink_conn):
        """注入MAVLink连接"""
        self._mavlink_conn = mavlink_conn

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

    def _send_mavlink_command(self, command: int, params: list) -> bool:
        """发送MAVLink COMMAND_LONG"""
        if not self._mavlink_conn or not self._mavlink_conn.master:
            logger.warning("MAVLink not connected, cannot send camera command")
            return False
        try:
            self._mavlink_conn.master.mav.command_long_send(
                self._mavlink_conn.master.target_system,
                self._mavlink_conn.master.target_component,
                command,
                0,  # confirmation
                *params
            )
            return True
        except Exception as e:
            logger.error(f"Camera command send failed: {e}")
            return False

    def _send_mapped_camera_command(self, method: str, data: dict) -> tuple[bool, dict]:
        """按 web/mavlink 相机映射发送 MAVLink COMMAND_LONG。"""
        mapping = build_camera_command(method, data)
        mavlink = mapping["mavlink"]
        success = self._send_mavlink_command(mavlink["command"], mavlink["params"])
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

    # ---- 拍照API ----

    def handle_camera_photo_take(self, msg: dict) -> dict:
        """
        单次拍照

        Request: { camera_index, exposure_compensation, zoom_ratio }
        """
        data = msg.get('data', {})
        camera_idx = data.get('camera_index', 0)

        logger.info(f"Photo take: camera={camera_idx}")

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

        self._photo_count += 1

        # 异步写入媒体文件元数据到数据库
        if success:
            self._save_media_metadata_sync('photo')

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

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
        self._is_recording = True
        self._recording_start_time = time.time()

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

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

    def handle_stop_record(self, msg: dict) -> dict:
        """停止录像"""
        if not self._is_recording:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Not recording")

        logger.info("Stop record")
        self._is_recording = False
        self._recording_time = int(time.time() - self._recording_start_time)

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
            self._save_media_metadata_sync('video', duration=self._recording_time)

        return MessageBuilder.build_reply(msg, result=0 if success else -1, data={'record_time': self._recording_time})

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

        if ratio is not None:
            self._zoom_level = ratio
            logger.info(f"Zoom ratio: {ratio}x")
        else:
            direction = data.get('zoom_direction', 0)
            speed = data.get('zoom_speed', 1)
            logger.info(f"Zoom direction: {direction}, speed: {speed}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_focus_mode(self, msg: dict) -> dict:
        """
        设置对焦模式

        Request: { mode: auto_focus/manual_focus/AFC }
        """
        data = msg.get('data', {})
        mode = data.get('mode', 'auto_focus')
        self._log_camera_setting_mapping('set_focus_mode', data)

        logger.info(f"Focus mode: {mode}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_focus_value(self, msg: dict) -> dict:
        """
        手动对焦值

        Request: { focus_distance }
        """
        data = msg.get('data', {})
        distance = data.get('focus_distance', 0)
        self._log_camera_setting_mapping('set_focus_value', data)

        logger.info(f"Focus distance: {distance}")

        return MessageBuilder.build_reply(msg, result=0)

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

        return MessageBuilder.build_reply(msg, result=0)

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
