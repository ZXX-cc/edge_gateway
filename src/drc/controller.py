"""
DRC(直接遥控)控制模块
处理上云API的DRC实时控制指令
参考大疆Pilot 2 / Cloud API DRC接口
"""
import math
import time
import threading
from typing import Optional, Callable
from pymavlink import mavutil
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.utils.field_labels import format_with_labels
from src.utils.dji_error_codes import (
    DEVICE_COMMAND_TIMEOUT,
    DEVICE_STATE_UNSUPPORTED,
    DOCK_COMMUNICATION_ERROR,
    INVALID_COMMAND_FORMAT,
)
from src.cloud_api.dock3_drc import (
    normalize_drone_control,
    normalize_stick_control,
    validate_drc_heartbeat,
    validate_drc_mode_enter,
    validate_gimbal_speed_push,
)
from src.cloud_api.dock3_point_flight import (
    normalize_fly_to_point_update,
    normalize_takeoff_to_point,
)

logger = get_logger("drc")


class DrcController:
    """
    DRC控制器

    职责:
    1. DRC模式进入/退出
    2. 实时速度/航向/高度控制
    3. 指点飞行DRC推送
    4. DRC上行数据上报

    API方法:
    - drc_mode_enter, drc_mode_exit
    - drc_speed_push, drc_heading_push, drc_altitude_push
    - drc_flyto_point_push
    """

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()

        # DRC状态
        self._drc_enabled = False
        self._session_id = ""
        self._original_mode = None  # 进入DRC前的原模式（用于退出恢复）

        # 最新控制指令缓存
        self._last_speed_cmd = None
        self._last_heading_cmd = None
        self._last_altitude_cmd = None
        self._last_flyto_cmd = None
        self._active_fly_to_point = None
        self._active_takeoff_to_point = None
        self._flight_authority_grabbed = False

        # MAVLink模块引用(由外部注入)
        self._mode_control = None
        self._flight_control = None
        self._mission_manager = None  # 任务管理器引用
        self._telemetry_module = None  # 遥测模块引用
        self._gimbal_control = None  # 云台控制引用
        self._camera_controller = None  # 相机控制器引用
        self._point_flight_controller = None  # services 指点飞行业务控制器

        # 回调
        self._event_callback: Optional[Callable] = None

        # 心跳保活机制
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_running = False
        self._last_cloud_heartbeat = 0  # 云端心跳时间戳
        self._last_cloud_heartbeat_seq = None  # 云端心跳序号
        self._last_device_heartbeat = 0  # 设备心跳时间戳
        self._heartbeat_timeout = 30  # 心跳超时时间（秒）

        # 指令限流
        self._speed_cmd_timestamps = []  # 速度指令时间戳列表
        self._speed_cmd_rate_limit = 20  # 最大频率（Hz）
        self._last_speed_cmd_time = 0

        # 边界校验配置
        self._altitude_min = 5  # 最小高度（米）
        self._altitude_max = 500  # 最大高度（米）
        self._speed_max = 20  # 最大速度（m/s）
        self._geofence_radius = 2000  # 电子围栏半径（米）
        self._guided_loiter_radius = Config.get('device.drc.guided_loiter_radius', 80.0)

    def set_event_callback(self, callback: Callable):
        """设置事件上报回调"""
        self._event_callback = callback

    def set_mode_control(self, mode_control):
        """注入模式控制模块"""
        self._mode_control = mode_control

    def set_flight_control(self, flight_control):
        """注入飞行控制模块"""
        self._flight_control = flight_control

    def set_mission_manager(self, mission_manager):
        """注入任务管理器模块"""
        self._mission_manager = mission_manager

    def set_telemetry_module(self, telemetry_module):
        """注入遥测模块"""
        self._telemetry_module = telemetry_module

    def set_gimbal_control(self, gimbal_control):
        """注入云台控制模块"""
        self._gimbal_control = gimbal_control

    def set_camera_controller(self, camera_controller):
        """注入相机控制器"""
        self._camera_controller = camera_controller

    def set_point_flight_controller(self, point_flight_controller):
        """注入 services-topic 指点飞行业务控制器。"""
        self._point_flight_controller = point_flight_controller

    def _get_mavlink_connection(self):
        """获取MAVLink连接对象"""
        if self._mode_control and hasattr(self._mode_control, 'dispatcher'):
            dispatcher = self._mode_control.dispatcher
            if dispatcher and hasattr(dispatcher, 'connection'):
                return dispatcher.connection
        if self._flight_control and hasattr(self._flight_control, 'dispatcher'):
            dispatcher = self._flight_control.dispatcher
            if dispatcher and hasattr(dispatcher, 'connection'):
                return dispatcher.connection
        return None

    def _get_mav_sender(self):
        """获取可发送 MAVLink 消息的 sender 对象。"""
        conn = self._get_mavlink_connection()
        if not conn:
            return None, None
        master = getattr(conn, "master", None)
        if master and hasattr(master, "mav"):
            return master.mav, master
        if hasattr(conn, "mav"):
            return conn.mav, conn
        return None, conn

    def _has_real_flight_adapter(self) -> bool:
        if self._mode_control and hasattr(self._mode_control, "set_mode"):
            return True
        mav, _ = self._get_mav_sender()
        return bool(mav)

    def _real_adapter_unavailable(self, msg: dict, reason: str) -> dict:
        logger.warning(
            "Dock3 DRC real adapter unavailable: method=%s, reason=%s",
            msg.get("method"),
            reason,
        )
        return MessageBuilder.build_reply(
            msg,
            result=DOCK_COMMUNICATION_ERROR,
            msg_str=reason,
            data={"output": {"status": "failed"}},
        )

    def _send_mavlink_message(self, msg) -> bool:
        """发送MAVLink消息"""
        conn = self._get_mavlink_connection()
        if conn and hasattr(conn, 'send_message'):
            conn.send_message(msg)
            return True
        logger.warning("MAVLink not connected, cannot send DRC command")
        return False

    def _send_body_velocity(self, vx: float, vy: float, vz: float, yaw_rate: float = 0.0) -> tuple:
        """
        发送机体系速度控制。

        Returns:
            (sent, simulated, error)
        """
        mav, master = self._get_mav_sender()
        if not mav or not hasattr(mav, "set_position_target_local_ned_send"):
            logger.warning("MAVLink sender not available, DRC velocity command not sent")
            return False, False, "MAVLink sender not available"

        try:
            mav.set_position_target_local_ned_send(
                0,
                getattr(master, "target_system", 1),
                getattr(master, "target_component", 1),
                mavutil.mavlink.MAV_FRAME_BODY_NED,
                0b0000111111000111,
                0, 0, 0,
                vx, vy, vz,
                0, 0, 0,
                0, yaw_rate,
            )
            return True, False, ""
        except Exception as exc:
            logger.error("DRC velocity send failed: %s", exc, exc_info=True)
            return False, False, str(exc)

    def _send_zero_velocity(self) -> tuple:
        """发送零速度，作为退出 DRC 或急停前的刹车/悬停动作。"""
        return self._send_body_velocity(0.0, 0.0, 0.0, 0.0)

    def _set_safe_mode(self) -> tuple:
        """切到安全悬停模式，优先 LOITER，失败后尝试 QLOITER。"""
        if not self._mode_control or not hasattr(self._mode_control, "set_mode"):
            return False, False, "mode_control not available"

        last_message = ""
        for mode in ("LOITER", "QLOITER"):
            try:
                result = self._mode_control.set_mode(mode)
            except Exception as exc:
                last_message = str(exc)
                logger.warning("DRC safe mode %s failed: %s", mode, exc)
                continue
            if result.get("success"):
                return True, False, mode
            last_message = result.get("message", f"{mode} failed")
        return False, False, last_message

    def _normalize_loiter_radius(self, radius) -> Optional[float]:
        try:
            if radius is None or isinstance(radius, bool):
                return None
            radius = float(radius)
        except (TypeError, ValueError):
            return None
        if radius == 0.0 or math.isnan(radius) or math.isinf(radius):
            return None
        return radius

    def _request_aircraft_parameter(self, param_name: str, timeout: float = 0.8) -> Optional[float]:
        """Read one MAVLink parameter from the aircraft with a short timeout."""
        mav, master = self._get_mav_sender()
        if not mav or not hasattr(mav, "param_request_read_send"):
            return None

        try:
            mav.param_request_read_send(
                getattr(master, "target_system", 1),
                getattr(master, "target_component", 1),
                param_name.encode("ascii"),
                -1,
            )
        except Exception as exc:
            logger.debug("MAVLink param request failed for %s: %s", param_name, exc)
            return None

        receiver = master if hasattr(master, "recv_match") else self._get_mavlink_connection()
        if not receiver or not hasattr(receiver, "recv_match"):
            return None

        deadline = time.time() + max(0.05, timeout)
        while time.time() < deadline:
            try:
                msg = receiver.recv_match(
                    type="PARAM_VALUE",
                    blocking=True,
                    timeout=max(0.05, min(0.2, deadline - time.time())),
                )
            except Exception as exc:
                logger.debug("MAVLink param receive failed for %s: %s", param_name, exc)
                return None
            if not msg:
                continue
            msg_param_id = getattr(msg, "param_id", "")
            if isinstance(msg_param_id, bytes):
                msg_param_id = msg_param_id.decode("ascii", errors="ignore")
            msg_param_id = str(msg_param_id).strip("\x00")
            if msg_param_id != param_name:
                continue
            return self._normalize_loiter_radius(getattr(msg, "param_value", None))
        return None

    def _resolve_guided_loiter_radius(self) -> float:
        aircraft_radius = self._request_aircraft_parameter("WP_LOITER_RAD")
        if aircraft_radius is not None:
            return aircraft_radius

        configured = self._normalize_loiter_radius(self._guided_loiter_radius)
        return configured if configured is not None else 80.0

    def _send_guided_loiter_target(
        self,
        latitude: float,
        longitude: float,
        height: float,
    ) -> dict:
        """固定翼指点飞行：切 GUIDED 后发送到点盘旋目标。"""
        if not self._mode_control:
            return {"success": False, "message": "mode_control not available"}

        if hasattr(self._mode_control, "set_mode"):
            mode_result = self._mode_control.set_mode("GUIDED")
            if not mode_result.get("success"):
                return {
                    "success": False,
                    "message": mode_result.get("message", "GUIDED mode switch failed"),
                }

        if hasattr(self._mode_control, "guided_loiter_at_point"):
            radius = self._resolve_guided_loiter_radius()
            result = self._mode_control.guided_loiter_at_point(
                latitude,
                longitude,
                height,
                radius=radius,
            )
            return result
        if hasattr(self._mode_control, "guided_goto"):
            return self._mode_control.guided_goto(latitude, longitude, height)
        return {"success": False, "message": "guided flight adapter not available"}

    def _get_current_point_flight_telemetry(self) -> tuple:
        """读取当前位置，用于指点飞行急停后的当前位置盘旋。"""
        if not self._telemetry_module or not hasattr(self._telemetry_module, "get_telemetry_data"):
            return None, "telemetry module not available"

        try:
            telemetry = self._telemetry_module.get_telemetry_data()
        except Exception as exc:
            logger.warning("DRC emergency hold telemetry unavailable: %s", exc)
            return None, str(exc)

        current_lat = telemetry.get("latitude")
        current_lon = telemetry.get("longitude")
        if current_lat is None or current_lon is None:
            return None, "current latitude/longitude unavailable"

        try:
            current_lat = float(current_lat)
            current_lon = float(current_lon)
        except (TypeError, ValueError):
            return None, "current latitude/longitude invalid"

        if not (-90 <= current_lat <= 90 and -180 <= current_lon <= 180):
            return None, "current latitude/longitude out of range"
        if current_lat == 0.0 and current_lon == 0.0:
            return None, "current latitude/longitude unavailable"

        return telemetry, ""

    def _active_point_flight_target(self) -> tuple:
        """返回当前指点飞行目标；没有 active 任务时返回 None。"""
        if self._active_fly_to_point:
            points = self._active_fly_to_point.get("points") or []
            if points:
                return "fly_to_point", self._active_fly_to_point, points[0]

        if self._active_takeoff_to_point:
            target = self._active_takeoff_to_point.get("target") or {}
            if target:
                return "takeoff_to_point", self._active_takeoff_to_point, {
                    "latitude": target.get("target_latitude"),
                    "longitude": target.get("target_longitude"),
                    "height": target.get("target_height"),
                }

        return None, None, None

    def _point_flight_emergency_hold(self) -> tuple:
        """
        指点飞行未到点时，急停转为当前位置平飞盘旋。

        Returns:
            (handled, ok, message, output)
        """
        if self._point_flight_controller and hasattr(self._point_flight_controller, "point_flight_emergency_hold"):
            return self._point_flight_controller.point_flight_emergency_hold()

        flight_kind, active_flight, target_point = self._active_point_flight_target()
        if not active_flight or not target_point:
            return False, False, "", {}

        telemetry, telemetry_error = self._get_current_point_flight_telemetry()
        if telemetry is None:
            return False, False, telemetry_error, {}

        try:
            target_lat = float(target_point.get("latitude"))
            target_lon = float(target_point.get("longitude"))
        except (TypeError, ValueError):
            return False, False, "active point flight target invalid", {}

        current_lat = float(telemetry["latitude"])
        current_lon = float(telemetry["longitude"])
        distance = self._distance_meters(current_lat, current_lon, target_lat, target_lon)
        arrival_radius = float(Config.get('device.drc.point_flight_arrival_radius', 5.0) or 5.0)
        if distance <= arrival_radius:
            return False, False, f"point flight target reached: distance={distance:.1f}m", {}

        loiter_height = (
            telemetry.get("altitudeRelative")
            or telemetry.get("height")
            or target_point.get("height")
            or telemetry.get("altitude")
            or 0
        )
        try:
            loiter_height = float(loiter_height)
        except (TypeError, ValueError):
            loiter_height = float(target_point.get("height") or 0)

        result = self._send_guided_loiter_target(current_lat, current_lon, loiter_height)
        ok = bool(result.get("success"))
        message = result.get("message", "current position loiter" if ok else "current position loiter failed")
        output = {
            "status": "ok" if ok else "failed",
            "action": "hold_current_position",
            "flight_kind": flight_kind,
            "distance_to_target": distance,
            "arrival_radius": arrival_radius,
            "loiter_point": {
                "latitude": current_lat,
                "longitude": current_lon,
                "height": loiter_height,
            },
            "message": message,
        }
        return True, ok, message, output

    def _set_emergency_land_mode(self) -> tuple:
        """进入紧急降落模式，优先复用 FlightControl.land。"""
        if self._flight_control and hasattr(self._flight_control, "land"):
            try:
                result = self._flight_control.land()
                if result.get("success"):
                    return True, False, result.get("message", "land")
                return False, False, result.get("message", "land failed")
            except Exception as exc:
                logger.warning("DRC emergency land failed: %s", exc)
                return False, False, str(exc)

        if self._mode_control and hasattr(self._mode_control, "set_mode"):
            last_message = ""
            for mode in ("QLAND", "LAND"):
                try:
                    result = self._mode_control.set_mode(mode)
                except Exception as exc:
                    last_message = str(exc)
                    continue
                if result.get("success"):
                    return True, False, mode
                last_message = result.get("message", f"{mode} failed")
            return False, False, last_message

        return False, False, "mode control not available"

    @staticmethod
    def _distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Haversine distance in meters."""
        radius = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lam = math.radians(lon2 - lon1)
        a = (
            math.sin(d_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
        )
        return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _check_target_boundary(self, latitude: float, longitude: float, altitude: float) -> tuple:
        """检查指点飞行目标的高度和电子围栏半径。"""
        valid, reason = self._check_altitude_boundary(altitude)
        if not valid:
            return False, reason

        if not Config.get('device.safety.geofence_enabled', True):
            return True, ""

        if not self._telemetry_module or not hasattr(self._telemetry_module, "get_telemetry_data"):
            return True, ""

        try:
            telemetry = self._telemetry_module.get_telemetry_data()
        except Exception as exc:
            logger.warning("DRC target boundary telemetry unavailable: %s", exc)
            return True, ""

        current_lat = telemetry.get("latitude")
        current_lon = telemetry.get("longitude")
        if not current_lat or not current_lon:
            return True, ""

        radius = Config.get('device.limits.max_distance', self._geofence_radius)
        distance = self._distance_meters(float(current_lat), float(current_lon), latitude, longitude)
        if distance > radius:
            return False, f"目标点距离 {distance:.1f}m 超过电子围栏半径 {radius}m"
        return True, ""

    # ---- DRC 事件上报 ----

    def report_drc_status_notify(self, drc_state: int, result: int = 0):
        """
        上报 DRC 链路状态 (drc_status_notify)

        Args:
            drc_state: 0=未连接, 1=连接中, 2=已连接
            result: 结果码
        """
        if self._event_callback:
            event = MessageBuilder.build_event(
                'drc_status_notify',
                {'result': result, 'drc_state': drc_state},
                self.gateway_sn
            )
            self._event_callback(event)
            logger.info("DRC status reported: %s", format_with_labels({'drc_state': drc_state, 'result': result}))

    def report_flyto_progress(self, fly_to_id: str, status: str, result: int = 0,
                              way_point_index: int = 0, remaining_distance: float = 0,
                              remaining_time: float = 0, planned_path_points: list = None):
        """
        上报 flyto 执行结果 (fly_to_point_progress)

        Args:
            fly_to_id: 飞向目标点 ID
            status: wayline_progress/wayline_ok/wayline_failed/wayline_cancel
            result: 结果码
            way_point_index: 当前航点索引
            remaining_distance: 剩余距离（米）
            remaining_time: 剩余时间（秒）
            planned_path_points: 规划轨迹点列表 [{latitude, longitude, height}, ...]
        """
        if self._event_callback:
            data = {
                'fly_to_id': fly_to_id,
                'status': status,
                'result': result,
                'way_point_index': way_point_index,
                'remaining_distance': remaining_distance,
                'remaining_time': remaining_time
            }
            if planned_path_points:
                data['planned_path_points'] = planned_path_points
            event = MessageBuilder.build_event(
                'fly_to_point_progress', data, self.gateway_sn
            )
            self._event_callback(event)

    def report_takeoff_to_point_progress(self, flight_id: str, status: str,
                                         result: int = 0, track_id: str = "",
                                         way_point_index: int = 0,
                                         remaining_distance: float = 0,
                                         remaining_time: float = 0,
                                         planned_path_points: list = None):
        """
        上报一键起飞结果 (takeoff_to_point_progress)

        Args:
            flight_id: 任务 UUID
            status: task_ready/wayline_progress/wayline_ok/wayline_failed/wayline_cancel/task_finish
            result: 结果码
            track_id: 航迹 ID
            way_point_index: 当前航点索引
            remaining_distance: 剩余距离（米）
            remaining_time: 剩余时间（秒）
            planned_path_points: 规划轨迹点列表
        """
        if self._event_callback:
            data = {
                'flight_id': flight_id,
                'status': status,
                'result': result,
                'track_id': track_id,
                'way_point_index': way_point_index,
                'remaining_distance': remaining_distance,
                'remaining_time': remaining_time,
                'output': {
                    'status': status,
                    'progress': {
                        'way_point_index': way_point_index,
                        'remaining_distance': remaining_distance,
                        'remaining_time': remaining_time
                    }
                }
            }
            if planned_path_points:
                data['planned_path_points'] = planned_path_points
                data['output']['ext'] = {'planned_path_points': planned_path_points}
            event = MessageBuilder.build_event(
                'takeoff_to_point_progress', data, self.gateway_sn
            )
            self._event_callback(event)

    # ---- DRC模式管理 ----

    def _check_flight_status(self) -> tuple:
        """
        检查飞行状态

        Returns:
            tuple: (can_enter_drc: bool, reason: str)
        """
        if not self._telemetry_module:
            return True, ""

        # 获取飞行状态
        if hasattr(self._telemetry_module, 'get_flight_status'):
            status = self._telemetry_module.get_flight_status()
            is_flying = status.get('is_flying', False)
            current_altitude = status.get('altitude', 0)

            # 检查是否在飞行中
            if not is_flying:
                return False, "飞行器未起飞，无法进入DRC模式"

            # 检查高度是否在安全范围内
            if current_altitude < self._altitude_min:
                return False, f"当前高度 {current_altitude}m 过低，无法进入DRC模式"

        return True, ""

    def _pause_current_mission(self) -> bool:
        """
        暂停当前航线任务

        Returns:
            bool: 是否成功暂停（或无需暂停）
        """
        if not self._mission_manager:
            return True

        # 检查是否有正在执行的任务
        if hasattr(self._mission_manager, 'status'):
            mission_status = self._mission_manager.status
            from src.mission.manager import MissionManager
            if mission_status in (MissionManager.MISSION_RUNNING, MissionManager.MISSION_PREPARING):
                logger.info("DRC: 暂停当前航线任务")
                # 创建暂停消息并调用（包含当前任务ID）
                if hasattr(self._mission_manager, 'handle_pause_wayline'):
                    task_id = getattr(self._mission_manager, '_current_task_id', '')
                    reply = self._mission_manager.handle_pause_wayline({'data': {'task_id': task_id}})
                    result = reply.get("data", {}).get("result", 0) if isinstance(reply, dict) else 0
                    return result == 0
                return False

        return True

    def _enter_drc_hold_mode(self) -> tuple:
        """进入 DRC 后的固定翼保持态：任务暂停用 LOITER，无任务也切 LOITER 盘旋。"""
        if self._mission_manager and hasattr(self._mission_manager, 'status'):
            from src.mission.manager import MissionManager
            mission_status = self._mission_manager.status
            if mission_status in (MissionManager.MISSION_RUNNING, MissionManager.MISSION_PREPARING):
                if self._pause_current_mission():
                    return True, "mission paused to LOITER"
                return False, "pause mission failed"

        sent, _, detail = self._set_safe_mode()
        if sent:
            return True, detail
        return False, detail

    def _start_heartbeat_monitor(self):
        """启动心跳监控线程"""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        self._heartbeat_running = True
        self._last_cloud_heartbeat = time.time()
        self._last_device_heartbeat = time.time()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_monitor_loop, daemon=True
        )
        self._heartbeat_thread.start()
        logger.info("DRC heartbeat monitor started")

    def _heartbeat_monitor_loop(self):
        """心跳监控循环"""
        while self._heartbeat_running:
            try:
                current_time = time.time()

                if self._check_heartbeat_timeout_once(current_time):
                    break

                # 定期发送设备心跳（每10秒）
                if current_time - self._last_device_heartbeat > 10:
                    self._send_device_heartbeat()
                    self._last_device_heartbeat = current_time

                time.sleep(1)
            except Exception as e:
                logger.error(f"Heartbeat monitor error: {e}")

    def _check_heartbeat_timeout_once(self, current_time: float = None) -> bool:
        """Check DRC cloud heartbeat timeout once. Returns True when auto-exited."""
        if not self._heartbeat_running or not self._drc_enabled:
            return False
        current_time = current_time or time.time()
        elapsed = current_time - self._last_cloud_heartbeat
        if elapsed <= self._heartbeat_timeout:
            return False
        logger.warning("DRC heartbeat timeout! Last cloud heartbeat: %.1fs ago", elapsed)
        self._auto_exit_drc("heartbeat_timeout")
        return True

    def _send_device_heartbeat(self):
        """发送设备DRC心跳"""
        if self._event_callback:
            event = MessageBuilder.build_event(
                'drc_heartbeat',
                {
                    'session_id': self._session_id,
                    'timestamp': int(time.time() * 1000)
                },
                self.gateway_sn
            )
            self._event_callback(event)

    def _auto_exit_drc(self, reason: str):
        """自动退出DRC模式"""
        logger.warning(f"DRC auto exit: {reason}")

        brake_sent, brake_simulated, brake_error = self._send_zero_velocity()
        mode_ok, mode_simulated, mode_message = self._set_safe_mode()
        logger.info(
            "DRC auto exit safety action: brake_sent=%s, brake_simulated=%s, brake_error=%s, "
            "mode_ok=%s, mode_simulated=%s, mode_message=%s",
            brake_sent,
            brake_simulated,
            brake_error,
            mode_ok,
            mode_simulated,
            mode_message,
        )

        # 停止心跳监控
        self._heartbeat_running = False

        # 恢复原始模式
        if self._original_mode and self._mode_control:
            self._mode_control.set_mode(self._original_mode)
            logger.info(f"DRC restored original mode: {self._original_mode}")

        # 更新状态
        self._drc_enabled = False
        self._session_id = ""
        self._original_mode = None
        self._active_fly_to_point = None
        self._active_takeoff_to_point = None
        if self._point_flight_controller and hasattr(self._point_flight_controller, "clear_active"):
            self._point_flight_controller.clear_active()
        self._flight_authority_grabbed = False
        self._last_cloud_heartbeat_seq = None

        # 上报退出事件
        result = DEVICE_COMMAND_TIMEOUT if reason == "heartbeat_timeout" else -1
        self.report_drc_status_notify(drc_state=0, result=result)
        self._write_audit_log("auto_exit", reason)

    def _write_audit_log(self, action: str, details: str):
        """写入操作审计日志"""
        logger.info(f"DRC Audit: [{action}] {details}")

    def handle_drc_mode_enter(self, msg: dict) -> dict:
        """
        进入DRC模式

        流程:
        1. 状态校验：检查飞行状态
        2. 保存原模式：记录进入前的飞行模式
        3. 固定翼保持：如正在执行航线任务，暂停到 LOITER；无任务时切 LOITER 盘旋
        4. 启动心跳：建立心跳保活机制
        5. 上报事件：drc_status_notify

        Request: { }
        """
        data = msg.get('data', {})
        valid, errors = validate_drc_mode_enter(data)
        if not valid:
            reason = "; ".join(errors)
            logger.warning("DRC enter payload invalid: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        if not self._has_real_flight_adapter():
            return self._real_adapter_unavailable(
                msg,
                "drc_mode_enter requires a real flight control adapter",
            )

        if self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC already enabled")

        # 1. 状态校验
        can_enter, reason = self._check_flight_status()
        if not can_enter:
            logger.warning(f"DRC enter blocked: {reason}")
            return MessageBuilder.build_reply(msg, result=-1, msg_str=reason)

        # 2. 保存原模式
        if self._mode_control and hasattr(self._mode_control, "get_current_mode"):
            mode_info = self._mode_control.get_current_mode()
            self._original_mode = mode_info.get('mode_name')
            logger.info(f"DRC: 保存原模式 {self._original_mode}")

        # 3. 固定翼进入可持续保持态；指点飞行时再切 GUIDED。
        hold_ok, hold_reason = self._enter_drc_hold_mode()
        if not hold_ok:
            logger.warning("DRC enter hold mode failed: %s", hold_reason)
            return self._real_adapter_unavailable(msg, hold_reason)

        # 4. 进入DRC状态
        self._drc_enabled = True
        self._flight_authority_grabbed = True
        self._session_id = str(int(time.time() * 1000))
        self._last_cloud_heartbeat_seq = None
        logger.info(f"DRC mode entered, session={self._session_id}")

        # 5. 启动心跳监控
        self._start_heartbeat_monitor()

        # 6. 上报进入成功事件
        self.report_drc_status_notify(drc_state=2, result=0)

        # 7. 写入审计日志
        self._write_audit_log("mode_enter", f"session={self._session_id}, hold={hold_reason}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_drc_mode_exit(self, msg: dict) -> dict:
        """
        退出DRC模式

        流程:
        1. 停止心跳监控
        2. 恢复原飞行模式（保存的模式）
        3. 可选恢复任务
        4. 上报退出状态事件
        5. 写入操作审计日志

        Request: { restore_mission: bool }  # 可选，是否恢复暂停的任务
        """
        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        data = msg.get('data', {})
        restore_mission = data.get('restore_mission', False)

        # 1. 停止心跳监控
        self._heartbeat_running = False
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2.0)
            self._heartbeat_thread = None

        # 2. 可选恢复任务（先恢复任务，任务会自动设置正确的模式）
        mission_resumed = False
        if restore_mission and self._mission_manager:
            if hasattr(self._mission_manager, 'handle_resume_wayline'):
                mission_resumed = True
                self._mission_manager.handle_resume_wayline({})
                logger.info("DRC: 已恢复暂停的航线任务")

        # 3. 恢复原飞行模式（仅当未恢复任务时）
        if not mission_resumed and self._original_mode and self._mode_control:
            result = self._mode_control.set_mode(self._original_mode)
            if result.get("success"):
                logger.info(f"DRC退出恢复原模式: {self._original_mode}")
            else:
                logger.warning(f"DRC退出恢复模式失败: {result.get('message')}")
                self._mode_control.set_mode("LOITER")

        # 4. 更新状态
        self._drc_enabled = False
        self._session_id = ""
        self._original_mode = None
        self._active_fly_to_point = None
        self._active_takeoff_to_point = None
        if self._point_flight_controller and hasattr(self._point_flight_controller, "clear_active"):
            self._point_flight_controller.clear_active()
        self._flight_authority_grabbed = False
        self._last_cloud_heartbeat_seq = None
        logger.info("DRC mode exited")

        # 5. 上报退出状态事件
        self.report_drc_status_notify(drc_state=0, result=0)

        # 6. 写入审计日志
        self._write_audit_log("mode_exit", f"restore_mission={restore_mission}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_drc_heart_beat(self, msg: dict) -> dict:
        """Handle cloud-origin DRC heartbeat on the drc/down channel."""
        data = msg.get('data', {})
        valid, normalized, errors = validate_drc_heartbeat(data)
        if not valid:
            reason = "; ".join(errors)
            logger.warning("DRC heartbeat payload invalid: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=DEVICE_STATE_UNSUPPORTED, msg_str="DRC not enabled")

        heartbeat_valid, heartbeat_reason = self._validate_cloud_heartbeat_window(normalized)
        if not heartbeat_valid:
            logger.warning("DRC heartbeat rejected: %s", heartbeat_reason)
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str=heartbeat_reason,
            )

        self._last_cloud_heartbeat = time.time()
        if "seq" in normalized:
            self._last_cloud_heartbeat_seq = normalized["seq"]
        reply_data = {
            "drc_state": 2,
            "session_id": self._session_id,
            "timestamp": normalized["timestamp"],
        }
        if "seq" in normalized:
            reply_data["seq"] = normalized["seq"]
        logger.debug("DRC cloud heartbeat received: %s", format_with_labels(reply_data))
        return MessageBuilder.build_reply(msg, result=0, data=reply_data)

    def handle_drc_heartbeat(self, msg: dict) -> dict:
        """Compatibility alias for older local tests."""
        return self.handle_drc_heart_beat(msg)

    def _validate_cloud_heartbeat_window(self, normalized: dict) -> tuple[bool, str]:
        """校验云端 DRC 心跳时间窗和 seq 单调递增。"""
        window_seconds = int(Config.get("security.drc.heartbeat_timestamp_window_seconds", 30))
        if window_seconds > 0:
            now_ms = int(time.time() * 1000)
            timestamp = int(normalized.get("timestamp", 0))
            if abs(now_ms - timestamp) > window_seconds * 1000:
                return False, "DRC heartbeat timestamp 超出允许时间窗"

        require_seq_increasing = bool(
            Config.get("security.drc.require_heartbeat_seq_increasing", True)
        )
        if require_seq_increasing and "seq" in normalized:
            seq = int(normalized["seq"])
            if self._last_cloud_heartbeat_seq is not None and seq <= self._last_cloud_heartbeat_seq:
                return False, "DRC heartbeat seq 必须递增"
        return True, ""

    def handle_flight_authority_grab(self, msg: dict) -> dict:
        """获取飞行控制权，供 Dock3 联调脚本验证控制链路。"""
        if not self._has_real_flight_adapter():
            return self._real_adapter_unavailable(
                msg,
                "flight_authority_grab requires a real flight control adapter",
            )
        self._flight_authority_grabbed = True
        logger.info("Flight authority grabbed")
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={
                "output": {"status": "ok"},
                "authority": "flight",
                "flight_authority": 1,
            },
        )

    def _ensure_drc_authority_for_point_flight(self, msg: dict) -> tuple[bool, str]:
        """指点飞行前自动进入/保持 DRC 控制权。"""
        if self._drc_enabled and self._flight_authority_grabbed:
            return True, ""
        if self._drc_enabled and not self._flight_authority_grabbed:
            authority_msg = {
                "method": "flight_authority_grab",
                "tid": msg.get("tid", ""),
                "gateway": msg.get("gateway", self.gateway_sn),
                "data": {},
            }
            reply = self.handle_flight_authority_grab(authority_msg)
            result = reply.get("data", {}).get("result") if isinstance(reply, dict) else None
            if result == 0 and self._flight_authority_grabbed:
                return True, ""
            reason = ""
            if isinstance(reply, dict):
                reason = reply.get("msg") or reply.get("data", {}).get("output", {}).get("reason", "")
            return False, reason or "DRC flight authority unavailable"

        enter_msg = {
            "method": "drc_mode_enter",
            "tid": msg.get("tid", ""),
            "gateway": msg.get("gateway", self.gateway_sn),
            "data": {},
        }
        reply = self.handle_drc_mode_enter(enter_msg)
        result = reply.get("data", {}).get("result") if isinstance(reply, dict) else None
        if result == 0 and self._drc_enabled:
            self._flight_authority_grabbed = True
            return True, ""
        reason = ""
        if isinstance(reply, dict):
            reason = reply.get("msg") or reply.get("data", {}).get("output", {}).get("reason", "")
        return False, reason or "DRC authority unavailable"

    # ---- DRC实时控制 ----

    def _check_rate_limit(self) -> bool:
        """
        检查指令频率限制

        Returns:
            bool: 是否允许执行（True=允许，False=限流）
        """
        current_time = time.time()

        # 清理超过1秒的时间戳
        self._speed_cmd_timestamps = [
            ts for ts in self._speed_cmd_timestamps
            if current_time - ts < 1.0
        ]

        # 检查是否超过频率限制
        if len(self._speed_cmd_timestamps) >= self._speed_cmd_rate_limit:
            return False

        # 记录本次指令时间戳
        self._speed_cmd_timestamps.append(current_time)
        return True

    def _check_speed_boundary(self, vx: float, vy: float, vz: float) -> tuple:
        """
        检查速度边界

        Args:
            vx, vy, vz: 速度值（m/s）

        Returns:
            tuple: (valid: bool, reason: str)
        """
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
        if speed > self._speed_max:
            # 限制速度
            scale = self._speed_max / speed
            return False, f"速度 {speed:.1f}m/s 超过限制 {self._speed_max}m/s"

        return True, ""

    def _check_altitude_boundary(self, altitude: float) -> tuple:
        """
        检查高度边界

        Args:
            altitude: 目标高度（米）

        Returns:
            tuple: (valid: bool, reason: str)
        """
        if altitude < self._altitude_min:
            return False, f"高度 {altitude}m 低于最小高度 {self._altitude_min}m"
        if altitude > self._altitude_max:
            return False, f"高度 {altitude}m 超过最大高度 {self._altitude_max}m"

        return True, ""

    def handle_drc_speed_push(self, msg: dict) -> dict:
        """
        DRC速度推送

        Request: {
            vx: float,  # 前向速度 m/s
            vy: float,  # 右向速度 m/s
            vz: float,  # 下向速度 m/s
            yaw_rate: float  # 偏航角速度 rad/s
        }
        """
        data = msg.get('data', {})
        vx = data.get('vx', 0)
        vy = data.get('vy', 0)
        vz = data.get('vz', 0)
        yaw_rate = data.get('yaw_rate', 0)

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        # 1. 指令限流
        if not self._check_rate_limit():
            logger.debug("DRC speed push rate limited")
            return MessageBuilder.build_reply(msg, result=-2, msg_str="rate limited")

        # 2. 边界校验 - 速度限制
        valid, reason = self._check_speed_boundary(vx, vy, vz)
        if not valid:
            logger.warning(f"DRC speed boundary: {reason}")
            # 限制速度到最大允许值
            speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            if speed > 0:
                scale = self._speed_max / speed
                vx *= scale
                vy *= scale
                vz *= scale

        # 3. 更新缓存
        self._last_speed_cmd = {
            'vx': vx, 'vy': vy, 'vz': vz, 'yaw_rate': yaw_rate,
            'timestamp': time.time()
        }

        # 4. 通过MAVLink发送速度指令 (SET_POSITION_TARGET_LOCAL_NED)
        sent, simulated, error = self._send_body_velocity(vx, vy, vz, yaw_rate)
        if error and not simulated:
            return MessageBuilder.build_reply(
                msg,
                result=DOCK_COMMUNICATION_ERROR,
                msg_str=f"DRC speed send failed: {error}",
            )

        logger.debug(
            "DRC speed handled: vx=%s, vy=%s, vz=%s, yaw_rate=%s, sent=%s, simulated=%s",
            vx,
            vy,
            vz,
            yaw_rate,
            sent,
            simulated,
        )
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={"output": {"status": "ok", "simulated": simulated}},
        )

    def handle_drc_heading_push(self, msg: dict) -> dict:
        """
        DRC航向推送

        Request: { yaw: float }  # 目标航向角(度)
        """
        data = msg.get('data', {})
        yaw_deg = data.get('yaw', 0)
        self._last_heading_cmd = {'yaw': yaw_deg}

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        logger.info(f"DRC heading push: yaw={yaw_deg}")

        # 发送航向控制指令 (MAV_CMD_CONDITION_YAW)
        if self._flight_control and hasattr(self._flight_control, '_send_command_long'):
            self._flight_control._send_command_long(
                mavutil.mavlink.MAV_CMD_CONDITION_YAW,
                yaw_deg,  # param1: 目标角度(度)
                0,        # param2: 角速度(deg/s), 0=默认
                1,        # param3: 方向, 1=顺时针
                0,        # param4: 0=绝对角度, 1=相对角度
                0, 0, 0
            )

        return MessageBuilder.build_reply(msg, result=0)

    def handle_drc_altitude_push(self, msg: dict) -> dict:
        """
        DRC高度推送

        Request: { altitude: float }  # 目标高度(m)
        """
        data = msg.get('data', {})
        altitude = data.get('altitude', 0)

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        # 高度边界校验
        valid, reason = self._check_altitude_boundary(altitude)
        if not valid:
            logger.warning(f"DRC altitude boundary: {reason}")
            return MessageBuilder.build_reply(msg, result=-3, msg_str=reason)

        # 更新缓存
        self._last_altitude_cmd = {'altitude': altitude, 'timestamp': time.time()}

        logger.info(f"DRC altitude push: alt={altitude}")

        # 发送高度控制指令 (SET_POSITION_TARGET_GLOBAL_INT, 仅控制Z轴)
        conn = self._get_mavlink_connection()
        if conn and hasattr(conn, 'master') and conn.master:
            try:
                conn.master.mav.set_position_target_global_int_send(
                    0,
                    conn.master.target_system,
                    conn.master.target_component,
                    mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                    0b0000111111111011,  # 仅使用Z位置
                    0, 0,  # lat, lon (忽略)
                    altitude,  # alt
                    0, 0, 0,  # vx, vy, vz (忽略)
                    0, 0, 0,  # afx, afy, afz (忽略)
                    0, 0     # yaw, yaw_rate (忽略)
                )
            except Exception as e:
                logger.error(f"DRC altitude send failed: {e}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_drc_flyto_point_push(self, msg: dict) -> dict:
        """
        DRC指点飞行推送

        Request: {
            latitude: float,
            longitude: float,
            altitude: float,
            speed: float
        }
        """
        data = msg.get('data', {})
        lat = data.get('latitude', 0)
        lon = data.get('longitude', 0)
        alt = data.get('altitude', 0)
        speed = data.get('speed', 5)
        self._last_flyto_cmd = {
            'lat': lat, 'lon': lon, 'alt': alt, 'speed': speed
        }

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        valid, reason = self._check_target_boundary(lat, lon, alt)
        if not valid:
            logger.warning("DRC flyto boundary rejected: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        logger.info(f"DRC flyto: lat={lat}, lon={lon}, alt={alt}, speed={speed}")

        result = self._send_guided_loiter_target(lat, lon, alt)
        if not result.get("success"):
            logger.warning(f"DRC flyto failed: {result.get('message')}")
            return self._real_adapter_unavailable(msg, result.get("message", "guided target failed"))

        return MessageBuilder.build_reply(msg, result=0)

    # ---- 大疆标准DRC接口 ----

    def handle_drone_control(self, msg: dict) -> dict:
        """
        无人机DRC控制 (drone_control)

        Request: {
            horizontal_speed: float,
            horizontal_direction: float,
            vertical_speed: float,
            vertical_direction: float,
            yaw: float
        }
        Response: { result }
        """
        data = msg.get('data', {})

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        valid, normalized, errors = normalize_drone_control(data)
        if not valid:
            reason = "; ".join(errors)
            logger.warning("Drone control payload invalid: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        vx = normalized["vx"]
        vy = normalized["vy"]
        vz = normalized["vz"]
        yaw_rate = normalized["yaw_rate"]
        logger.info(
            "Drone control normalized: seq=%s, vx=%.2f, vy=%.2f, vz=%.2f, yaw_rate=%.3f",
            normalized.get("seq"),
            vx,
            vy,
            vz,
            yaw_rate,
        )

        sent, simulated, error = self._send_body_velocity(vx, vy, vz, yaw_rate)
        if error and not simulated:
            return MessageBuilder.build_reply(
                msg,
                result=DOCK_COMMUNICATION_ERROR,
                msg_str=f"Drone control send failed: {error}",
            )

        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={"output": {"status": "ok", "simulated": simulated}},
        )

    def handle_stick_control(self, msg: dict) -> dict:
        """
        DRC摇杆控制 (stick_control)

        当前先作为 Dock3 DRC 控制数据的模拟兼容入口：
        - 优先接受 vx/vy/vz/yaw_rate，直接转为速度控制
        - 兼容 right_y/right_x/left_y/left_x 或 pitch/roll/throttle/yaw

        后续按 Dock3 官方字段精校后，应在此处收敛字段名和值域。
        """
        data = msg.get('data', {})

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        valid, normalized, errors = normalize_stick_control(data)
        if not valid:
            reason = "; ".join(errors)
            logger.warning("Stick control payload invalid: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        speed_msg = dict(msg)
        speed_msg['data'] = normalized
        logger.info(
            "Stick control mapped to speed: vx=%s, vy=%s, vz=%s, yaw_rate=%s",
            speed_msg['data']['vx'],
            speed_msg['data']['vy'],
            speed_msg['data']['vz'],
            speed_msg['data']['yaw_rate'],
        )
        return self.handle_drc_speed_push(speed_msg)

    def handle_payload_control(self, msg: dict) -> dict:
        """
        负载DRC控制 (payload_control)

        Request: {
            payload_index: str,
            action: 'photo_take'|'record_start'|'record_stop'|'zoom'|'gimbal_rotate',
            params: { ... }
        }
        Response: { result }
        """
        data = msg.get('data', {})
        payload_index = str(data.get('payload_index', '52-0-0') or '52-0-0')
        action = data.get('action', '')
        params = data.get('params', {})
        if not isinstance(params, dict):
            params = {}
        camera_index = self._camera_index_from_payload_index(payload_index)

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        logger.info(f"Payload control: index={payload_index}, action={action}, params={params}")

        command_result = {"success": True, "message": ""}
        # 根据 action 分发到对应模块
        try:
            if action == 'photo_take':
                # 拍照 - 调用相机控制器
                if self._camera_controller:
                    self._camera_controller.handle_camera_photo_take({'data': {'camera_index': camera_index}})

            elif action == 'record_start':
                # 开始录像
                if self._camera_controller:
                    self._camera_controller.handle_camera_recording_start({'data': {'camera_index': camera_index}})

            elif action == 'record_stop':
                # 停止录像
                if self._camera_controller:
                    self._camera_controller.handle_camera_recording_stop({'data': {'camera_index': camera_index}})

            elif action == 'zoom':
                # 变焦
                zoom_level = params.get('level', 1)
                if self._gimbal_control:
                    command_result = self._gimbal_control.zoom_to(zoom_level)
                else:
                    command_result = {"success": False, "message": "云台控制未配置"}

            elif action == 'gimbal_rotate':
                # 云台旋转
                if self._gimbal_control:
                    pitch_speed = self._float_or_none(self._first_present(params, "pitch_speed", "pitchSpeed"))
                    yaw_speed = self._float_or_none(self._first_present(params, "yaw_speed", "yawSpeed"))
                    pitch = self._float_or_none(self._first_present(params, "pitch", "gimbal_pitch", "gimbalPitch"))
                    yaw = self._float_or_none(self._first_present(params, "yaw", "gimbal_yaw", "gimbalYaw"))
                    if pitch_speed is not None or yaw_speed is not None:
                        yaw_spd = int(float(yaw_speed or 0.0) * 100)
                        pitch_spd = int(float(pitch_speed or 0.0) * 100)
                        if yaw_spd == 0 and pitch_spd == 0 and hasattr(self._gimbal_control, "stop"):
                            command_result = self._gimbal_control.stop()
                        else:
                            command_result = self._gimbal_control.move(yaw_spd, pitch_spd)
                    else:
                        command_result = self._gimbal_control.turn_to(yaw or 0.0, pitch or 0.0)
                    logger.info(f"Payload gimbal rotate: params={params}, result={command_result}")
                else:
                    command_result = {"success": False, "message": "云台控制未配置"}

        except Exception as e:
            logger.error(f"Payload control dispatch error: {e}")
            return MessageBuilder.build_reply(msg, result=-1, msg_str=str(e))

        if not command_result.get("success", True):
            return MessageBuilder.build_reply(
                msg,
                result=-1,
                msg_str=str(command_result.get("message", "payload_control failed")),
                data={"output": {"status": "failed", "detail": command_result}},
            )

        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={"output": {"status": "ok", "detail": command_result}},
        )

    @staticmethod
    def _camera_index_from_payload_index(payload_index: str) -> int:
        """Map Dock3 type-subtype-gimbalIndex to the local camera slot."""
        try:
            return int(str(payload_index).split("-")[-1])
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _first_present(data: dict, *names: str):
        if not isinstance(data, dict):
            return None
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

    def handle_drc_gimbal_speed_push(self, msg: dict) -> dict:
        """
        DRC云台速度推送 (drc_gimbal_speed_push)

        Request: {
            pitch_speed: float,
            yaw_speed: float,
            roll_speed: float
        }
        Response: { result }
        """
        data = msg.get('data', {})
        pitch = data.get('pitch_speed', 0)
        yaw = data.get('yaw_speed', 0)
        roll = data.get('roll_speed', 0)

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        valid, errors = validate_gimbal_speed_push(data)
        if not valid:
            reason = "; ".join(errors)
            logger.warning("DRC gimbal speed payload invalid: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        logger.info(f"DRC gimbal speed: pitch={pitch}, yaw={yaw}, roll={roll}")

        # 分发到云台控制模块
        if self._gimbal_control:
            try:
                # 将速度转换为整数（SDK使用 0.01°/s 为单位）
                yaw_spd = int(yaw * 100)
                pitch_spd = int(pitch * 100)
                self._gimbal_control.move(yaw_spd, pitch_spd)
            except Exception as e:
                logger.error(f"Gimbal speed dispatch error: {e}")

        return MessageBuilder.build_reply(msg, result=0)

    # ---- 指点飞行接口 ----

    def handle_fly_to_point_stop(self, msg: dict) -> dict:
        """
        停止指点飞行 (fly_to_point_stop)

        Request: { }
        """
        logger.info("Fly to point stop")
        stopped = False
        fly_to_id = ""
        if self._active_fly_to_point:
            fly_to_id = self._active_fly_to_point.get("fly_to_id", "")
            self.report_flyto_progress(fly_to_id, "wayline_cancel", result=0)
            self._active_fly_to_point = None
            stopped = True
        if self._active_takeoff_to_point:
            flight_id = self._active_takeoff_to_point.get("flight_id", "")
            track_id = self._active_takeoff_to_point.get("track_id", "")
            self.report_takeoff_to_point_progress(flight_id, "wayline_cancel", result=0, track_id=track_id)
            self._active_takeoff_to_point = None
            stopped = True
            fly_to_id = fly_to_id or flight_id
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={
                "fly_to_id": fly_to_id,
                "output": {
                    "status": "ok",
                    "stopped": stopped,
                },
            },
        )

    def handle_fly_to_point_update(self, msg: dict) -> dict:
        """
        更新指点飞行目标点 (fly_to_point_update)

        Request: {
            max_speed: int,
            points: [ { latitude, longitude, height } ]
        }
        """
        data = msg.get('data', {})
        default_fly_to_id = msg.get('tid') or str(int(time.time() * 1000))
        valid, target, errors = normalize_fly_to_point_update(data, default_fly_to_id=default_fly_to_id)
        if not valid:
            reason = "; ".join(errors)
            logger.warning("fly_to_point_update payload invalid: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        max_speed = target["max_speed"]
        points = target["points"]
        fly_to_id = target["fly_to_id"]
        first_point = points[0]
        valid, reason = self._check_target_boundary(
            first_point["latitude"],
            first_point["longitude"],
            first_point["height"],
        )
        if not valid:
            logger.warning("fly_to_point_update boundary rejected: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        authority_ok, authority_reason = self._ensure_drc_authority_for_point_flight(msg)
        if not authority_ok:
            logger.warning("fly_to_point_update rejected before authority: %s", authority_reason)
            return MessageBuilder.build_reply(
                msg,
                result=DOCK_COMMUNICATION_ERROR,
                data={"output": {"status": "failed", "reason": authority_reason}},
                msg_str=authority_reason,
            )

        self._active_fly_to_point = {
            "fly_to_id": fly_to_id,
            "max_speed": max_speed,
            "points": points,
            "timestamp": time.time(),
        }
        logger.info("Fly to point update: id=%s, max_speed=%s, points=%s", fly_to_id, max_speed, points)

        if not self._mode_control or not (
            hasattr(self._mode_control, 'guided_loiter_at_point') or hasattr(self._mode_control, 'guided_goto')
        ):
            self.report_flyto_progress(fly_to_id, "wayline_failed", result=DOCK_COMMUNICATION_ERROR)
            self._active_fly_to_point = None
            return self._real_adapter_unavailable(
                msg,
                "fly_to_point_update requires a real guided flight adapter",
            )

        try:
            result = self._send_guided_loiter_target(
                first_point["latitude"],
                first_point["longitude"],
                first_point["height"],
            )
        except Exception as exc:
            self.report_flyto_progress(fly_to_id, "wayline_failed", result=DOCK_COMMUNICATION_ERROR)
            self._active_fly_to_point = None
            return self._real_adapter_unavailable(
                msg,
                f"fly_to_point_update guided dispatch failed: {exc}",
            )
        if not result.get("success"):
            warning = result.get("message", "guided_goto failed")
            logger.warning("fly_to_point_update guided_goto failed: %s", warning)
            self.report_flyto_progress(fly_to_id, "wayline_failed", result=DOCK_COMMUNICATION_ERROR)
            self._active_fly_to_point = None
            return self._real_adapter_unavailable(msg, warning)

        self.report_flyto_progress(
            fly_to_id,
            "wayline_progress",
            result=0,
            way_point_index=0,
            planned_path_points=points,
        )
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={
                "fly_to_id": fly_to_id,
                "max_speed": max_speed,
                "target_point": first_point,
                "output": {
                    "status": "ok",
                    "simulated": False,
                    "warning": "",
                },
            },
        )

    # ---- 相机控制接口 ----

    def handle_camera_screen_drag(self, msg: dict) -> dict:
        """
        相机屏幕拖动 (camera_screen_drag)

        Request: {
            payload_index: str,
            locked: bool,
            pitch_speed: double,
            yaw_speed: double
        }
        """
        data = msg.get('data', {})
        payload_index = data.get('payload_index', '')
        locked = data.get('locked', False)
        pitch_speed = data.get('pitch_speed', 0.0)
        yaw_speed = data.get('yaw_speed', 0.0)
        logger.info(f"Camera screen drag: index={payload_index}, locked={locked}, "
                   f"pitch={pitch_speed}, yaw={yaw_speed}")
        return self._real_adapter_unavailable(msg, "camera_screen_drag requires a real payload adapter")

    def handle_camera_aim(self, msg: dict) -> dict:
        """
        相机瞄准 (camera_aim)

        Request: {
            payload_index: str,
            camera_type: str,
            locked: bool,
            x: double,
            y: double
        }
        """
        data = msg.get('data', {})
        payload_index = data.get('payload_index', '')
        camera_type = data.get('camera_type', '')
        locked = data.get('locked', False)
        x = data.get('x', 0.5)
        y = data.get('y', 0.5)
        logger.info(f"Camera aim: index={payload_index}, type={camera_type}, locked={locked}, "
                   f"x={x}, y={y}")
        return self._real_adapter_unavailable(msg, "camera_aim requires a real payload adapter")

    def handle_camera_screen_split(self, msg: dict) -> dict:
        """
        相机分屏设置 (camera_screen_split)

        Request: {
            payload_index: str,
            enable: bool
        }
        """
        data = msg.get('data', {})
        payload_index = data.get('payload_index', '')
        enable = data.get('enable', False)
        logger.info(f"Camera screen split: index={payload_index}, enable={enable}")
        return self._real_adapter_unavailable(msg, "camera_screen_split requires a real payload adapter")

    def handle_video_storage_set(self, msg: dict) -> dict:
        """
        视频存储设置 (video_storage_set)

        Request: {
            payload_index: str,
            video_storage_settings: list
        }
        """
        data = msg.get('data', {})
        payload_index = data.get('payload_index', '')
        video_storage_settings = data.get('video_storage_settings', [])
        logger.info(f"Video storage set: index={payload_index}, settings={video_storage_settings}")
        return self._real_adapter_unavailable(msg, "video_storage_set requires a real payload storage adapter")

    def handle_camera_exposure_set(self, msg: dict) -> dict:
        """
        相机曝光设置 (camera_exposure_set)

        Request: {
            payload_index: str,
            camera_type: str,
            exposure_value: int
        }
        """
        data = msg.get('data', {})
        payload_index = data.get('payload_index', '')
        camera_type = data.get('camera_type', '')
        exposure_value = data.get('exposure_value', 16)
        logger.info(f"Camera exposure set: index={payload_index}, type={camera_type}, "
                   f"exposure={exposure_value}")
        return self._real_adapter_unavailable(msg, "camera_exposure_set requires a real camera adapter")

    # ---- 红外测温接口 ----

    def handle_ir_metering_point_set(self, msg: dict) -> dict:
        """
        红外测温点设置 (ir_metering_point_set)

        Request: {
            payload_index: str,
            x: double,
            y: double
        }
        """
        data = msg.get('data', {})
        payload_index = data.get('payload_index', '')
        x = data.get('x', 0.5)
        y = data.get('y', 0.5)
        logger.info(f"IR metering point set: index={payload_index}, x={x}, y={y}")
        return self._real_adapter_unavailable(msg, "ir_metering_point_set requires a real infrared payload adapter")

    def handle_ir_metering_area_set(self, msg: dict) -> dict:
        """
        红外测温区域设置 (ir_metering_area_set)

        Request: {
            payload_index: str,
            x: double,
            y: double,
            width: double,
            height: double
        }
        """
        data = msg.get('data', {})
        payload_index = data.get('payload_index', '')
        x = data.get('x', 0.5)
        y = data.get('y', 0.5)
        width = data.get('width', 0.5)
        height = data.get('height', 0.5)
        logger.info(f"IR metering area set: index={payload_index}, x={x}, y={y}, "
                   f"width={width}, height={height}")
        return self._real_adapter_unavailable(msg, "ir_metering_area_set requires a real infrared payload adapter")

    # ---- POI模式接口 ----

    def handle_poi_mode_enter(self, msg: dict) -> dict:
        """
        进入POI模式 (poi_mode_enter)

        Request: {
            latitude: double,
            longitude: double,
            height: float
        }
        """
        data = msg.get('data', {})
        latitude = data.get('latitude', 0)
        longitude = data.get('longitude', 0)
        height = data.get('height', 50)
        logger.info(f"POI mode enter: lat={latitude}, lon={longitude}, height={height}")
        return self._real_adapter_unavailable(msg, "poi_mode_enter requires a real guided flight adapter")

    def handle_poi_mode_exit(self, msg: dict) -> dict:
        """
        退出POI模式 (poi_mode_exit)

        Request: { }
        """
        logger.info("POI mode exit")
        return self._real_adapter_unavailable(msg, "poi_mode_exit requires a real guided flight adapter")

    def handle_poi_circle_speed_set(self, msg: dict) -> dict:
        """
        POI环绕速度设置 (poi_circle_speed_set)

        Request: {
            circle_speed: float
        }
        """
        data = msg.get('data', {})
        circle_speed = data.get('circle_speed', 0)
        logger.info(f"POI circle speed set: {circle_speed}")
        return self._real_adapter_unavailable(msg, "poi_circle_speed_set requires a real guided flight adapter")

    def handle_payload_authority_grab(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "payload_authority_grab requires a real payload adapter")

    def handle_fly_to_point(self, msg: dict) -> dict:
        return self.handle_fly_to_point_update(msg)

    def handle_camera_frame_zoom(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_frame_zoom requires a real camera adapter")

    def handle_camera_mode_switch(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_mode_switch requires a real camera adapter")

    def handle_camera_photo_take(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_photo_take requires a real camera adapter")

    def handle_camera_photo_stop(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_photo_stop requires a real camera adapter")

    def handle_camera_recording_start(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_recording_start requires a real camera adapter")

    def handle_camera_recording_stop(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_recording_stop requires a real camera adapter")

    def handle_camera_focal_length_set(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_focal_length_set requires a real camera adapter")

    def handle_gimbal_reset(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "gimbal_reset requires a real gimbal adapter")

    def handle_camera_look_at(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_look_at requires a real payload adapter")

    def handle_photo_storage_set(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "photo_storage_set requires a real payload storage adapter")

    def handle_camera_exposure_mode_set(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_exposure_mode_set requires a real camera adapter")

    def handle_camera_focus_mode_set(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_focus_mode_set requires a real camera adapter")

    def handle_camera_focus_value_set(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_focus_value_set requires a real camera adapter")

    def handle_camera_point_focus_action(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "camera_point_focus_action requires a real camera adapter")

    def handle_ir_metering_mode_set(self, msg: dict) -> dict:
        return self._real_adapter_unavailable(msg, "ir_metering_mode_set requires a real infrared payload adapter")

    # ---- 紧急控制接口 ----

    def handle_drone_emergency_stop(self, msg: dict) -> dict:
        """
        无人机紧急停止 (drone_emergency_stop)

        Request: { }
        """
        logger.info("Drone emergency stop!")
        brake_sent, brake_simulated, brake_error = self._send_zero_velocity()
        if brake_error and not brake_simulated:
            return MessageBuilder.build_reply(
                msg,
                result=DOCK_COMMUNICATION_ERROR,
                msg_str=f"Emergency brake failed: {brake_error}",
                data={
                    "output": {
                        "status": "failed",
                        "brake_sent": brake_sent,
                        "simulated": False,
                    }
                },
            )

        hold_handled, hold_ok, hold_message, hold_output = self._point_flight_emergency_hold()
        if hold_handled:
            point_flight = self._point_flight_controller
            if point_flight and getattr(point_flight, "_active_fly_to_point", None):
                fly_to_id = point_flight._active_fly_to_point.get("fly_to_id", "")
                point_flight.report_flyto_progress(fly_to_id, "wayline_cancel", result=0 if hold_ok else DOCK_COMMUNICATION_ERROR)
            elif self._active_fly_to_point:
                fly_to_id = self._active_fly_to_point.get("fly_to_id", "")
                self.report_flyto_progress(fly_to_id, "wayline_cancel", result=0 if hold_ok else DOCK_COMMUNICATION_ERROR)
            if point_flight and getattr(point_flight, "_active_takeoff_to_point", None):
                flight_id = point_flight._active_takeoff_to_point.get("flight_id", "")
                track_id = point_flight._active_takeoff_to_point.get("track_id", "")
                point_flight.report_takeoff_to_point_progress(
                    flight_id, "wayline_cancel", result=0 if hold_ok else DOCK_COMMUNICATION_ERROR, track_id=track_id
                )
            elif self._active_takeoff_to_point:
                flight_id = self._active_takeoff_to_point.get("flight_id", "")
                track_id = self._active_takeoff_to_point.get("track_id", "")
                self.report_takeoff_to_point_progress(
                    flight_id, "wayline_cancel", result=0 if hold_ok else DOCK_COMMUNICATION_ERROR, track_id=track_id
                )

            if self._drc_enabled:
                self._heartbeat_running = False
                self._drc_enabled = False
                self._session_id = ""
                self.report_drc_status_notify(drc_state=0, result=0 if hold_ok else DOCK_COMMUNICATION_ERROR)
            self._active_fly_to_point = None
            self._active_takeoff_to_point = None
            if point_flight and hasattr(point_flight, "clear_active"):
                point_flight.clear_active()
            self._write_audit_log("emergency_stop_hold_current_position", hold_message)
            return MessageBuilder.build_reply(
                msg,
                result=0 if hold_ok else DOCK_COMMUNICATION_ERROR,
                msg_str="" if hold_ok else hold_message,
                data={
                    "output": {
                        **hold_output,
                        "brake_sent": brake_sent,
                        "land_ok": False,
                        "loiter_ok": hold_ok,
                        "simulated": False,
                    }
                },
            )

        land_ok, land_simulated, land_message = self._set_emergency_land_mode()
        status = "ok" if land_ok else "failed"
        result_code = 0 if land_ok else DOCK_COMMUNICATION_ERROR
        if self._drc_enabled:
            self._heartbeat_running = False
            self._drc_enabled = False
            self._session_id = ""
            self._active_fly_to_point = None
            self._active_takeoff_to_point = None
            if self._point_flight_controller and hasattr(self._point_flight_controller, "clear_active"):
                self._point_flight_controller.clear_active()
            self._last_cloud_heartbeat_seq = None
            self.report_drc_status_notify(drc_state=0, result=0)
            self._write_audit_log("emergency_stop", land_message)
        return MessageBuilder.build_reply(
            msg,
            result=result_code,
            msg_str="" if result_code == 0 else land_message,
            data={
                "output": {
                    "status": status,
                    "brake_sent": brake_sent,
                    "land_ok": land_ok,
                    "simulated": False,
                    "message": land_message,
                }
            },
        )

    def handle_takeoff_to_point(self, msg: dict) -> dict:
        """
        起飞到指定点 (takeoff_to_point)

        Request: {
            target_latitude: double,
            target_longitude: double,
            target_height: float,
            security_takeoff_height: float,
            rth_altitude: int,
            rc_lost_action: int,
            commander_mode_lost_action: int,
            commander_flight_height: float,
            flight_id: str,
            max_speed: int
        }
        """
        data = msg.get('data', {})
        default_flight_id = msg.get('tid') or self._session_id or str(int(time.time() * 1000))
        valid, target, errors = normalize_takeoff_to_point(data, default_flight_id=default_flight_id)
        if not valid:
            reason = "; ".join(errors)
            logger.warning("takeoff_to_point payload invalid: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        target_latitude = target["target_latitude"]
        target_longitude = target["target_longitude"]
        target_height = target["target_height"]
        flight_id = target["flight_id"]
        max_speed = target["max_speed"]
        valid, reason = self._check_target_boundary(target_latitude, target_longitude, target_height)
        if not valid:
            logger.warning("takeoff_to_point boundary rejected: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        track_id = f"track-{flight_id}"
        self._last_flyto_cmd = {
            **target,
            "track_id": track_id,
            "timestamp": time.time(),
            "source": "takeoff_to_point",
        }
        self._active_takeoff_to_point = {
            "flight_id": flight_id,
            "track_id": track_id,
            "target": target,
            "timestamp": time.time(),
        }

        logger.info(
            "Takeoff to point: lat=%s, lon=%s, height=%s, flight_id=%s, max_speed=%s",
            target_latitude,
            target_longitude,
            target_height,
            flight_id,
            max_speed,
        )
        self.report_takeoff_to_point_progress(
            flight_id,
            "task_ready",
            result=0,
            track_id=track_id,
            planned_path_points=[{
                "latitude": target_latitude,
                "longitude": target_longitude,
                "height": target_height,
            }],
        )

        if not self._mode_control or not (
            hasattr(self._mode_control, 'guided_loiter_at_point') or hasattr(self._mode_control, 'guided_goto')
        ):
            self.report_takeoff_to_point_progress(
                flight_id,
                "wayline_failed",
                result=DOCK_COMMUNICATION_ERROR,
                track_id=track_id,
            )
            self._active_takeoff_to_point = None
            return self._real_adapter_unavailable(
                msg,
                "takeoff_to_point requires a real guided flight adapter",
            )

        guidance_message = "guided target sent"
        try:
            goto_result = self._send_guided_loiter_target(target_latitude, target_longitude, target_height)
            if not goto_result.get("success"):
                guidance_warning = goto_result.get("message", "guided_goto failed")
                logger.warning("takeoff_to_point guided_goto failed: %s", guidance_warning)
                self.report_takeoff_to_point_progress(
                    flight_id,
                    "wayline_failed",
                    result=DOCK_COMMUNICATION_ERROR,
                    track_id=track_id,
                )
                self._active_takeoff_to_point = None
                return self._real_adapter_unavailable(msg, guidance_warning)
            guidance_message = goto_result.get("message", guidance_message)
        except Exception as exc:
            guidance_warning = str(exc)
            logger.warning("takeoff_to_point guidance dispatch failed: %s", guidance_warning)
            self.report_takeoff_to_point_progress(
                flight_id,
                "wayline_failed",
                result=DOCK_COMMUNICATION_ERROR,
                track_id=track_id,
            )
            self._active_takeoff_to_point = None
            return self._real_adapter_unavailable(msg, guidance_warning)

        self.report_takeoff_to_point_progress(
            flight_id,
            "wayline_progress",
            result=0,
            track_id=track_id,
            remaining_distance=0,
            remaining_time=0,
            planned_path_points=[{
                "latitude": target_latitude,
                "longitude": target_longitude,
                "height": target_height,
            }],
        )
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={
                "flight_id": flight_id,
                "track_id": track_id,
                "target_point": {
                    "latitude": target_latitude,
                    "longitude": target_longitude,
                    "height": target_height,
                },
                "max_speed": max_speed,
                "output": {
                    "status": "ok",
                    "message": guidance_message,
                    "simulated": False,
                    "warning": "",
                },
            },
        )

    # ---- 状态查询 ----

    def get_drc_status(self) -> dict:
        """获取DRC状态"""
        point_flight_status = (
            self._point_flight_controller.get_status()
            if self._point_flight_controller and hasattr(self._point_flight_controller, "get_status")
            else {}
        )
        return {
            "enabled": self._drc_enabled,
            "session_id": self._session_id,
            "last_speed_cmd": self._last_speed_cmd,
            "last_heading_cmd": self._last_heading_cmd,
            "last_altitude_cmd": self._last_altitude_cmd,
            "last_flyto_cmd": point_flight_status.get("last_flyto_cmd", self._last_flyto_cmd),
            "active_fly_to_point": point_flight_status.get("active_fly_to_point", self._active_fly_to_point),
            "active_takeoff_to_point": point_flight_status.get("active_takeoff_to_point", self._active_takeoff_to_point),
            "heartbeat_timeout": self._heartbeat_timeout,
            "last_cloud_heartbeat_age": (
                time.time() - self._last_cloud_heartbeat
                if self._last_cloud_heartbeat else None
            )
        }
