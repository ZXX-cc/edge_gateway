"""
相机控制模块
处理拍照、录像、相机参数设置
"""
from typing import Optional
from pymavlink import mavutil
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder

logger = get_logger("camera")


class CameraController:
    """
    相机控制器

    职责:
    1. 拍照控制(单次/连续/定时)
    2. 录像控制(开始/停止)
    3. 相机参数设置(模式、曝光、变焦、对焦)
    4. 拍照进度上报

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

    def set_mavlink_connection(self, mavlink_conn):
        """注入MAVLink连接"""
        self._mavlink_conn = mavlink_conn

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

    # ---- 拍照API ----

    def handle_camera_photo_take(self, msg: dict) -> dict:
        """
        单次拍照

        Request: { camera_index, exposure_compensation, zoom_ratio }
        """
        data = msg.get('data', {})
        camera_idx = data.get('camera_index', 0)

        logger.info(f"Photo take: camera={camera_idx}")

        # 通过MAVLink发送拍照命令 (MAV_CMD_DO_DIGICAM_CONTROL)
        self._send_mavlink_command(
            mavutil.mavlink.MAV_CMD_DO_DIGICAM_CONTROL,
            [0, 0, 0, 0, 1, 0, 0]  # param5=1 触发拍照
        )

        self._photo_count += 1

        return MessageBuilder.build_reply(msg, result=0)

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

        # 通过MAVLink发送连拍命令 (MAV_CMD_IMAGE_START_CAPTURE)
        self._send_mavlink_command(
            mavutil.mavlink.MAV_CMD_IMAGE_START_CAPTURE,
            [0, interval * 1000, count, 0, 0, 0, 0]  # param2=间隔(ms), param3=张数
        )

        return MessageBuilder.build_reply(msg, result=0)

    def handle_stop_photo(self, msg: dict) -> dict:
        """停止连续拍照"""
        logger.info("Stop photo")

        # 通过MAVLink停止拍照 (MAV_CMD_IMAGE_STOP_CAPTURE)
        self._send_mavlink_command(
            mavutil.mavlink.MAV_CMD_IMAGE_STOP_CAPTURE,
            [0, 0, 0, 0, 0, 0, 0]
        )

        return MessageBuilder.build_reply(msg, result=0)

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

        # 通过MAVLink发送录像命令 (MAV_CMD_VIDEO_START_CAPTURE)
        self._send_mavlink_command(
            mavutil.mavlink.MAV_CMD_VIDEO_START_CAPTURE,
            [0, 30, 0, 0, 0, 0, 0]  # param2=帧率
        )

        return MessageBuilder.build_reply(msg, result=0)

    def handle_stop_record(self, msg: dict) -> dict:
        """停止录像"""
        if not self._is_recording:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Not recording")

        logger.info("Stop record")
        self._is_recording = False

        # 通过MAVLink停止录像 (MAV_CMD_VIDEO_STOP_CAPTURE)
        self._send_mavlink_command(
            mavutil.mavlink.MAV_CMD_VIDEO_STOP_CAPTURE,
            [0, 0, 0, 0, 0, 0, 0]
        )

        return MessageBuilder.build_reply(msg, result=0, data={'record_time': self._recording_time})

    # ---- 参数设置API ----

    def handle_set_camera_mode(self, msg: dict) -> dict:
        """
        切换相机模式

        Request: { mode: 'photo'/'video'/'playback' }
        """
        data = msg.get('data', {})
        mode_str = data.get('mode', 'photo')

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

        logger.info(f"Focus mode: {mode}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_focus_value(self, msg: dict) -> dict:
        """
        手动对焦值

        Request: { focus_distance }
        """
        data = msg.get('data', {})
        distance = data.get('focus_distance', 0)

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
        # TODO: 通过events上报 photo_take_progress
        pass

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
        logger.info(f"Shutter speed: {shutter}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_iso(self, msg: dict) -> dict:
        """设置ISO"""
        data = msg.get('data', {})
        iso = data.get('iso', 'auto')
        logger.info(f"ISO: {iso}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_white_balance(self, msg: dict) -> dict:
        """设置白平衡"""
        data = msg.get('data', {})
        wb = data.get('white_balance', 'auto')
        logger.info(f"White balance: {wb}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_aperture(self, msg: dict) -> dict:
        """设置光圈"""
        data = msg.get('data', {})
        aperture = data.get('aperture', 'auto')
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
        logger.info(f"Resolution: {resolution}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_frame_rate(self, msg: dict) -> dict:
        """设置帧率"""
        data = msg.get('data', {})
        fps = data.get('frame_rate', 30)
        logger.info(f"Frame rate: {fps}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_video_encoding(self, msg: dict) -> dict:
        """设置视频编码"""
        data = msg.get('data', {})
        encoding = data.get('encoding', 'h264')
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

