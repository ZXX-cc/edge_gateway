"""
云台控制模块
处理云台角度、模式、目标指向
"""
import time
from typing import Any, Dict, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.modules.gimbal_control import (
    VLKConnType,
    VLKFocusMode,
    VLKLaserZoomMode,
    VLKRecordMode,
    VLKSensor,
    VLKTrackTemplateSize,
)
from src.payload.dock3_payload import with_dock3_payload_index
from web.mavlink.gimbal import GIMBAL_CAPABILITY, build_gimbal_command
from web.mavlink.viewlink import build_viewlink_call

logger = get_logger("gimbal")


class GimbalController:
    """
    云台控制器

    职责:
    1. 云台角度控制(pitch/roll/yaw)
    2. 云台工作模式切换(FOLLOW/FREE/FPV/RESET)
    3. 目标点指向(camera_look_at)
    4. 角速度控制
    5. 变焦控制

    API方法:
    - gimbalRotate, camera_look_at, set_gimbal_mode
    """

    # 云台模式
    MODE_FOLLOW = 0   # 跟随模式
    MODE_FREE = 1     # 自由模式
    MODE_FPV = 2      # FPV模式
    MODE_RESET = 3    # 回中模式

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()

        # 当前云台状态
        self._pitch = 0.0
        self._roll = 0.0
        self._yaw = 0.0
        self._mode = self.MODE_FOLLOW
        self._last_control: Dict[str, Any] = {}
        self._last_update_time = int(time.time() * 1000)

        # MAVLink连接引用(由外部注入)
        self._mavlink_conn = None

        # ViewLink SDK 云台控制模块引用
        self._gimbal_control = None

        # 事件回调
        self._event_callback: Optional[Callable] = None

    def set_mavlink_connection(self, mavlink_conn):
        """注入MAVLink连接"""
        self._mavlink_conn = mavlink_conn

    def set_gimbal_control(self, gimbal_control):
        """注入 ViewLink 云台控制模块"""
        self._gimbal_control = gimbal_control

    def set_event_callback(self, callback: Callable):
        """设置事件回调"""
        self._event_callback = callback

    def _mode_text(self) -> str:
        return {
            self.MODE_FOLLOW: "FOLLOW",
            self.MODE_FREE: "FREE",
            self.MODE_FPV: "FPV",
            self.MODE_RESET: "RESET",
        }.get(self._mode, "FOLLOW")

    def _connection_state(self) -> str:
        if not self._gimbal_control:
            return "disconnected"
        try:
            return "connected" if self._gimbal_control.is_connected() else "disconnected"
        except Exception as exc:
            logger.warning("Query ViewLink connection state failed: %s", exc)
            return "unknown"

    def build_payload_state(self) -> Dict[str, Any]:
        """Build the Cloud API-facing state snapshot for the only connected payload: gimbal."""
        gimbal_config = Config.get_payload_config().get("devices", {}).get("gimbal", {})
        channels = gimbal_config.get("channels", {}) if isinstance(gimbal_config, dict) else {}
        control = channels.get("control", {}) if isinstance(channels, dict) else {}
        video = channels.get("video", {}) if isinstance(channels, dict) else {}
        state = {
            "payload_type": "gimbal",
            "payload_model_key": str(gimbal_config.get("model_key", "viewlink-gimbal")),
            "payload_name": str(gimbal_config.get("name", "gimbal")),
            "connection_state": self._connection_state(),
            "adapter": control.get("adapter", "viewlink"),
            "control": {
                "transport": control.get("transport", "viewlink_tcp"),
                "host": control.get("host", ""),
                "port": control.get("port", 0),
            },
            "video": {
                "transport": video.get("transport", ""),
                "endpoint": video.get("endpoint", ""),
            },
            "gimbal": {
                "pitch": self._pitch,
                "roll": self._roll,
                "yaw": self._yaw,
                "mode": self._mode,
                "mode_text": self._mode_text(),
            },
            "last_control": dict(self._last_control),
            "last_update_time": self._last_update_time,
        }
        return with_dock3_payload_index(state, gimbal_config)

    def _notify_payload_state(self, reason: str):
        """Publish a payload state event without polluting Dock3 dock state properties."""
        self._last_update_time = int(time.time() * 1000)
        if not self._event_callback:
            return
        event = MessageBuilder.build_event(
            "payload_status_notify",
            {
                "reason": reason,
                "payloads": [self.build_payload_state()],
            },
            self.gateway_sn,
        )
        self._event_callback(event)

    def _send_mavlink_command(self, command: int, params: list) -> bool:
        """发送MAVLink COMMAND_LONG"""
        if not self._mavlink_conn or not self._mavlink_conn.master:
            logger.warning("MAVLink not connected, cannot send gimbal command")
            return False
        try:
            self._mavlink_conn.master.mav.command_long_send(
                self._mavlink_conn.master.target_system,
                self._mavlink_conn.master.target_component,
                command,
                0,
                *params
            )
            return True
        except Exception as e:
            logger.error(f"Gimbal command send failed: {e}")
            return False

    def _send_mapped_gimbal_command(self, method: str, data: dict) -> tuple[bool, dict]:
        """按 web/mavlink 云台映射发送 MAVLink COMMAND_LONG。"""
        mapping = build_gimbal_command(method, data)
        mavlink = mapping["mavlink"]
        success = self._send_mavlink_command(mavlink["command"], mavlink["params"])
        logger.info(
            "Gimbal MAVLink mapped: method=%s command=%s params=%s success=%s",
            method,
            mavlink["command_name"],
            mavlink["params"],
            success,
        )
        return success, mapping

    def _execute_viewlink_call(self, mapping: dict) -> dict:
        """执行 web/mavlink/viewlink 生成的 SDK 调用描述。"""
        if not self._gimbal_control:
            return {"success": False, "message": "ViewLink control is not configured"}

        method = mapping["sdk_method"]
        args = mapping.get("args", {})

        try:
            if method == "initialize":
                sdk_dir = args.get("sdk_dir")
                if sdk_dir:
                    self._gimbal_control.sdk_dir = sdk_dir
                return self._gimbal_control.initialize()
            if method == "connect":
                conn_type = VLKConnType(args.get("conn_type", VLKConnType.TCP))
                return self._gimbal_control.connect(
                    ip=args.get("ip"),
                    port=args.get("port"),
                    conn_type=conn_type,
                )
            if method == "is_connected":
                connected = self._gimbal_control.is_connected()
                return {"success": True, "connected": connected}
            if method == "start_track":
                return self._gimbal_control.start_track(
                    args.get("x", 0),
                    args.get("y", 0),
                    args.get("width", 0),
                    args.get("height", 0),
                    VLKTrackTemplateSize(args.get("template_size", VLKTrackTemplateSize.AUTO)),
                    VLKSensor(args.get("sensor", VLKSensor.VISIBLE1)),
                )
            if method == "set_record_mode":
                return self._gimbal_control.set_record_mode(
                    VLKRecordMode(args.get("mode", VLKRecordMode.NONE))
                )
            if method == "set_focus_mode":
                return self._gimbal_control.set_focus_mode(
                    VLKFocusMode(args.get("mode", VLKFocusMode.AUTO))
                )
            if method == "set_laser_zoom_mode":
                return self._gimbal_control.set_laser_zoom_mode(
                    VLKLaserZoomMode(args.get("mode", VLKLaserZoomMode.FOLLOW_EO))
                )

            func = getattr(self._gimbal_control, method)
            return func(**args)
        except Exception as exc:
            logger.error(f"ViewLink call failed: {method}, error={exc}")
            return {"success": False, "message": str(exc)}

    # ---- API处理器 ----

    def handle_gimbal_rotate(self, msg: dict) -> dict:
        """
        云台角度控制

        Request: { pitch, roll, yaw, pitch_speed, roll_speed, yaw_speed }
        支持绝对角度控制和角速度控制
        """
        data = msg.get('data', {})

        # 绝对角度
        pitch = data.get('pitch')
        roll = data.get('roll')
        yaw = data.get('yaw')

        # 角速度
        pitch_speed = data.get('pitch_speed')
        roll_speed = data.get('roll_speed')
        yaw_speed = data.get('yaw_speed')

        target_pitch = self._pitch if pitch is None else max(-90, min(30, pitch))
        target_roll = self._roll if roll is None else roll
        target_yaw = self._yaw if yaw is None else yaw

        logger.info(f"Gimbal rotate: pitch={pitch}, roll={roll}, yaw={yaw}")
        if pitch_speed is not None:
            logger.info(f"Gimbal speed: p={pitch_speed}, r={roll_speed}, y={yaw_speed}")

        success = False

        # 优先使用 ViewLink SDK 云台控制
        if self._gimbal_control and self._gimbal_control.is_connected():
            if pitch is not None or yaw is not None:
                logger.info("ViewLink mapped: %s", build_viewlink_call("turn_to", {
                    "yaw": target_yaw,
                    "pitch": target_pitch,
                }))
                result = self._gimbal_control.turn_to(target_yaw, target_pitch)
                success = result.get('success', False)
                logger.info(f"ViewLink turn_to: {result}")
            elif pitch_speed is not None or yaw_speed is not None:
                yaw_spd = int((yaw_speed or 0) * 100)
                pitch_spd = int((pitch_speed or 0) * 100)
                logger.info("ViewLink mapped: %s", build_viewlink_call("move", {
                    "yaw_speed": yaw_spd,
                    "pitch_speed": pitch_spd,
                }))
                result = self._gimbal_control.move(yaw_spd, pitch_spd)
                success = result.get('success', False)
                logger.info(f"ViewLink move: {result}")

        # 如果 ViewLink 不可用，回退到 MAVLink
        if not success and (pitch is not None or yaw is not None or roll is not None):
            mapped_data = dict(data)
            mapped_data["pitch"] = target_pitch
            mapped_data["roll"] = target_roll
            mapped_data["yaw"] = target_yaw
            success, _ = self._send_mapped_gimbal_command('gimbalRotate', mapped_data)

        if success:
            self._pitch = target_pitch
            self._roll = target_roll
            self._yaw = target_yaw
            self._last_control = {
                "method": msg.get("method", "gimbal_rotate"),
                "type": "angle" if (pitch is not None or yaw is not None or roll is not None) else "speed",
                "pitch_speed": pitch_speed,
                "roll_speed": roll_speed,
                "yaw_speed": yaw_speed,
            }
            self._notify_payload_state("gimbal_rotate")

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

    def handle_camera_screen_drag(self, msg: dict) -> dict:
        """
        负载控制-画面拖动控制 (camera_screen_drag)

        官方上云接口属于 services 负载控制。对 ViewLink 云台来说，
        画面拖动等价于按 pitch/yaw 角速度移动云台。
        """
        data = msg.get('data', {})
        payload_index = data.get('payload_index', '')
        locked = bool(data.get('locked', False))
        pitch_speed = float(data.get('pitch_speed', 0.0) or 0.0)
        yaw_speed = float(data.get('yaw_speed', 0.0) or 0.0)

        logger.info(
            "Camera screen drag: payload_index=%s locked=%s pitch_speed=%s yaw_speed=%s",
            payload_index,
            locked,
            pitch_speed,
            yaw_speed,
        )

        success = False
        result = {"success": False, "message": "ViewLink control is not connected"}
        if self._gimbal_control and self._gimbal_control.is_connected():
            # ViewLink SDK 使用 0.01 degree/s 为单位，Dock3 文档字段是 degree/s。
            yaw_spd = int(yaw_speed * 100)
            pitch_spd = int(pitch_speed * 100)
            logger.info("ViewLink mapped: %s", build_viewlink_call("move", {
                "yaw_speed": yaw_spd,
                "pitch_speed": pitch_spd,
            }))
            result = self._gimbal_control.move(yaw_spd, pitch_spd)
            success = bool(result.get('success', False))
            logger.info("ViewLink screen drag move: %s", result)

        if success:
            self._last_control = {
                "method": msg.get("method", "camera_screen_drag"),
                "type": "screen_drag",
                "payload_index": payload_index,
                "locked": locked,
                "pitch_speed": pitch_speed,
                "yaw_speed": yaw_speed,
            }
            self._notify_payload_state("camera_screen_drag")

        return MessageBuilder.build_reply(
            msg,
            result=0 if success else -1,
            msg_str="" if success else str(result.get("message", "camera_screen_drag failed")),
            data={
                "payload_index": payload_index,
                "locked": locked,
                "pitch_speed": pitch_speed,
                "yaw_speed": yaw_speed,
                "viewlink_result": result,
            },
        )

    def handle_camera_look_at(self, msg: dict) -> dict:
        """
        目标点指向 - 云台自动计算角度对准目标

        Request: { latitude, longitude, height, aircraft_location }
        """
        data = msg.get('data', {})
        target_lat = data.get('latitude', 0)
        target_lon = data.get('longitude', 0)
        target_alt = data.get('height', 0)

        logger.info(f"Camera look at: lat={target_lat}, lon={target_lon}, alt={target_alt}")

        success, _ = self._send_mapped_gimbal_command('camera_look_at', data)

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

    def handle_set_gimbal_mode(self, msg: dict) -> dict:
        """
        设置云台工作模式

        Request: { mode: FOLLOW/FREE/FPV/RESET }
        """
        data = msg.get('data', {})
        mode_str = str(data.get('mode', 'FOLLOW')).upper()

        mode_map = {
            'FOLLOW': self.MODE_FOLLOW,
            'FREE': self.MODE_FREE,
            'FPV': self.MODE_FPV,
            'RESET': self.MODE_RESET
        }
        target_mode = mode_map.get(mode_str, self.MODE_FOLLOW)

        logger.info(f"Gimbal mode: {mode_str}")

        success, _ = self._send_mapped_gimbal_command('set_gimbal_mode', data)

        # RESET模式下自动回中
        if success and target_mode == self.MODE_RESET:
            success, _ = self._send_mapped_gimbal_command('gimbal_reset', data)

        if success:
            self._mode = target_mode
            self._last_control = {"method": msg.get("method", "set_gimbal_mode"), "mode": mode_str}
            if target_mode == self.MODE_RESET:
                self._pitch = 0
                self._roll = 0
                self._yaw = 0
            self._notify_payload_state("set_gimbal_mode")

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

    # ---- 云台扩展接口 ----

    def handle_gimbal_reset(self, msg: dict) -> dict:
        """云台回中"""
        logger.info("Gimbal reset")

        success = False

        # 优先使用 ViewLink SDK 回中
        if self._gimbal_control and self._gimbal_control.is_connected():
            logger.info("ViewLink mapped: %s", build_viewlink_call("home", msg.get('data', {})))
            result = self._gimbal_control.home()
            success = result.get('success', False)
            logger.info(f"ViewLink home: {result}")

        # 如果 ViewLink 不可用，回退到 MAVLink
        if not success:
            success, _ = self._send_mapped_gimbal_command('gimbal_reset', msg.get('data', {}))

        if success:
            self._pitch = 0
            self._roll = 0
            self._yaw = 0
            self._last_control = {"method": msg.get("method", "gimbal_reset")}
            self._notify_payload_state("gimbal_reset")

        return MessageBuilder.build_reply(msg, result=0 if success else -1)

    def handle_gimbal_capability_query(self, msg: dict) -> dict:
        """云台能力查询"""
        logger.info("Gimbal capability query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'supported_modes': ['FOLLOW', 'FREE', 'FPV', 'RESET'],
            'viewlink_extensions': [
                'zoom_to',
                'focus_in',
                'focus_out',
                'stop_focus',
                'set_focus_mode',
                'get_focus_mode',
                'switch_eo_digital_zoom',
                'ir_digital_zoom_in',
                'ir_digital_zoom_out',
                'switch_defog',
                'is_defog_on',
                'switch_laser',
                'laser_single',
                'laser_zoom_in',
                'laser_zoom_out',
                'laser_stop_zoom',
                'set_laser_zoom_mode',
                'is_tracking',
                'get_record_mode',
            ],
            **GIMBAL_CAPABILITY,
        })

    def handle_gimbal_status_query(self, msg: dict) -> dict:
        """云台状态查询"""
        logger.info("Gimbal status query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'pitch': self._pitch,
            'roll': self._roll,
            'yaw': self._yaw,
            'mode': self._mode,
            'payload': self.build_payload_state(),
        })

    # ---- 红外相机接口 (IR) ----

    def handle_ir_metering_mode_set(self, msg: dict) -> dict:
        """IR测温模式设置"""
        data = msg.get('data', {})
        mode = data.get('mode', 0)
        logger.info(f"IR metering mode: {mode}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_ir_color_palette_set(self, msg: dict) -> dict:
        """IR色板设置"""
        data = msg.get('data', {})
        palette = data.get('palette', 'white_hot')
        logger.info(f"IR color palette: {palette}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_ir_display_mode_set(self, msg: dict) -> dict:
        """IR显示模式设置"""
        data = msg.get('data', {})
        mode = data.get('mode', 0)
        logger.info(f"IR display mode: {mode}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_ir_isotherm_set(self, msg: dict) -> dict:
        """IR等温线设置"""
        data = msg.get('data', {})
        enabled = data.get('enabled', False)
        low = data.get('low_temperature', 0)
        high = data.get('high_temperature', 100)
        logger.info(f"IR isotherm: enabled={enabled}, range=({low}, {high})")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_ir_zoom_set(self, msg: dict) -> dict:
        """IR变焦设置"""
        data = msg.get('data', {})
        ratio = data.get('zoom_ratio', 1.0)
        logger.info(f"IR zoom: {ratio}x")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_viewlink_service(self, msg: dict) -> dict:
        """ViewLink SDK services 入口，方法名支持 viewlink_xxx 或文档原始 SDK 方法名。"""
        method = msg.get('method', '')
        sdk_method = method[len('viewlink_'):] if method.startswith('viewlink_') else method
        data = msg.get('data', {})

        mapping = build_viewlink_call(sdk_method, data)
        result = self._execute_viewlink_call(mapping)
        success = bool(result.get("success", False))
        if success:
            if sdk_method in {"home"}:
                self._pitch = 0
                self._roll = 0
                self._yaw = 0
            elif sdk_method == "turn_to":
                self._yaw = data.get("yaw", self._yaw)
                self._pitch = data.get("pitch", self._pitch)
            self._last_control = {
                "method": method,
                "sdk_method": sdk_method,
                "args": mapping.get("args", {}),
            }
            self._notify_payload_state(method)

        return MessageBuilder.build_reply(
            msg,
            result=0 if success else -1,
            msg_str=result.get("message", ""),
            data={
                "mapping": mapping,
                "viewlink_result": result,
            },
        )
