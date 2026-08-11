"""
云台控制模块
处理云台角度、模式、目标指向
"""
import time
from typing import Any, Dict, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED, DOCK_SYSTEM_ERROR, INVALID_COMMAND_FORMAT
from src.modules.gimbal_control import (
    VLKConnType,
    VLKFocusMode,
    VLKImageType,
    VLKIrColor,
    VLKLaserZoomMode,
    VLKRecordMode,
    VLKSensor,
    VLKTrackTemplateSize,
)
from src.payload.dock3_payload import payload_index_from_config, with_dock3_payload_index
from web.mavlink.gimbal import GIMBAL_CAPABILITY, build_gimbal_command
from web.mavlink.viewlink import (
    build_ir_metering_area_frames,
    build_ir_color_palette_frame,
    build_ir_metering_point_frames,
    build_screen_split_frame,
    build_temperature_output_interval_frame,
    build_temperature_output_once_frame,
    build_temperature_output_stop_frame,
    build_viewlink_call,
    frame_hex,
)

logger = get_logger("gimbal")


IR_DISPLAY_MODE_MAP = {
    "visible": (VLKImageType.VISIBLE1, False),
    "normal": (VLKImageType.VISIBLE1, False),
    "eo": (VLKImageType.VISIBLE1, False),
    "ir": (VLKImageType.IR1, False),
    "infrared": (VLKImageType.IR1, False),
    "thermal": (VLKImageType.IR1, False),
    "visible_ir": (VLKImageType.VISIBLE1, True),
    "eo_ir": (VLKImageType.VISIBLE1, True),
    "ir_visible": (VLKImageType.IR1, True),
    "ir_eo": (VLKImageType.IR1, True),
    "fusion": (VLKImageType.FUSION, False),
}

IR_COLOR_MAP = {
    "white_hot": VLKIrColor.WHITEHOT,
    "whitehot": VLKIrColor.WHITEHOT,
    "black_hot": VLKIrColor.BLACKHOT,
    "blackhot": VLKIrColor.BLACKHOT,
    "pseudo_hot": VLKIrColor.PSEUDOHOT,
    "pseudohot": VLKIrColor.PSEUDOHOT,
    "rainbow": VLKIrColor.PSEUDOHOT,
    "iron_red": VLKIrColor.RUSTY,
    "ironred": VLKIrColor.RUSTY,
    "rusty": VLKIrColor.RUSTY,
}

