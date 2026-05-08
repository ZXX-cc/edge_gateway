"""
MAVLink适配器模块
处理与飞控的MAVLink通信，包括连接管理、消息收发、心跳维护
"""
import time
import threading
from typing import Callable, Optional, Dict, Any
from pymavlink import mavutil

from src.utils.logger import get_logger
from src.utils.config import Config

logger = get_logger("mavlink")


class MAVLinkAdapter:
    """
    MAVLink通信适配器

    职责:
    1. 建立并维护与飞控的MAVLink连接(串口/UDP/TCP)
    2. 发送MAVLink命令到飞控
    3. 接收并解析MAVLink消息
    4. 维护心跳(HEARTBEAT)
    5. 将MAVLink消息转换为内部事件

    支持连接类型:
    - serial: 串口连接(如 /dev/ttyACM0)
    - udp: UDP连接(如 127.0.0.1:14550)
    - tcp: TCP连接
    """

    def __init__(self):
        self.config = Config.get_mavlink_config()
        self.conn_type = self.config['connection_type']
        self.system_id = self.config['system_id']
        self.component_id = self.config['component_id']

        self.master: Optional[mavutil.mavlink_connection] = None
        self.connected = False
        self.target_system = 1       # 飞控系统ID
        self.target_component = 1    # 飞控组件ID

        self._running = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._receive_thread: Optional[threading.Thread] = None
        self._message_handlers: Dict[str, Callable] = {}
        self._telemetry_callbacks: Dict[str, Callable] = {}

    def connect(self) -> bool:
        """建立MAVLink连接"""
        try:
            if self.conn_type == 'serial':
                port = self.config['port']
                baud = self.config['baudrate']
                logger.info(f"Connecting MAVLink via serial {port} @ {baud}")
                self.master = mavutil.mavlink_connection(port, baud=baud)

            elif self.conn_type == 'udp':
                host = self.config.get('host', '127.0.0.1')
                port = self.config.get('udp_port', 14550)
                conn_str = f"udp:{host}:{port}"
                logger.info(f"Connecting MAVLink via UDP {conn_str}")
                self.master = mavutil.mavlink_connection(conn_str)

            elif self.conn_type == 'tcp':
                host = self.config.get('host', '127.0.0.1')
                port = self.config.get('udp_port', 5760)
                conn_str = f"tcp:{host}:{port}"
                logger.info(f"Connecting MAVLink via TCP {conn_str}")
                self.master = mavutil.mavlink_connection(conn_str)

            else:
                logger.error(f"Unsupported connection type: {self.conn_type}")
                return False

            # 等待心跳确认连接
            logger.info("Waiting for heartbeat...")
            self.master.wait_heartbeat(timeout=10)
            self.target_system = self.master.target_system
            self.target_component = self.master.target_component
            self.connected = True
            logger.info(f"Heartbeat from system {self.target_system}, component {self.target_component}")

            # 启动线程
            self._running = True
            self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self._heartbeat_thread.start()

            self._receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self._receive_thread.start()

            # 请求数据流
            self._request_data_streams()

            return True

        except Exception as e:
            logger.error(f"MAVLink connection failed: {e}")
            return False

    def disconnect(self):
        """断开MAVLink连接"""
        self._running = False
        self.connected = False
        if self.master:
            self.master.close()
        logger.info("MAVLink disconnected")

    def send_command_long(self, command: int, params: list, timeout: int = 10) -> Optional[dict]:
        """
        发送MAVLink COMMAND_LONG

        Args:
            command: MAV_CMD枚举值
            params: [param1, param2, param3, param4, param5, param6, param7]
            timeout: 等待ACK超时(秒)

        Returns:
            COMMAND_ACK消息 或 None
        """
        if not self.connected or not self.master:
            logger.warning("Cannot send command, MAVLink not connected")
            return None

        try:
            self.master.mav.command_long_send(
                self.target_system,
                self.target_component,
                command,
                0,  # confirmation
                *params
            )
            # 等待ACK
            ack = self.master.recv_match(
                type='COMMAND_ACK',
                blocking=True,
                timeout=timeout
            )
            if ack:
                return {
                    'command': ack.command,
                    'result': ack.result,
                    'progress': ack.progress if hasattr(ack, 'progress') else 0
                }
            return None
        except Exception as e:
            logger.error(f"Command send error: {e}")
            return None

    def send_mission_item(self, seq: int, frame: int, command: int,
                          current: int, autocontinue: int, params: list):
        """发送单个航点任务项"""
        if not self.master:
            return
        self.master.mav.mission_item_send(
            self.target_system,
            self.target_component,
            seq, 0,  # seq, current
            frame, command,
            current, autocontinue,
            *params
        )

    def set_mode(self, mode: int) -> bool:
        """设置飞行模式"""
        result = self.send_command_long(
            mavutil.mavlink.MAV_CMD_DO_SET_MODE,
            [mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode, 0, 0, 0, 0, 0]
        )
        return result is not None and result.get('result') == mavutil.mavlink.MAV_RESULT_ACCEPTED

    def arm(self, arm: bool = True) -> bool:
        """解锁/锁定电机"""
        result = self.send_command_long(
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            [1 if arm else 0, 0, 0, 0, 0, 0, 0]
        )
        return result is not None and result.get('result') == mavutil.mavlink.MAV_RESULT_ACCEPTED

    def takeoff(self, altitude: float) -> bool:
        """起飞到指定高度"""
        result = self.send_command_long(
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            [0, 0, 0, 0, 0, 0, altitude]
        )
        return result is not None and result.get('result') == mavutil.mavlink.MAV_RESULT_ACCEPTED

    def land(self) -> bool:
        """降落"""
        result = self.send_command_long(
            mavutil.mavlink.MAV_CMD_NAV_LAND,
            [0, 0, 0, 0, 0, 0, 0]
        )
        return result is not None and result.get('result') == mavutil.mavlink.MAV_RESULT_ACCEPTED

    def return_to_launch(self) -> bool:
        """返航(RTL)"""
        result = self.send_command_long(
            mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
            [0, 0, 0, 0, 0, 0, 0]
        )
        return result is not None and result.get('result') == mavutil.mavlink.MAV_RESULT_ACCEPTED

    def send_position_target(self, lat: int, lon: int, alt: float,
                             vx: float = 0, vy: float = 0, vz: float = 0,
                             yaw: float = 0) -> bool:
        """
        发送位置目标(用于flyto)

        Args:
            lat: 纬度(度 * 1e7)
            lon: 经度(度 * 1e7)
            alt: 高度(m)
            vx/vy/vz: 速度(m/s)
            yaw: 航向(rad)
        """
        if not self.master:
            return False
        try:
            self.master.mav.set_position_target_global_int_send(
                0,  # time_boot_ms
                self.target_system,
                self.target_component,
                mavutil.mavlink.MAV_FRAME_GLOBAL_INT,
                0b0000111111111000,  # type_mask
                lat, lon, alt,
                vx, vy, vz,
                0, 0, 0,  # afx, afy, afz
                yaw, 0    # yaw, yaw_rate
            )
            return True
        except Exception as e:
            logger.error(f"Position target error: {e}")
            return False

    def send_velocity(self, vx: float, vy: float, vz: float,
                      yaw_rate: float = 0) -> bool:
        """发送速度指令(DRC速度控制)"""
        if not self.master:
            return False
        try:
            self.master.mav.set_position_target_local_ned_send(
                0,
                self.target_system,
                self.target_component,
                mavutil.mavlink.MAV_FRAME_BODY_NED,
                0b0000111111000111,  # 仅速度+偏航率
                0, 0, 0,  # 位置忽略
                vx, vy, vz,
                0, 0, 0,
                0, yaw_rate
            )
            return True
        except Exception as e:
            logger.error(f"Velocity command error: {e}")
            return False

    def register_message_handler(self, msg_type: str, handler: Callable):
        """注册MAVLink消息处理器"""
        self._message_handlers[msg_type] = handler

    def register_telemetry_callback(self, name: str, callback: Callable):
        """注册遥测数据回调"""
        self._telemetry_callbacks[name] = callback

    # ---- 内部方法 ----

    def _heartbeat_loop(self):
        """心跳发送循环"""
        while self._running and self.master:
            try:
                self.master.mav.heartbeat_send(
                    mavutil.mavlink.MAV_TYPE_GCS,
                    mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                    0, 0, 0
                )
                time.sleep(1)
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                time.sleep(1)

    def _receive_loop(self):
        """MAVLink消息接收循环"""
        while self._running and self.master:
            try:
                msg = self.master.recv_match(blocking=False)
                if msg:
                    self._handle_message(msg)
                time.sleep(0.001)  # 1ms轮询
            except Exception as e:
                logger.error(f"Receive error: {e}")
                time.sleep(0.1)

    def _handle_message(self, msg):
        """处理接收到的MAVLink消息"""
        msg_type = msg.get_type()

        # 调用注册的消息处理器
        handler = self._message_handlers.get(msg_type)
        if handler:
            try:
                handler(msg)
            except Exception as e:
                logger.error(f"Message handler error for {msg_type}: {e}")

        # 分发遥测数据
        self._dispatch_telemetry(msg_type, msg)

    def _dispatch_telemetry(self, msg_type: str, msg):
        """分发遥测数据到回调"""
        telemetry_map = {
            'GLOBAL_POSITION_INT': 'position',
            'ATTITUDE': 'attitude',
            'SYS_STATUS': 'battery',
            'GPS_RAW_INT': 'gps',
            'HEARTBEAT': 'heartbeat',
            'VFR_HUD': 'vfr_hud',
            'WIND': 'wind',
            'BATTERY_STATUS': 'battery_status',
        }
        telem_name = telemetry_map.get(msg_type)
        if telem_name and telem_name in self._telemetry_callbacks:
            try:
                self._telemetry_callbacks[telem_name](msg)
            except Exception as e:
                logger.error(f"Telemetry callback error: {e}")

    def _request_data_streams(self):
        """请求飞控数据流"""
        if not self.master:
            return
        streams = [
            (mavutil.mavlink.MAV_DATA_STREAM_ALL, 4),
        ]
        for stream_id, rate in streams:
            self.master.mav.request_data_stream_send(
                self.target_system,
                self.target_component,
                stream_id,
                rate,
                1  # start
            )
        logger.info("Data streams requested")
