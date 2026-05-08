"""
DRC(直接遥控)控制模块
处理上云API的DRC实时控制指令
参考大疆Pilot 2 / Cloud API DRC接口
"""
import math
from typing import Optional, Callable
from pymavlink import mavutil
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder

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

        # 最新控制指令缓存
        self._last_speed_cmd = None
        self._last_heading_cmd = None
        self._last_altitude_cmd = None
        self._last_flyto_cmd = None

        # MAVLink模块引用(由外部注入)
        self._mode_control = None
        self._flight_control = None

        # 回调
        self._event_callback: Optional[Callable] = None

    def set_event_callback(self, callback: Callable):
        """设置事件上报回调"""
        self._event_callback = callback

    def set_mode_control(self, mode_control):
        """注入模式控制模块"""
        self._mode_control = mode_control

    def set_flight_control(self, flight_control):
        """注入飞行控制模块"""
        self._flight_control = flight_control

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
            logger.info(f"DRC status reported: state={drc_state}")

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
                'remaining_time': remaining_time
            }
            if planned_path_points:
                data['planned_path_points'] = planned_path_points
            event = MessageBuilder.build_event(
                'takeoff_to_point_progress', data, self.gateway_sn
            )
            self._event_callback(event)

    # ---- DRC模式管理 ----

    def handle_drc_mode_enter(self, msg: dict) -> dict:
        """
        进入DRC模式

        Request: { }
        """
        if self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC already enabled")

        self._drc_enabled = True
        self._session_id = str(int(__import__('time').time() * 1000))
        logger.info(f"DRC mode entered, session={self._session_id}")

        # 切换到GUIDED模式以支持实时控制
        if self._mode_control:
            result = self._mode_control.set_mode("GUIDED")
            if not result.get("success"):
                logger.warning(f"DRC进入GUIDED模式失败: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0, data={
            "drc_session_id": self._session_id,
            "status": "enabled"
        })

    def handle_drc_mode_exit(self, msg: dict) -> dict:
        """
        退出DRC模式

        Request: { }
        """
        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        self._drc_enabled = False
        self._session_id = ""
        logger.info("DRC mode exited")

        # 恢复到AUTO模式(自动控制)
        if self._mode_control:
            result = self._mode_control.set_mode("AUTO")
            if not result.get("success"):
                logger.warning(f"DRC退出恢复AUTO模式失败: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0)

    # ---- DRC实时控制 ----

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
        self._last_speed_cmd = {
            'vx': vx, 'vy': vy, 'vz': vz, 'yaw_rate': yaw_rate
        }

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        # 通过MAVLink发送速度指令 (SET_POSITION_TARGET_LOCAL_NED)
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
        self._last_altitude_cmd = {'altitude': altitude}

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

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
        h_speed = data.get('horizontal_speed', 0)
        h_dir = data.get('horizontal_direction', 0)
        v_speed = data.get('vertical_speed', 0)
        v_dir = data.get('vertical_direction', 0)
        yaw = data.get('yaw', 0)

        if not self._drc_enabled:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="DRC not enabled")

        # 将极坐标速度转换为机体坐标系速度
        # h_dir: 0=前, 90=右, 180=后, 270=左
        rad = math.radians(h_dir)
        vx = h_speed * math.cos(rad)
        vy = h_speed * math.sin(rad)
        vz = v_speed * (1 if v_dir > 0 else -1)

        logger.info(f"Drone control: vx={vx:.2f}, vy={vy:.2f}, vz={vz:.2f}, yaw={yaw}")

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
                    yaw, 0  # yaw, yaw_rate
                )
            except Exception as e:
                logger.error(f"Drone control send failed: {e}")

        return MessageBuilder.build_reply(msg, result=0)

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
        # TODO: 转发负载控制指令

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

        logger.info(f"DRC gimbal speed: pitch={pitch}, yaw={yaw}, roll={roll}")
        # TODO: 发送云台速度控制指令

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
        target_latitude = data.get('target_latitude', 0)
        target_longitude = data.get('target_longitude', 0)
        target_height = data.get('target_height', 50)
        flight_id = data.get('flight_id', '')
        max_speed = data.get('max_speed', 12)
        logger.info(f"Takeoff to point: lat={target_latitude}, lon={target_longitude}, "
                   f"height={target_height}, flight_id={flight_id}, max_speed={max_speed}")
        # TODO: 实现具体逻辑
        return MessageBuilder.build_reply(msg, result=0)

    # ---- 状态查询 ----

    def get_drc_status(self) -> dict:
        """获取DRC状态"""
        return {
            "enabled": self._drc_enabled,
            "session_id": self._session_id,
            "last_speed_cmd": self._last_speed_cmd,
            "last_heading_cmd": self._last_heading_cmd,
            "last_altitude_cmd": self._last_altitude_cmd,
            "last_flyto_cmd": self._last_flyto_cmd
        }