CAMERA_TYPE_SENSOR_MAP = {
    "visible": VLKSensor.VISIBLE1,
    "normal": VLKSensor.VISIBLE1,
    "wide": VLKSensor.VISIBLE1,
    "zoom": VLKSensor.VISIBLE1,
    "eo": VLKSensor.VISIBLE1,
    "ir": VLKSensor.IR,
    "infrared": VLKSensor.IR,
    "thermal": VLKSensor.IR,
    "fusion": VLKSensor.VISIBLE_IR,
    "visible_ir": VLKSensor.VISIBLE_IR,
    "eo_ir": VLKSensor.VISIBLE_IR,
    "ir_visible": VLKSensor.IR_VISIBLE,
    "ir_eo": VLKSensor.IR_VISIBLE,
}


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
        self._ir_zoom_level = 1.0
        self._last_control: Dict[str, Any] = {}
        self._last_update_time = int(time.time() * 1000)
        self._ai_spotlight_enabled = False
        self._ai_spotlight_selection: Optional[Dict[str, Any]] = None
        self._drc_seq = 0
        self._screen_drag_axis_map = str(
            Config.get("payload.screen_drag_axis_map", "normal") or "normal"
        ).strip().lower()
        self._screen_drag_invert_yaw = self._bool_value(
            Config.get("payload.screen_drag_invert_yaw", False), False
        )
        self._screen_drag_invert_pitch = self._bool_value(
            Config.get("payload.screen_drag_invert_pitch", False), False
        )

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

    @staticmethod
    def _payload_data(data: dict) -> dict:
        """兼容 data.params 包裹和驼峰字段，便于不同前端直接复用云台接口。"""
        if not isinstance(data, dict):
            return {}
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        merged = dict(params)
        merged.update({k: v for k, v in data.items() if k != "params"})
        return merged

    @staticmethod
    def _first_present(data: dict, *names: str):
        for name in names:
            if name in data and data.get(name) is not None:
                return data.get(name)
        return None

    @staticmethod
    def _float_or_none(value):
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float_or_default(value, default: float = 0.0) -> float:
        parsed = GimbalController._float_or_none(value)
        return default if parsed is None else parsed

    @staticmethod
    def _bool_value(value, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}

    @staticmethod
    def _viewlink_speed(value: float) -> int:
        # ViewLink SDK 使用 0.01 degree/s 为单位，Dock3/页面侧使用 degree/s。
        return max(-2000, min(2000, int(float(value or 0.0) * 100)))

    def _screen_drag_viewlink_speeds(self, yaw_speed: float, pitch_speed: float) -> tuple[int, int]:
        """Map screen drag speeds to ViewLink yaw/pitch speeds."""
        yaw_value = float(yaw_speed or 0.0)
        pitch_value = float(pitch_speed or 0.0)
        if self._screen_drag_axis_map == "swap":
            yaw_value, pitch_value = pitch_value, yaw_value
        if self._screen_drag_invert_yaw:
            yaw_value = -yaw_value
        if self._screen_drag_invert_pitch:
            pitch_value = -pitch_value
        return self._viewlink_speed(yaw_value), self._viewlink_speed(pitch_value)

    @staticmethod
    def _move_result_success(result: dict) -> bool:
        return bool(result.get("success", False)) if isinstance(result, dict) else False

    @staticmethod
    def _camera_type_sensor(data: dict) -> VLKSensor:
        if data.get("sensor") is not None:
            return VLKSensor(int(data.get("sensor")))
        camera_type = str(data.get("camera_type") or "").strip().lower()
        return CAMERA_TYPE_SENSOR_MAP.get(camera_type, VLKSensor.VISIBLE1)

    @staticmethod
    def _ir_color_value(value) -> VLKIrColor:
        if value is None:
            return VLKIrColor.WHITEHOT
        text = str(value).strip().lower()
        if text.isdigit():
            try:
                return VLKIrColor(int(text))
            except ValueError:
                return VLKIrColor.WHITEHOT
        return IR_COLOR_MAP.get(text, VLKIrColor.WHITEHOT)

    @staticmethod
    def _zoom_direction_value(direction) -> tuple[int, str]:
        try:
            direction_value = int(direction)
        except (TypeError, ValueError):
            direction_value = 0
        return direction_value, str(direction).strip().lower()

    def _ensure_viewlink_connected(self) -> tuple[bool, dict]:
        if not self._gimbal_control:
            return False, {"success": False, "message": "ViewLink control is not configured"}
        try:
            if (
                hasattr(self._gimbal_control, "connect_in_progress")
                and self._gimbal_control.connect_in_progress()
                and hasattr(self._gimbal_control, "ensure_connected_async")
            ):
                result = self._gimbal_control.ensure_connected_async(timeout=2.0, retry_interval=2.0)
                return False, result
            if self._gimbal_control.is_connected():
                return True, {"success": True, "message": "ViewLink control is connected"}
            if hasattr(self._gimbal_control, "ensure_connected_async"):
                result = self._gimbal_control.ensure_connected_async(timeout=2.0, retry_interval=2.0)
                return bool(result.get("success", False)), result
            return False, {
                "success": False,
                "message": "ViewLink control is not connected; background connect is not supported",
            }
        except Exception as exc:
            logger.warning("Query ViewLink connection failed: %s", exc)
            return False, {"success": False, "message": str(exc)}

    @staticmethod
    def _frame_number(data: dict, key: str, default: float) -> float:
        try:
            value = data.get(key, default)
            return float(default if value is None else value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _frame_size(cls, data: dict) -> dict:
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
            "width": max(1, int(cls._frame_number({"value": width}, "value", 1920))),
            "height": max(1, int(cls._frame_number({"value": height}, "value", 1080))),
        }

    @staticmethod
    def _drc_unit_value(value, default: float) -> float:
        """兼容 DJI DRC 的 0-10000 坐标和本地 0-1 归一化坐标。"""
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = default
        if number > 1.0:
            number = number / 10000.0
        return max(0.0, min(1.0, number))

    @classmethod
    def _frame_point_pixels(cls, data: dict, x_value: float, y_value: float) -> tuple[int, int, dict]:
        frame_size = cls._frame_size(data)
        x = float(x_value or 0.0)
        y = float(y_value or 0.0)
        x_px = int(round(x * frame_size["width"])) if 0.0 <= x <= 1.0 else int(round(x))
        y_px = int(round(y * frame_size["height"])) if 0.0 <= y <= 1.0 else int(round(y))
        x_px = max(0, min(frame_size["width"], x_px))
        y_px = max(0, min(frame_size["height"], y_px))
        return x_px, y_px, frame_size

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

    @staticmethod
    def _reply_with_seq(msg: dict, reply: dict) -> dict:
        """DRC 官方回包保留 seq，兼容不带 tid 的云端控制报文。"""
        if "seq" in msg and "seq" not in reply:
            reply["seq"] = msg.get("seq")
        if not reply.get("gateway"):
            reply["gateway"] = Config.get_gateway_sn()
        return reply

    def _build_drc_ai_reply(self, msg: dict, success: bool, result: dict, data: Optional[dict] = None) -> dict:
        reply_data = data or {}
        if result:
            reply_data.setdefault("output", {
                "status": "ok" if success else "failed",
                "message": result.get("message", ""),
            })
            reply_data.setdefault("viewlink_result", result)
        return self._reply_with_seq(msg, MessageBuilder.build_reply(
            msg,
            result=0 if success else DOCK_SYSTEM_ERROR,
            msg_str="" if success else str(result.get("message", "drc ai spotlight zoom failed")),
            data=reply_data,
        ))

    def _report_drc_ai_info(self, state: int, reason: str = "") -> None:
        """按 Dock3 Remote Control 的 drc_ai_info_push 上报当前跟随状态。"""
        if not self._event_callback:
            return
        self._drc_seq += 1
        self._event_callback({
            "method": "drc_ai_info_push",
            "seq": self._drc_seq,
            "gateway": self.gateway_sn,
            "timestamp": int(time.time() * 1000),
            "data": {
                "identify_on": 0,
                "spotlight_zoom_on": 1 if self._ai_spotlight_enabled else 0,
                "ai_spotlight_zoom": {
                    "state": state,
                    "reason": reason,
                },
            },
        })

    def _drc_ai_selection_from_data(self, data: dict) -> dict:
        width = self._drc_unit_value(data.get("width"), 0.1)
        height = self._drc_unit_value(data.get("height"), 0.1)
        if "center_x" in data or "center_y" in data:
            center_x = self._drc_unit_value(data.get("center_x"), 0.5)
            center_y = self._drc_unit_value(data.get("center_y"), 0.5)
        else:
            left = self._drc_unit_value(data.get("x"), 0.5 - width / 2.0)
            top = self._drc_unit_value(data.get("y"), 0.5 - height / 2.0)
            center_x = max(0.0, min(1.0, left + width / 2.0))
            center_y = max(0.0, min(1.0, top + height / 2.0))
        frame_size = self._frame_size(data)
        x_px, y_px, _ = self._frame_point_pixels(data, center_x, center_y)
        return {
            "center_x": center_x,
            "center_y": center_y,
            "width": width,
            "height": height,
            "x_px": x_px,
            "y_px": y_px,
            "frame_size": frame_size,
            "sensor": int(self._camera_type_sensor(data)),
        }

    def _start_drc_ai_track(self, selection: dict) -> dict:
        connected, connect_result = self._ensure_viewlink_connected()
        if not connected:
            return connect_result
        return self._gimbal_control.start_track(
            selection["x_px"],
            selection["y_px"],
            selection["frame_size"]["width"],
            selection["frame_size"]["height"],
            VLKTrackTemplateSize.AUTO,
            VLKSensor(selection["sensor"]),
        )

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
                return self._gimbal_control.initialize()
            if method == "connect":
                return self._gimbal_control.connect(conn_type=VLKConnType.TCP)
            if method == "is_connected":
                connected = self._gimbal_control.is_connected()
                return {"success": True, "connected": connected}
            if method == "start_track":
                return self._gimbal_control.start_track(
                    args.get("x", 0),
                    args.get("y", 0),
                    args.get("video_width") or args.get("width", 0),
                    args.get("video_height") or args.get("height", 0),
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

    def _send_viewlink_protocol_frames(self, frames: list[bytes], timeout: float = 1.0) -> dict:
        """通过 ViewLink 原始协议发送 SDK 未覆盖的功能帧。"""
        frame_list = [bytes(frame) for frame in frames if frame]
        payload = {
            "protocol": "viewlink",
            "version": "3.4.9",
            "frame_hex": [frame_hex(frame) for frame in frame_list],
        }
        if not frame_list:
            return {**payload, "success": False, "message": "no protocol frames"}
        if self._gimbal_control and hasattr(self._gimbal_control, "send_protocol_frames"):
            result = self._gimbal_control.send_protocol_frames(frame_list, timeout=timeout)
            return {**payload, **result}
        if self._gimbal_control and hasattr(self._gimbal_control, "send_protocol_frame"):
            results = [self._gimbal_control.send_protocol_frame(frame, timeout=timeout) for frame in frame_list]
            success = all(item.get("success", False) for item in results)
            return {
                **payload,
                "success": success,
                "message": "协议帧全部发送" if success else "部分协议帧发送失败",
                "frames": results,
            }
        return {
            **payload,
            "success": False,
            "message": "ViewLink raw protocol adapter is not configured",
        }

    @classmethod
    def _protocol_point(cls, data: dict, x_value: float, y_value: float) -> tuple[int, int, dict]:
        x_px, y_px, frame_size = cls._frame_point_pixels(data, x_value, y_value)
        x = int(round((x_px / max(1, frame_size["width"]) - 0.5) * 1920))
        y = int(round((y_px / max(1, frame_size["height"]) - 0.5) * 1080))
        return max(-960, min(960, x)), max(-540, min(540, y)), frame_size

    def _configured_payload_index(self) -> str:
        gimbal_config = Config.get_payload_config().get("devices", {}).get("gimbal", {})
        return payload_index_from_config(gimbal_config if isinstance(gimbal_config, dict) else {})

    def _reject_ir_request(self, msg: dict, reason: str) -> dict:
        return MessageBuilder.build_reply(
            msg,
            result=INVALID_COMMAND_FORMAT,
            msg_str=reason,
            data={"reason": reason},
        )

    def _validate_ir_payload_index(self, msg: dict, data: dict) -> Optional[dict]:
        requested = str(data.get("payload_index") or "").strip()
        expected = self._configured_payload_index()
        if requested and requested != expected:
            return self._reject_ir_request(
                msg,
                f"payload_index {requested} is not handled by gimbal payload {expected}",
            )
        return None

    @staticmethod
    def _finite_float(value) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number

    @staticmethod
    def _is_ir_coordinate_valid(value: float, pixel_limit: int) -> bool:
        if 0.0 <= value <= 1.0:
            return True
        if value < 0.0 or value > float(pixel_limit):
            return False
        return float(value).is_integer()

    @classmethod
    def _validate_ir_point_input(cls, data: dict) -> tuple[Optional[float], Optional[float], str]:
        frame_size = cls._frame_size(data)
        x = cls._finite_float(data.get("x", 0.5))
        y = cls._finite_float(data.get("y", 0.5))
        if x is None or y is None:
            return None, None, "point x/y must be numeric"
        if not cls._is_ir_coordinate_valid(x, frame_size["width"]):
            return x, y, "point x out of range"
        if not cls._is_ir_coordinate_valid(y, frame_size["height"]):
            return x, y, "point y out of range"
        return x, y, ""

    @classmethod
    def _validate_ir_area_input(cls, data: dict) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], str]:
        frame_size = cls._frame_size(data)
        x = cls._finite_float(data.get("x", 0.0))
        y = cls._finite_float(data.get("y", 0.0))
        width = cls._finite_float(data.get("width", 1.0))
        height = cls._finite_float(data.get("height", 1.0))
        if x is None or y is None or width is None or height is None:
            return x, y, width, height, "area x/y/width/height must be numeric"
        if width <= 0.0 or height <= 0.0:
            return x, y, width, height, "area width/height must be positive"
        if not cls._is_ir_coordinate_valid(x, frame_size["width"]):
            return x, y, width, height, "area x out of range"
        if not cls._is_ir_coordinate_valid(y, frame_size["height"]):
            return x, y, width, height, "area y out of range"
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
            if width > 1.0 or height > 1.0 or x + width > 1.0 or y + height > 1.0:
                return x, y, width, height, "normalized area exceeds frame"
        else:
            if x + width > frame_size["width"] or y + height > frame_size["height"]:
                return x, y, width, height, "pixel area exceeds frame"
        return x, y, width, height, ""

    # ---- API处理器 ----

    def handle_gimbal_rotate(self, msg: dict) -> dict:
        """
        云台角度控制

        Request: { pitch, roll, yaw, pitch_speed, roll_speed, yaw_speed }
        支持绝对角度控制和角速度控制
        """
        data = self._payload_data(msg.get('data', {}))

        # 绝对角度
        pitch = self._float_or_none(self._first_present(data, 'pitch', 'gimbal_pitch', 'gimbalPitch'))
        roll = self._float_or_none(self._first_present(data, 'roll', 'gimbal_roll', 'gimbalRoll'))
        yaw = self._float_or_none(self._first_present(data, 'yaw', 'gimbal_yaw', 'gimbalYaw'))

        # 角速度
        pitch_speed = self._float_or_none(self._first_present(
            data, 'pitch_speed', 'pitchSpeed', 'vertical_speed', 'verticalSpeed'
        ))
        roll_speed = self._float_or_none(self._first_present(data, 'roll_speed', 'rollSpeed'))
        yaw_speed = self._float_or_none(self._first_present(
            data, 'yaw_speed', 'yawSpeed', 'horizontal_speed', 'horizontalSpeed'
        ))
        has_speed = pitch_speed is not None or yaw_speed is not None or roll_speed is not None

        target_pitch = self._pitch if pitch is None else max(-90, min(30, pitch))
        target_roll = self._roll if roll is None else roll
        target_yaw = self._yaw if yaw is None else yaw

        logger.info(f"Gimbal rotate: pitch={pitch}, roll={roll}, yaw={yaw}")
        if pitch_speed is not None:
            logger.info(f"Gimbal speed: p={pitch_speed}, r={roll_speed}, y={yaw_speed}")

        success = False
        result = {"success": False, "message": "ViewLink control is not connected"}

        # 优先使用 ViewLink SDK 云台控制
        connected, connect_result = self._ensure_viewlink_connected()
        if not connected:
            result = connect_result
        if connected:
            if has_speed:
                yaw_spd = self._viewlink_speed(yaw_speed or 0.0)
                pitch_spd = self._viewlink_speed(pitch_speed or 0.0)
                logger.info("ViewLink mapped: %s", build_viewlink_call("move", {
                    "yaw_speed": yaw_spd,
                    "pitch_speed": pitch_spd,
                }))
                if yaw_spd == 0 and pitch_spd == 0:
                    result = self._gimbal_control.stop()
                else:
                    result = self._gimbal_control.move(yaw_spd, pitch_spd)
                success = self._move_result_success(result)
                logger.info(f"ViewLink move: {result}")
            elif pitch is not None or yaw is not None:
                logger.info("ViewLink mapped: %s", build_viewlink_call("turn_to", {
                    "yaw": target_yaw,
                    "pitch": target_pitch,
                }))
                result = self._gimbal_control.turn_to(target_yaw, target_pitch)
                success = self._move_result_success(result)
                logger.info(f"ViewLink turn_to: {result}")

        fallback_allowed = (
            not self._gimbal_control
            or bool(Config.get_payload_config().get('flight_controller_fallback_enabled', False))
        )
        # 角度类指令仅在未配置 ViewLink 或显式允许飞控降级时回落 MAVLink；速度类必须真实命中 ViewLink。
        if fallback_allowed and not success and not has_speed and (pitch is not None or yaw is not None or roll is not None):
            mapped_data = dict(data)
            mapped_data["pitch"] = target_pitch
            mapped_data["roll"] = target_roll
            mapped_data["yaw"] = target_yaw
            success, _ = self._send_mapped_gimbal_command('gimbalRotate', mapped_data)
            if success:
                result = {"success": True, "message": "MAVLink gimbalRotate command sent"}
        elif not success and self._gimbal_control and not has_speed:
            result = {"success": False, "message": "ViewLink control is not connected; MAVLink fallback is disabled"}

        if success:
            self._pitch = target_pitch
            self._roll = target_roll
            self._yaw = target_yaw
            self._last_control = {
                "method": msg.get("method", "gimbal_rotate"),
                "type": "speed" if has_speed else "angle",
                "pitch_speed": pitch_speed,
                "roll_speed": roll_speed,
                "yaw_speed": yaw_speed,
            }
            self._notify_payload_state("gimbal_rotate")

        return MessageBuilder.build_reply(
            msg,
            result=0 if success else -1,
            msg_str="" if success else str(result.get("message", "gimbal_rotate failed")),
            data={"viewlink_result": result},
        )

    def handle_camera_screen_drag(self, msg: dict) -> dict:
        """
        负载控制-画面拖动控制 (camera_screen_drag)

        官方上云接口属于 services 负载控制。对 ViewLink 云台来说，
        画面拖动等价于按 pitch/yaw 角速度移动云台。
        """
        data = self._payload_data(msg.get('data', {}))
        payload_index = data.get('payload_index', '')
        locked = self._bool_value(data.get('locked'), False)
        pitch_speed = self._float_or_default(self._first_present(
            data, 'pitch_speed', 'pitchSpeed', 'vertical_speed', 'verticalSpeed'
        ))
        yaw_speed = self._float_or_default(self._first_present(
            data, 'yaw_speed', 'yawSpeed', 'horizontal_speed', 'horizontalSpeed'
        ))

        logger.info(
            "Camera screen drag: payload_index=%s locked=%s pitch_speed=%s yaw_speed=%s",
            payload_index,
            locked,
            pitch_speed,
            yaw_speed,
        )

        success = False
        result = {"success": False, "message": "ViewLink control is not connected"}
        connected, connect_result = self._ensure_viewlink_connected()
        if not connected:
            result = connect_result
        if connected:
            yaw_spd, pitch_spd = self._screen_drag_viewlink_speeds(yaw_speed, pitch_speed)
            logger.info("ViewLink mapped: %s", build_viewlink_call("move", {
                "yaw_speed": yaw_spd,
                "pitch_speed": pitch_spd,
            }))
            if yaw_spd == 0 and pitch_spd == 0:
                result = self._gimbal_control.stop()
            else:
                result = self._gimbal_control.move(yaw_spd, pitch_spd)
            success = self._move_result_success(result)
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
                "mapped_yaw_speed": yaw_spd if connected else 0,
                "mapped_pitch_speed": pitch_spd if connected else 0,
                "axis_map": self._screen_drag_axis_map,
                "invert_yaw": self._screen_drag_invert_yaw,
                "invert_pitch": self._screen_drag_invert_pitch,
                "viewlink_result": result,
            },
        )

    def handle_camera_aim(self, msg: dict) -> dict:
        """双击成为 AIM (camera_aim)，属于 services 负载控制。"""
        data = msg.get('data', {})
        logger.info(
            "Payload services method received: method=camera_aim tid=%s bid=%s payload_index=%s",
            msg.get("tid", ""),
            msg.get("bid", ""),
            data.get("payload_index", ""),
        )
        locked = bool(data.get("locked", False))
        x = float(data.get("x", 0.5) or 0.5)
        y = float(data.get("y", 0.5) or 0.5)
        logger.info(
            "Camera AIM: payload_index=%s camera_type=%s locked=%s point=(%s,%s)",
            data.get("payload_index", ""),
            data.get("camera_type", ""),
            locked,
            x,
            y,
        )

        result = {"success": False, "message": "ViewLink control is not connected"}
        if not self._gimbal_control or not self._gimbal_control.is_connected():
            return MessageBuilder.build_reply(
                msg,
                result=DEVICE_STATE_UNSUPPORTED,
                msg_str="ViewLink control is not connected",
            )
        if locked:
            x_px, y_px, frame_size = self._frame_point_pixels(data, x, y)
            sensor = self._camera_type_sensor(data)
            mapping = build_viewlink_call("start_track", {
                "x": x_px,
                "y": y_px,
                "video_width": frame_size["width"],
                "video_height": frame_size["height"],
                "template_size": data.get("template_size"),
                "sensor": int(sensor),
            })
            logger.info("ViewLink mapped: %s", mapping)
            result = self._execute_viewlink_call(mapping)
        else:
            if hasattr(self._gimbal_control, "stop_track"):
                result = self._gimbal_control.stop_track()
            else:
                result = {
                    "success": False,
                    "message": "ViewLink stop_track is not exposed by this adapter",
                }

        success = bool(result.get("success", False))
        if success:
            self._last_control = {
                "method": msg.get("method", "camera_aim"),
                "payload_index": data.get("payload_index", ""),
                "locked": locked,
                "x": x,
                "y": y,
            }
            self._notify_payload_state("camera_aim")

        return MessageBuilder.build_reply(msg, result=0 if success else -1, data={
            "locked": locked,
            "x": x,
            "y": y,
            "viewlink_result": result,
        })

    def handle_camera_screen_split(self, msg: dict) -> dict:
        """分屏控制 (camera_screen_split)，属于 services 负载控制。"""
        data = msg.get('data', {})
        enabled = bool(data.get("enable", data.get("enabled", False)))
        mode = data.get("mode") or data.get("display_mode") or ("visible_ir" if enabled else "visible")
        mode_key = str(mode).lower()
        palette = data.get("palette", data.get("ir_color", "white_hot"))
        logger.info(
            "Camera screen split: payload_index=%s enabled=%s mode=%s palette=%s",
            data.get("payload_index", ""),
            enabled,
            mode_key,
            palette,
        )

        result = {"success": False, "message": "ViewLink control is not connected"}
        image_type = None
        enable_pip = False
        ir_color = self._ir_color_value(palette)
        if self._gimbal_control and self._gimbal_control.is_connected():
            image_type, enable_pip = IR_DISPLAY_MODE_MAP.get(
                mode_key,
                IR_DISPLAY_MODE_MAP["visible_ir" if enabled else "visible"],
            )
            result = self._gimbal_control.set_image_color(image_type, enable_pip, ir_color)
        else:
            frames = [build_screen_split_frame(mode_key)]
            if str(palette or "").strip():
                frames.append(build_ir_color_palette_frame(palette))
            result = self._send_viewlink_protocol_frames(frames)

        success = bool(result.get("success", False))
        if success:
            self._last_control = {
                "method": msg.get("method", "camera_screen_split"),
                "payload_index": data.get("payload_index", ""),
                "enable": enabled,
                "mode": mode_key,
                "ir_color": int(ir_color),
            }
            self._notify_payload_state("camera_screen_split")

        return MessageBuilder.build_reply(msg, result=0 if success else -1, data={
            "payload_index": data.get("payload_index", ""),
            "enable": enabled,
            "mode": mode_key,
            "image_type": int(image_type) if image_type is not None else None,
            "enable_pip": enable_pip,
            "ir_color": int(ir_color),
            "viewlink_result": result,
        })

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
                'eo_digital_zoom_stop',
                'ir_digital_zoom_in',
                'ir_digital_zoom_out',
                'ir_digital_zoom_stop',
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

    # ---- Dock3 Remote Control AI 跟随适配 ----

    def handle_drc_ai_spotlight_zoom_set(self, msg: dict) -> dict:
        """AI 跟随开关。当前用 ViewLink 跟踪能力承接，不开启独立 AI 识别模型。"""
        data = self._payload_data(msg.get('data', {}))
        on = self._bool_value(data.get("on"), False)
        logger.info("DRC AI spotlight zoom set: on=%s", on)

        connected, connect_result = self._ensure_viewlink_connected()
        if not connected:
            return self._build_drc_ai_reply(msg, False, connect_result, {"on": 1 if on else 0})

        result = {"success": True, "message": "AI spotlight zoom enabled by ViewLink track adapter"}
        if not on:
            if hasattr(self._gimbal_control, "stop_track"):
                result = self._gimbal_control.stop_track()
            else:
                result = {"success": False, "message": "ViewLink stop_track is not exposed by this adapter"}
        success = bool(result.get("success", False))
        if success:
            self._ai_spotlight_enabled = on
            if not on:
                self._ai_spotlight_selection = None
            self._last_control = {
                "method": msg.get("method", "drc_ai_spotlight_zoom_set"),
                "on": 1 if on else 0,
            }
            self._notify_payload_state("drc_ai_spotlight_zoom_set")
            self._report_drc_ai_info(0 if not on else 1, "enabled" if on else "disabled")
        return self._build_drc_ai_reply(msg, success, result, {"on": 1 if on else 0})

    def handle_drc_ai_spotlight_zoom_select(self, msg: dict) -> dict:
        """AI 框选目标跟随。ViewLink 无候选确认阶段，先缓存框选并立即尝试跟踪。"""
        data = self._payload_data(msg.get('data', {}))
        selection = self._drc_ai_selection_from_data(data)
        self._ai_spotlight_enabled = True
        self._ai_spotlight_selection = selection
        logger.info("DRC AI spotlight select: %s", selection)

        result = self._start_drc_ai_track(selection)
        success = bool(result.get("success", False))
        if success:
            self._last_control = {
                "method": msg.get("method", "drc_ai_spotlight_zoom_select"),
                "selection": selection,
            }
            self._notify_payload_state("drc_ai_spotlight_zoom_select")
            self._report_drc_ai_info(2, "target_selected")
        return self._build_drc_ai_reply(msg, success, result, {"selection": selection})

    def handle_drc_ai_spotlight_zoom_confirm(self, msg: dict) -> dict:
        """AI 框选目标跟随确认。确认时对最近一次框选目标再次下发 ViewLink 跟踪。"""
        if not self._ai_spotlight_selection:
            reply = MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str="no selected AI spotlight zoom target",
                data={"output": {"status": "failed", "message": "no selected AI spotlight zoom target"}},
            )
            return self._reply_with_seq(msg, reply)

        result = self._start_drc_ai_track(self._ai_spotlight_selection)
        success = bool(result.get("success", False))
        if success:
            self._last_control = {
                "method": msg.get("method", "drc_ai_spotlight_zoom_confirm"),
                "selection": dict(self._ai_spotlight_selection),
            }
            self._notify_payload_state("drc_ai_spotlight_zoom_confirm")
            self._report_drc_ai_info(2, "target_confirmed")
        return self._build_drc_ai_reply(
            msg,
            success,
            result,
            {"selection": dict(self._ai_spotlight_selection)},
        )

    def handle_drc_ai_spotlight_zoom_track(self, msg: dict) -> dict:
        """
        AI 识别目标跟随。

        当前没有真实 AI 目标列表，不能仅凭 target_index 命中目标；如报文携带
        框选坐标则按框选跟踪，否则复用最近一次框选目标。
        """
        data = self._payload_data(msg.get('data', {}))
        if any(key in data for key in ("center_x", "center_y", "x", "y")):
            self._ai_spotlight_selection = self._drc_ai_selection_from_data(data)
        if not self._ai_spotlight_selection:
            reply = MessageBuilder.build_reply(
                msg,
                result=DEVICE_STATE_UNSUPPORTED,
                msg_str="target_index requires a real AI target list, select a target box first",
                data={
                    "target_index": data.get("target_index"),
                    "output": {
                        "status": "failed",
                        "message": "target_index requires a real AI target list, select a target box first",
                    },
                },
            )
            return self._reply_with_seq(msg, reply)

        self._ai_spotlight_enabled = True
        result = self._start_drc_ai_track(self._ai_spotlight_selection)
        success = bool(result.get("success", False))
        if success:
            self._last_control = {
                "method": msg.get("method", "drc_ai_spotlight_zoom_track"),
                "target_index": data.get("target_index"),
                "selection": dict(self._ai_spotlight_selection),
            }
            self._notify_payload_state("drc_ai_spotlight_zoom_track")
            self._report_drc_ai_info(2, "tracking")
        return self._build_drc_ai_reply(
            msg,
            success,
            result,
            {
                "target_index": data.get("target_index"),
                "selection": dict(self._ai_spotlight_selection),
            },
        )

    def handle_drc_ai_spotlight_zoom_stop(self, msg: dict) -> dict:
        """停止目标跟随。"""
        logger.info("DRC AI spotlight stop")
        connected, connect_result = self._ensure_viewlink_connected()
        if not connected:
            return self._build_drc_ai_reply(msg, False, connect_result)
        if not hasattr(self._gimbal_control, "stop_track"):
            return self._build_drc_ai_reply(
                msg,
                False,
                {"success": False, "message": "ViewLink stop_track is not exposed by this adapter"},
            )

        result = self._gimbal_control.stop_track()
        success = bool(result.get("success", False))
        if success:
            self._ai_spotlight_enabled = False
            self._ai_spotlight_selection = None
            self._last_control = {"method": msg.get("method", "drc_ai_spotlight_zoom_stop")}
            self._notify_payload_state("drc_ai_spotlight_zoom_stop")
            self._report_drc_ai_info(0, "stopped")
        return self._build_drc_ai_reply(msg, success, result)

    # ---- 红外相机接口 (IR) ----

    def handle_ir_metering_mode_set(self, msg: dict) -> dict:
        """IR测温模式设置"""
        data = msg.get('data', {})
        payload_error = self._validate_ir_payload_index(msg, data)
        if payload_error:
            return payload_error
        mode = str(data.get('mode', data.get("metering_mode", 0))).lower()
        interval = data.get("interval_frames", data.get("interval"))
        logger.info("IR metering mode: %s interval=%s", mode, interval)
        if mode in {"off", "stop", "0"}:
            frames = [build_temperature_output_stop_frame()]
        elif mode in {"periodic", "interval", "continuous", "2"}:
            frames = [build_temperature_output_interval_frame(int(interval or 2))]
        else:
            frames = [build_temperature_output_once_frame()]
        result = self._send_viewlink_protocol_frames(frames)
        return MessageBuilder.build_reply(msg, result=0 if result.get("success") else -1, data={
            "mode": mode,
            "interval_frames": int(interval or 0) if interval is not None else None,
            "viewlink_result": result,
        })

    def handle_ir_metering_point_set(self, msg: dict) -> dict:
        """IR 点测温设置 (ir_metering_point_set)，属于 services 负载控制。"""
        data = msg.get('data', {})
        payload_error = self._validate_ir_payload_index(msg, data)
        if payload_error:
            return payload_error
        raw_x, raw_y, validation_error = self._validate_ir_point_input(data)
        if validation_error:
            logger.warning(
                "IR metering point rejected: payload_index=%s x=%s y=%s reason=%s",
                data.get("payload_index", ""),
                data.get("x"),
                data.get("y"),
                validation_error,
            )
            return self._reject_ir_request(msg, validation_error)
        protocol_x, protocol_y, frame_size = self._protocol_point(data, raw_x, raw_y)
        logger.info(
            "IR metering point: payload_index=%s point=(%s,%s) protocol=(%s,%s)",
            data.get("payload_index", ""),
            raw_x,
            raw_y,
            protocol_x,
            protocol_y,
        )
        result = self._send_viewlink_protocol_frames(build_ir_metering_point_frames(protocol_x, protocol_y))
        if result.get("success"):
            self._last_control = {
                "method": msg.get("method", "ir_metering_point_set"),
                "x": raw_x,
                "y": raw_y,
                "protocol_x": protocol_x,
                "protocol_y": protocol_y,
            }
            self._notify_payload_state("ir_metering_point_set")
        return MessageBuilder.build_reply(msg, result=0 if result.get("success") else -1, data={
            "x": raw_x,
            "y": raw_y,
            "protocol_point": {"x": protocol_x, "y": protocol_y},
            "frame_size": frame_size,
            "viewlink_result": result,
        })

    def handle_ir_metering_area_set(self, msg: dict) -> dict:
        """IR 区域测温设置 (ir_metering_area_set)，属于 services 负载控制。"""
        data = msg.get('data', {})
        payload_error = self._validate_ir_payload_index(msg, data)
        if payload_error:
            return payload_error
        x, y, width, height, validation_error = self._validate_ir_area_input(data)
        if validation_error:
            logger.warning(
                "IR metering area rejected: payload_index=%s x=%s y=%s width=%s height=%s reason=%s",
                data.get("payload_index", ""),
                data.get("x"),
                data.get("y"),
                data.get("width"),
                data.get("height"),
                validation_error,
            )
            return self._reject_ir_request(msg, validation_error)
        left, top, frame_size = self._protocol_point(data, x, y)
        right, bottom, _ = self._protocol_point(data, x + width, y + height)
        logger.info(
            "IR metering area: payload_index=%s area=(%s,%s,%s,%s) protocol=(%s,%s,%s,%s)",
            data.get("payload_index", ""),
            x,
            y,
            width,
            height,
            left,
            top,
            right,
            bottom,
        )
        result = self._send_viewlink_protocol_frames(build_ir_metering_area_frames(left, top, right, bottom))
        if result.get("success"):
            self._last_control = {
                "method": msg.get("method", "ir_metering_area_set"),
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "protocol_area": {"left": left, "top": top, "right": right, "bottom": bottom},
            }
            self._notify_payload_state("ir_metering_area_set")
        return MessageBuilder.build_reply(msg, result=0 if result.get("success") else -1, data={
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "protocol_area": {"left": left, "top": top, "right": right, "bottom": bottom},
            "frame_size": frame_size,
            "viewlink_result": result,
        })

    def handle_ir_color_palette_set(self, msg: dict) -> dict:
        """IR色板设置"""
        data = msg.get('data', {})
        palette = data.get('palette', data.get("ir_color", "white_hot"))
        mode = data.get("mode", data.get("display_mode", "ir"))
        mode_key = str(mode).lower()
        if mode_key.isdigit():
            mode_key = {
                0: "visible",
                1: "ir",
                2: "visible_ir",
                3: "ir_visible",
                4: "fusion",
            }.get(int(mode_key), "ir")
        image_type, enable_pip = IR_DISPLAY_MODE_MAP.get(mode_key, IR_DISPLAY_MODE_MAP["ir"])
        if "image_type" in data:
            image_type = VLKImageType(int(data.get("image_type")))
        if "enable_pip" in data:
            enable_pip = bool(data.get("enable_pip"))
        ir_color = self._ir_color_value(palette)
        logger.info(
            "IR color palette: palette=%s image_type=%s enable_pip=%s",
            palette,
            int(image_type),
            enable_pip,
        )

        result = {"success": False, "message": "ViewLink control is not connected"}
        if self._gimbal_control and self._gimbal_control.is_connected():
            result = self._gimbal_control.set_image_color(image_type, enable_pip, ir_color)

        success = bool(result.get("success", False))
        if success:
            self._last_control = {
                "method": msg.get("method", "ir_color_palette_set"),
                "mode": mode_key,
                "image_type": int(image_type),
                "enable_pip": enable_pip,
                "ir_color": int(ir_color),
            }
            self._notify_payload_state("ir_color_palette_set")

        return MessageBuilder.build_reply(
            msg,
            result=0 if success else -1,
            msg_str="" if success else str(result.get("message", "ir_color_palette_set failed")),
            data={
                "mode": mode_key,
                "image_type": int(image_type),
                "enable_pip": enable_pip,
                "ir_color": int(ir_color),
                "viewlink_result": result,
            },
        )

    def handle_ir_display_mode_set(self, msg: dict) -> dict:
        """IR显示模式设置"""
        data = msg.get('data', {})
        mode = data.get('mode', data.get('display_mode', 'ir'))
        mode_key = str(mode).lower()
        if mode_key.isdigit():
            numeric_mode = int(mode_key)
            mode_key = {
                0: "visible",
                1: "ir",
                2: "visible_ir",
                3: "ir_visible",
                4: "fusion",
            }.get(numeric_mode, "ir")

        image_type, enable_pip = IR_DISPLAY_MODE_MAP.get(mode_key, IR_DISPLAY_MODE_MAP["ir"])
        if "image_type" in data:
            image_type = VLKImageType(int(data.get("image_type")))
        if "enable_pip" in data:
            enable_pip = bool(data.get("enable_pip"))

        palette = str(data.get("palette", data.get("ir_color", "white_hot"))).lower()
        ir_color = IR_COLOR_MAP.get(palette, VLKIrColor.WHITEHOT)
        logger.info(
            "IR display mode: mode=%s image_type=%s enable_pip=%s ir_color=%s",
            mode,
            int(image_type),
            enable_pip,
            int(ir_color),
        )

        result = {"success": False, "message": "ViewLink control is not connected"}
        if self._gimbal_control and self._gimbal_control.is_connected():
            result = self._gimbal_control.set_image_color(image_type, enable_pip, ir_color)

        success = bool(result.get("success", False))
        if success:
            self._last_control = {
                "method": msg.get("method", "ir_display_mode_set"),
                "mode": mode_key,
                "image_type": int(image_type),
                "enable_pip": enable_pip,
                "ir_color": int(ir_color),
            }
            self._notify_payload_state("ir_display_mode_set")

        return MessageBuilder.build_reply(
            msg,
            result=0 if success else -1,
            msg_str="" if success else str(result.get("message", "ir_display_mode_set failed")),
            data={
                "mode": mode_key,
                "image_type": int(image_type),
                "enable_pip": enable_pip,
                "ir_color": int(ir_color),
                "viewlink_result": result,
            },
        )

    def handle_ir_isotherm_set(self, msg: dict) -> dict:
        """IR等温线设置"""
        data = msg.get('data', {})
        enabled = data.get('enabled', False)
        low = data.get('low_temperature', 0)
        high = data.get('high_temperature', 100)
        logger.info(f"IR isotherm: enabled={enabled}, range=({low}, {high})")
        return MessageBuilder.build_reply(
            msg,
            result=DEVICE_STATE_UNSUPPORTED,
            msg_str="ViewLink SDK/protocol mapping does not expose IR isotherm control",
            data={
                "enabled": enabled,
                "low_temperature": low,
                "high_temperature": high,
                "supported": False,
            },
        )

    def handle_ir_zoom_set(self, msg: dict) -> dict:
        """IR变焦设置"""
        data = msg.get('data', {})
        ratio = data.get('zoom_ratio', 1.0)
        direction = data.get("zoom_direction")
        speed = int(data.get("zoom_speed", data.get("speed", 0)) or 0)
        logger.info(f"IR zoom: {ratio}x")

        result = {"success": False, "message": "ViewLink control is not connected"}
        if self._gimbal_control and self._gimbal_control.is_connected():
            if direction is not None:
                direction_value, direction_text = self._zoom_direction_value(direction)
                zooming_in = direction_value > 0 or direction_text in {"in", "zoom_in", "tele"}
                zooming_out = direction_value < 0 or direction_text in {"out", "zoom_out", "wide"}
            else:
                try:
                    target_ratio = float(ratio)
                except (TypeError, ValueError):
                    target_ratio = self._ir_zoom_level
                zooming_in = target_ratio > self._ir_zoom_level
                zooming_out = target_ratio < self._ir_zoom_level
            if zooming_in:
                result = self._gimbal_control.ir_digital_zoom_in(speed)
            elif zooming_out:
                result = self._gimbal_control.ir_digital_zoom_out(speed)
            else:
                if hasattr(self._gimbal_control, "ir_digital_zoom_stop"):
                    result = self._gimbal_control.ir_digital_zoom_stop()
                else:
                    result = {
                        "success": False,
                        "message": "IR digital zoom stop is not exposed by this adapter",
                    }

        success = bool(result.get("success", False))
        if success:
            try:
                self._ir_zoom_level = float(ratio)
            except (TypeError, ValueError):
                pass
            self._last_control = {
                "method": msg.get("method", "ir_zoom_set"),
                "zoom_ratio": self._ir_zoom_level,
                "zoom_direction": direction,
            }
            self._notify_payload_state("ir_zoom_set")

        return MessageBuilder.build_reply(
            msg,
            result=0 if success else -1,
            msg_str="" if success else str(result.get("message", "ir_zoom_set failed")),
            data={
                "zoom_ratio": self._ir_zoom_level,
                "zoom_direction": direction,
                "viewlink_result": result,
            },
        )

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
