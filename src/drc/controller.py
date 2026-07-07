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
from src.utils.dji_error_codes import DEVICE_COMMAND_TIMEOUT, DEVICE_STATE_UNSUPPORTED, INVALID_COMMAND_FORMAT
from src.cloud_api.dock3_drc import (
    normalize_drone_control,
    normalize_stick_control,
    normalize_takeoff_to_point,
    validate_drc_heartbeat,
    validate_drc_mode_enter,
    validate_gimbal_speed_push,
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

        # MAVLink模块引用(由外部注入)
        self._mode_control = None
        self._flight_control = None
        self._mission_manager = None  # 任务管理器引用
        self._telemetry_module = None  # 遥测模块引用
        self._gimbal_control = None  # 云台控制引用
        self._camera_controller = None  # 相机控制器引用

        # 回调
        self._event_callback: Optional[Callable] = None

        # 心跳保活机制
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_running = False
        self._last_cloud_heartbeat = 0  # 云端心跳时间戳
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

    def _send_mavlink_message(self, msg) -> bool:
        """发送MAVLink消息"""
        conn = self._get_mavlink_connection()
        if conn and hasattr(conn, 'send_message'):
            conn.send_message(msg)
            return True
        logger.warning("MAVLink not connected, cannot send DRC command")
        return False

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
                    self._mission_manager.handle_pause_wayline({'data': {'task_id': task_id}})
                return True

        return True

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

                # 检查云端心跳超时
                if current_time - self._last_cloud_heartbeat > self._heartbeat_timeout:
                    logger.warning(f"DRC heartbeat timeout! Last cloud heartbeat: "
                                  f"{current_time - self._last_cloud_heartbeat:.1f}s ago")
                    # 自动退出DRC模式
                    self._auto_exit_drc("heartbeat_timeout")
                    break

                # 定期发送设备心跳（每10秒）
                if current_time - self._last_device_heartbeat > 10:
                    self._send_device_heartbeat()
                    self._last_device_heartbeat = current_time

                time.sleep(1)
            except Exception as e:
                logger.error(f"Heartbeat monitor error: {e}")

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
        2. 暂停任务：如正在执行航线任务，先暂停
        3. 保存原模式：记录进入前的飞行模式
        4. 模式切换：切换至GUIDED模式
        5. 启动心跳：建立心跳保活机制
        6. 上报事件：drc_status_notify

        Request: { }
        """
        data = msg.get('data', {})
        valid, errors = validate_drc_mode_enter(data)
        if not valid:
            reason = "; ".join(errors)
            logger.warning("DRC enter payload invalid: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        if self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC already enabled")

        # 1. 状态校验
        can_enter, reason = self._check_flight_status()
        if not can_enter:
            logger.warning(f"DRC enter blocked: {reason}")
            return MessageBuilder.build_reply(msg, result=-1, msg_str=reason)

        # 2. 保存原模式
        if self._mode_control:
            mode_info = self._mode_control.get_current_mode()
            self._original_mode = mode_info.get('mode_name')
            logger.info(f"DRC: 保存原模式 {self._original_mode}")

        # 3. 暂停当前任务
        self._pause_current_mission()

        # 4. 进入DRC状态
        self._drc_enabled = True
        self._session_id = str(int(time.time() * 1000))
        logger.info(f"DRC mode entered, session={self._session_id}")

        # 5. 切换到GUIDED模式以支持实时控制
        if self._mode_control:
            result = self._mode_control.set_mode("GUIDED")
            if not result.get("success"):
                logger.warning(f"DRC进入GUIDED模式失败: {result.get('message')}")
                # 即使失败也保持DRC状态，允许后续重试

        # 6. 启动心跳监控
        self._start_heartbeat_monitor()

        # 7. 上报进入成功事件
        self.report_drc_status_notify(drc_state=2, result=0)

        # 8. 写入审计日志
        self._write_audit_log("mode_enter", f"session={self._session_id}")

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

        self._last_cloud_heartbeat = time.time()
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
        conn = self._get_mavlink_connection()
        if conn and hasattr(conn, 'master') and conn.master:
            try:
                conn.master.mav.set_position_target_local_ned_send(
                    0,  # time_boot_ms
                    conn.master.target_system,
                    conn.master.target_component,
                    mavutil.mavlink.MAV_FRAME_BODY_NED,
                    0b0000111111000111,  # 仅使用vx,vy,vz,yaw_rate
                    0, 0, 0,  # x,y,z (忽略)
                    vx, vy, vz,  # 速度
                    0, 0, 0,  # 加速度 (忽略)
                    0, yaw_rate  # yaw, yaw_rate
                )
                logger.debug(f"DRC speed sent: vx={vx}, vy={vy}, vz={vz}, yaw_rate={yaw_rate}")
            except Exception as e:
                logger.error(f"DRC speed send failed: {e}")

        return MessageBuilder.build_reply(msg, result=0)

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

        logger.info(f"DRC flyto: lat={lat}, lon={lon}, alt={alt}, speed={speed}")

        # 通过ModeControl发送GUIDED位置目标
        if self._mode_control and hasattr(self._mode_control, 'guided_goto'):
            result = self._mode_control.guided_goto(lat, lon, alt)
            if not result.get("success"):
                logger.warning(f"DRC flyto failed: {result.get('message')}")

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

        # 发送速度指令
        conn = self._get_mavlink_connection()
        if conn and hasattr(conn, 'master') and conn.master:
            try:
                conn.master.mav.set_position_target_local_ned_send(
                    0,
                    conn.master.target_system,
                    conn.master.target_component,
                    mavutil.mavlink.MAV_FRAME_BODY_NED,
                    0b0000111111000111,  # 仅使用vx,vy,vz,yaw_rate
                    0, 0, 0,
                    vx, vy, vz,
                    0, 0, 0,
                    0, yaw_rate
                )
            except Exception as e:
                logger.error(f"Drone control send failed: {e}")

        return MessageBuilder.build_reply(msg, result=0)

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
        payload_index = data.get('payload_index', '0')
        action = data.get('action', '')
        params = data.get('params', {})

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        logger.info(f"Payload control: index={payload_index}, action={action}, params={params}")

        # 根据 action 分发到对应模块
        try:
            if action == 'photo_take':
                # 拍照 - 调用相机控制器
                if self._camera_controller:
                    self._camera_controller.handle_camera_photo_take({'data': {'camera_index': int(payload_index)}})

            elif action == 'record_start':
                # 开始录像
                if self._camera_controller:
                    self._camera_controller.handle_camera_record_start({'data': {'camera_index': int(payload_index)}})

            elif action == 'record_stop':
                # 停止录像
                if self._camera_controller:
                    self._camera_controller.handle_camera_record_stop({'data': {'camera_index': int(payload_index)}})

            elif action == 'zoom':
                # 变焦
                zoom_level = params.get('level', 1)
                if self._gimbal_control:
                    self._gimbal_control.set_zoom(zoom_level)

            elif action == 'gimbal_rotate':
                # 云台旋转
                pitch = params.get('pitch')
                yaw = params.get('yaw')
                roll = params.get('roll')
                if self._gimbal_control:
                    self._gimbal_control.turn_to(yaw, pitch)
                    logger.info(f"Payload gimbal rotate: pitch={pitch}, yaw={yaw}, roll={roll}")

        except Exception as e:
            logger.error(f"Payload control dispatch error: {e}")
            return MessageBuilder.build_reply(msg, result=-1, msg_str=str(e))

        return MessageBuilder.build_reply(msg, result=0)

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
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

    def handle_fly_to_point_update(self, msg: dict) -> dict:
        """
        更新指点飞行目标点 (fly_to_point_update)

        Request: {
            max_speed: int,
            points: [ { latitude, longitude, height } ]
        }
        """
        data = msg.get('data', {})
        max_speed = data.get('max_speed', 12)
        points = data.get('points', [])
        logger.info(f"Fly to point update: max_speed={max_speed}, points={points}")
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

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
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

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
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

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
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

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
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

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
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

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
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

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
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

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
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

    def handle_poi_mode_exit(self, msg: dict) -> dict:
        """
        退出POI模式 (poi_mode_exit)

        Request: { }
        """
        logger.info("POI mode exit")
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

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
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

    # ---- 紧急控制接口 ----

    def handle_drone_emergency_stop(self, msg: dict) -> dict:
        """
        无人机紧急停止 (drone_emergency_stop)

        Request: { }
        """
        logger.info("Drone emergency stop!")
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

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
        track_id = f"track-{flight_id}"
        self._last_flyto_cmd = {
            **target,
            "track_id": track_id,
            "timestamp": time.time(),
            "source": "takeoff_to_point",
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

        simulated = True
        guidance_message = "simulated"
        guidance_warning = ""
        if self._mode_control and hasattr(self._mode_control, 'guided_goto'):
            try:
                can_send_guided_target = True
                if hasattr(self._mode_control, 'set_mode'):
                    mode_result = self._mode_control.set_mode("GUIDED")
                    if not mode_result.get("success"):
                        guidance_warning = mode_result.get("message", "GUIDED mode switch failed")
                        can_send_guided_target = False
                        logger.warning("takeoff_to_point GUIDED mode failed: %s", guidance_warning)
                    else:
                        simulated = False
                if can_send_guided_target:
                    goto_result = self._mode_control.guided_goto(target_latitude, target_longitude, target_height)
                    if not goto_result.get("success"):
                        guidance_warning = goto_result.get("message", "guided_goto failed")
                        logger.warning("takeoff_to_point guided_goto failed: %s", guidance_warning)
                    else:
                        simulated = False
                        guidance_message = goto_result.get("message", "guided target sent")
            except Exception as exc:
                guidance_warning = str(exc)
                logger.warning("takeoff_to_point guidance dispatch failed: %s", guidance_warning)

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
                    "simulated": simulated,
                    "warning": guidance_warning,
                },
            },
        )

    # ---- 状态查询 ----

    def get_drc_status(self) -> dict:
        """获取DRC状态"""
        return {
            "enabled": self._drc_enabled,
            "session_id": self._session_id,
            "last_speed_cmd": self._last_speed_cmd,
            "last_heading_cmd": self._last_heading_cmd,
            "last_altitude_cmd": self._last_altitude_cmd,
            "last_flyto_cmd": self._last_flyto_cmd,
            "heartbeat_timeout": self._heartbeat_timeout,
            "last_cloud_heartbeat_age": (
                time.time() - self._last_cloud_heartbeat
                if self._last_cloud_heartbeat else None
            )
        }
