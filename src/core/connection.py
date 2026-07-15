"""
MAVLink连接管理模块
core/connection.py
"""
import os
import struct
import sys
import time
from typing import Optional, Set
from pymavlink import mavutil
from threading import Lock
from queue import Queue

from src.utils.logger import get_logger


mavlink_raw_logger = get_logger("mavlink.raw")


class ConnectionStatus:
    """连接状态枚举"""
    DISCONNECTED = 0
    CONNECTING = 1
    CONNECTED = 2
    ERROR = 3


def _extract_msg_id(msg) -> Optional[int]:
    """兼容 pymavlink/生成 dialect 消息对象的 ID 提取。"""
    try:
        if hasattr(msg, 'get_msgId'):
            return msg.get_msgId()
        if hasattr(msg, 'msgid'):
            return msg.msgid
        if hasattr(msg, 'id'):
            return msg.id
    except Exception:
        return None
    return None


def _x25_checksum(data: bytes, extra: int) -> int:
    """Calculate the MAVLink X25 checksum including a message CRC extra."""
    checksum = 0xFFFF
    for byte in data + bytes([extra]):
        tmp = byte ^ (checksum & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        checksum = ((checksum >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return checksum


def build_mavlink_v1_heartbeat(source_system: int, source_component: int, sequence: int = 0) -> bytes:
    """Build a standard MAVLink v1 GCS heartbeat without relying on a dialect."""
    payload = struct.pack("<IBBBBB", 0, 6, 8, 0, 0, 3)
    header = struct.pack(
        "<BBBBBB",
        0xFE,
        len(payload),
        sequence & 0xFF,
        source_system & 0xFF,
        source_component & 0xFF,
        0,
    )
    return header + payload + struct.pack("<H", _x25_checksum(payload, 50))


def send_udp_bootstrap_heartbeat(sock, target: tuple[str, int], source_system: int, source_component: int) -> None:
    """Register a UDP peer so a passive nest simulator can return its status."""
    sock.sendto(build_mavlink_v1_heartbeat(source_system, source_component), target)


def _raw_frame_hex(msg) -> str:
    """提取 pymavlink 消息的完整帧；无法提取时保留可诊断文本。"""
    try:
        frame = msg.get_msgbuf() if hasattr(msg, "get_msgbuf") else b""
        if frame:
            return bytes(frame).hex()
    except Exception:
        pass
    return "unavailable"


def _log_raw_mavlink(direction: str, connection_string: str, msg) -> None:
    """记录 MAVLink 原始收发帧，供协议级故障回放。"""
    try:
        msg_type = msg.get_type() if hasattr(msg, "get_type") else type(msg).__name__
        msg_id = _extract_msg_id(msg)
        sysid = msg.get_srcSystem() if hasattr(msg, "get_srcSystem") else None
        compid = msg.get_srcComponent() if hasattr(msg, "get_srcComponent") else None
        mavlink_raw_logger.info(
            "direction=%s connection=%s type=%s id=%s sysid=%s compid=%s raw_hex=%s",
            direction,
            connection_string,
            msg_type,
            msg_id,
            sysid,
            compid,
            _raw_frame_hex(msg),
            # 所有 MAVLink 原始帧完整写入网关总日志和 mavlink_raw 独立日志，
            # 但不进入控制台；解析错误和业务层状态仍正常显示。
            extra={"console_silent": True},
        )
    except Exception as exc:
        mavlink_raw_logger.warning(
            "direction=%s connection=%s raw_log_error=%s",
            direction,
            connection_string,
            exc,
        )


class MAVLinkConnection:
    """MAVLink连接管理器"""

    def __init__(self,
                 connection_string: str,
                 source_system: int = 1,
                 source_component: int = 0,
                 baudrate: int = 57600,
                 use_custom_dialect: bool = False):
        """
        初始化连接

        Args:
            connection_string: 连接字符串 (tcp:, udp:, serial:等)
            source_system: 本机系统ID
            source_component: 本机组件ID
            baudrate: 串口波特率
            use_custom_dialect: 是否使用自定义dialect（机巢设备用）
        """
        self.connection_string = connection_string
        self.source_system = source_system
        self.source_component = source_component
        self.baudrate = baudrate
        self.use_custom_dialect = use_custom_dialect

        self.master: Optional[mavutil.mavlink_connection] = None
        self.status = ConnectionStatus.DISCONNECTED
        self.lock = Lock()

        self.last_heartbeat_time = 0
        self.heartbeat_timeout = 10.0  # 5秒无心跳认为断开

        self._running = False
        self._receiver_thread = None
        self._message_queue = Queue(maxsize=1000)
        self._custom_valid_message_ids: Set[int] = set()

    def connect(self) -> bool:
        """建立连接"""
        try:
            self.status = ConnectionStatus.CONNECTING

            # 如果使用自定义dialect，预先加载
            if self.use_custom_dialect:
                if not self._load_custom_dialect():
                    print("[Connection] ⚠️  自定义dialect加载失败")
                    return False

            print(f"[Connection] 等待连接: {self.connection_string}")

            # ⭐⭐⭐ 对于自定义dialect的UDP连接，手动创建 ⭐⭐⭐
            if self.use_custom_dialect and self.connection_string.startswith('udp:'):
                import socket
                import threading
                from src.generated_dialect import drone_nest
                from pymavlink.dialects.v20 import ardupilotmega
                valid_message_ids = set(self._custom_valid_message_ids)

                parts = self.connection_string.replace('udp:', '').split(':')
                host = parts[0]
                port = int(parts[1])

                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(('0.0.0.0', 0))
                sock.settimeout(1.0)

                class UDPWriter:
                    def __init__(self, sock_obj, target_addr):
                        self._sock = sock_obj
                        self._target = target_addr

                    def write(self, data):
                        self._sock.sendto(data, self._target)

                class CustomUDPMAVLinkWrapper:
                    def __init__(self, mav_instance, standard_mav_instance, sock_obj, valid_ids):
                        self.mav = mav_instance
                        self.standard_mav = standard_mav_instance
                        self._sock = sock_obj
                        self._valid_ids = valid_ids
                        self.target_system = 0
                        self.target_component = 0
                        self._message_queue = []
                        self._running = True
                        self._receiver_thread = threading.Thread(
                            target=self._receiver_loop,
                            daemon=True,
                            name="UDP_Nest_MAVLink_Receiver"
                        )
                        self._receiver_thread.start()

                    def _remember_aircraft_target(self, msg):
                        """从机巢转发回来的飞机消息学习 sys/comp，供后续控制命令使用。"""
                        try:
                            msg_type = msg.get_type()
                            sysid = int(msg.get_srcSystem())
                            compid = int(msg.get_srcComponent())
                        except Exception:
                            return
                        if not sysid or not compid:
                            return
                        if sysid >= 100:
                            return
                        if msg_type == "HEARTBEAT" or not self.target_system:
                            self.target_system = sysid
                            self.target_component = compid

                    def _receiver_loop(self):
                        while self._running:
                            try:
                                data, _ = self._sock.recvfrom(4096)
                                for byte in data:
                                    chunk = bytes([byte])
                                    try:
                                        msg = self.mav.parse_char(chunk)
                                        if msg:
                                            msg_id = _extract_msg_id(msg)
                                            if msg_id in self._valid_ids:
                                                self._message_queue.append(msg)
                                    except Exception as e:
                                        error_str = str(e).lower()
                                        if 'invalid' not in error_str and 'prefix' not in error_str:
                                            print(f"[Connection] UDP解析异常: {e}")
                                    try:
                                        standard_msg = self.standard_mav.parse_char(chunk)
                                        if standard_msg:
                                            msg_id = _extract_msg_id(standard_msg)
                                            if msg_id not in self._valid_ids:
                                                self._remember_aircraft_target(standard_msg)
                                                self._message_queue.append(standard_msg)
                                    except Exception as e:
                                        error_str = str(e).lower()
                                        if 'invalid' not in error_str and 'prefix' not in error_str:
                                            print(f"[Connection] UDP标准MAVLink解析异常: {e}")
                            except socket.timeout:
                                continue
                            except Exception as e:
                                if self._running:
                                    print(f"[Connection] UDP recv错误: {e}")
                                break

                    def recv_match(self, blocking=True, timeout=None):
                        if self._message_queue:
                            return self._message_queue.pop(0)
                        if blocking and timeout:
                            end_time = time.time() + timeout
                            while time.time() < end_time:
                                if self._message_queue:
                                    return self._message_queue.pop(0)
                                time.sleep(0.01)
                        return None

                    def close(self):
                        self._running = False
                        try:
                            self._sock.close()
                        except:
                            pass

                writer = UDPWriter(sock, (host, port))
                mav_instance = drone_nest.MAVLink(
                    writer,
                    srcSystem=self.source_system,
                    srcComponent=self.source_component
                )
                standard_mav_instance = ardupilotmega.MAVLink(
                    writer,
                    srcSystem=self.source_system,
                    srcComponent=self.source_component,
                )
                self.master = CustomUDPMAVLinkWrapper(mav_instance, standard_mav_instance, sock, valid_message_ids)

                # The nest simulator is intentionally passive: it learns the
                # gateway's ephemeral UDP address from the first packet, then
                # starts sending DRONE_NEST_RESPONSE status frames.  A standard
                # MAVLink heartbeat is non-mutating and works with real devices
                # as well as the custom dialect simulator.
                send_udp_bootstrap_heartbeat(sock, (host, port), self.source_system, self.source_component)
                print(f"[Connection] 已发送机巢 UDP 注册心跳: {host}:{port}")

                print(f"[Connection] ✅ UDP自定义dialect连接已建立: {host}:{port}")
                self.status = ConnectionStatus.CONNECTED
                self.last_heartbeat_time = time.time()
                return True

            # ⭐⭐⭐ 对于自定义dialect的TCP连接，手动创建 ⭐⭐⭐
            if self.use_custom_dialect and self.connection_string.startswith('tcp:'):
                import socket
                from src.generated_dialect import drone_nest
                from pymavlink.dialects.v20 import ardupilotmega
                valid_message_ids = set(self._custom_valid_message_ids)

                # 解析连接字符串
                parts = self.connection_string.replace('tcp:', '').split(':')
                host = parts[0]
                port = int(parts[1])

                # 创建TCP连接
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((host, port))
                print(f"[Connection] ✅ TCP连接已建立: {host}:{port}")

                # 创建file对象
                file = sock.makefile('rwb', buffering=0)

                # ⭐ 创建包装对象，使其兼容mavutil接口
                # 在 connect() 方法中，找到 CustomMAVLinkWrapper 类定义，替换为：

                class CustomMAVLinkWrapper:
                    def __init__(self, mav_instance, standard_mav_instance, file_obj, sock_obj, valid_ids):
                        self.mav = mav_instance
                        self.standard_mav = standard_mav_instance
                        self.file = file_obj
                        self._sock = sock_obj
                        self._sock.settimeout(1.0)
                        self._valid_ids = valid_ids
                        self.target_system = 0
                        self.target_component = 0

                        # ⭐ 新增：消息队列缓冲
                        self._message_queue = []
                        self._running = True

                        # ⭐ 新增：启动receiver线程持续读取数据
                        import threading
                        self._receiver_thread = threading.Thread(
                            target=self._receiver_loop,
                            daemon=True,
                            name="TCP_MAVLink_Receiver"
                        )
                        self._receiver_thread.start()

                    def _remember_aircraft_target(self, msg):
                        """从机巢转发回来的飞机消息学习 sys/comp，供后续控制命令使用。"""
                        try:
                            msg_type = msg.get_type()
                            sysid = int(msg.get_srcSystem())
                            compid = int(msg.get_srcComponent())
                        except Exception:
                            return
                        if not sysid or not compid:
                            return
                        if sysid >= 100:
                            return
                        if msg_type == "HEARTBEAT" or not self.target_system:
                            self.target_system = sysid
                            self.target_component = compid

                    def _receiver_loop(self):
                        """后台receiver线程：持续读取socket数据并解析消息"""
                        while self._running:
                            try:
                                data = self._sock.recv(4096)
                                if not data:
                                    print("[Connection] TCP连接被关闭")
                                    break

                                for byte in data:
                                    chunk = bytes([byte])
                                    try:
                                        msg = self.mav.parse_char(chunk)
                                        if msg:
                                            msg_id = _extract_msg_id(msg)
                                            if msg_id in self._valid_ids:
                                                self._message_queue.append(msg)
                                    except Exception as e:
                                        error_str = str(e).lower()
                                        if 'invalid' not in error_str and 'prefix' not in error_str:
                                            print(f"[Connection] 解析异常: {e}")
                                    try:
                                        standard_msg = self.standard_mav.parse_char(chunk)
                                        if standard_msg:
                                            msg_id = _extract_msg_id(standard_msg)
                                            if msg_id not in self._valid_ids:
                                                self._remember_aircraft_target(standard_msg)
                                                self._message_queue.append(standard_msg)
                                    except Exception as e:
                                        error_str = str(e).lower()
                                        if 'invalid' not in error_str and 'prefix' not in error_str:
                                            print(f"[Connection] 标准MAVLink解析异常: {e}")
                            except socket.timeout:
                                continue
                            except Exception as e:
                                if "timeout" not in str(e).lower():
                                    print(f"[Connection] recv错误: {e}")
                                break

                    def recv_match(self, blocking=True, timeout=None):
                        """
                        正确实现：先返回队列中的消息，队列空时才读取新数据
                        """
                        # ⭐ 1. 先检查队列中是否有待返回的消息
                        if self._message_queue:
                            return self._message_queue.pop(0)

                        # ⭐ 2. 阻塞模式下等待消息
                        if blocking and timeout:
                            import time
                            end_time = time.time() + timeout
                            while time.time() < end_time:
                                if self._message_queue:
                                    return self._message_queue.pop(0)
                                time.sleep(0.01)

                        return None

                    def close(self):
                        """关闭连接"""
                        self._running = False
                        try:
                            self._sock.close()
                        except:
                            pass

                # 使用自定义dialect
                mav_instance = drone_nest.MAVLink(
                    file,
                    srcSystem=self.source_system,
                    srcComponent=self.source_component
                )
                standard_mav_instance = ardupilotmega.MAVLink(
                    file,
                    srcSystem=self.source_system,
                    srcComponent=self.source_component,
                )

                self.master = CustomMAVLinkWrapper(mav_instance, standard_mav_instance, file, sock, valid_message_ids)

                print(f"[Connection] ✅ 使用自定义dialect包装连接")
                self.status = ConnectionStatus.CONNECTED
                self.last_heartbeat_time = time.time()
                return True

            # ⭐ 标准连接（飞机等）
            self.master = mavutil.mavlink_connection(
                self.connection_string,
                source_system=self.source_system,
                source_component=self.source_component,
                baud=self.baudrate
            )

            # 自定义dialect但非TCP（如UDP）
            if self.use_custom_dialect:
                print(f"[Connection] ✅ 自定义dialect连接已建立（无需心跳验证）")
                self.status = ConnectionStatus.CONNECTED
                self.last_heartbeat_time = time.time()
                return True

            # 标准连接等待心跳
            print(f"[Connection] 等待心跳: {self.connection_string}")
            msg = self.master.wait_heartbeat(timeout=10)

            if msg:
                self.status = ConnectionStatus.CONNECTED
                self.last_heartbeat_time = time.time()
                print(f"[Connection] 已连接到系统 {msg.get_srcSystem()}")
                return True
            else:
                print(f"[Connection] 心跳超时")
                self.status = ConnectionStatus.ERROR
                return False

        except Exception as e:
            print(f"[Connection] 连接失败: {e}")
            import traceback
            traceback.print_exc()
            self.status = ConnectionStatus.ERROR
            return False

    def _load_custom_dialect(self) -> bool:
        """
        加载自定义dialect（内部方法）

        Returns:
            bool: 是否成功加载
        """
        try:
            # 添加生成的dialect到Python路径
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            dialect_path = os.path.join(project_root, 'generated_dialect')

            if not os.path.exists(dialect_path):
                print(f"[Connection] ❌ Dialect路径不存在: {dialect_path}")
                print(f"            请运行: python mavlink_definitions/generate_dialect.py")
                return False

            if dialect_path not in sys.path:
                sys.path.insert(0, dialect_path)
                print(f"[Connection] 📦 已添加dialect路径: {dialect_path}")

            # 设置MAVLink 2.0
            os.environ['MAVLINK20'] = '1'

            # 预加载自定义dialect模块
            import drone_nest
            print(f"[Connection] ✅ 已预加载自定义dialect: drone_nest")
            self._custom_valid_message_ids = set(getattr(drone_nest, 'mavlink_map', {}).keys())

            # 验证关键方法是否存在
            # 替换第226-236行
            if hasattr(drone_nest, 'MAVLink'):
                test_mav = drone_nest.MAVLink(None)

                # ✅ 检查真实存在的三个方法
                required = {
                    'drone_nest_command_send': 'DRONE_NEST_COMMAND (12003)',
                    'drone_nest_response_send': 'DRONE_NEST_RESPONSE (12004)',
                    'drone_nest_order_response_send': 'DRONE_NEST_ORDER_RESPONSE (12005)',
                }

                all_ok = True
                for method, desc in required.items():
                    if hasattr(test_mav, method):
                        print(f"[Connection] ✅ 已验证: {desc}")
                    else:
                        print(f"[Connection] ⚠️  缺失方法: {method} ({desc})")
                        all_ok = False

                if all_ok:
                    print(f"[Connection] ✅ 自定义消息方法已验证")
            print(f"[Connection] ✅ 合法自定义消息ID: {sorted(self._custom_valid_message_ids)}")
            print(f"[Connection] 📦 使用自定义dialect (MAVLink 2.0)")
            return True

        except ImportError as e:
            print(f"[Connection] ❌ 无法导入自定义dialect: {e}")
            print(f"            请确保已运行: python mavlink_definitions/generate_dialect.py")
            return False
        except Exception as e:
            print(f"[Connection] ❌ 加载dialect时发生错误: {e}")
            import traceback
            traceback.print_exc()
            return False

    def disconnect(self):
        """断开连接"""
        with self.lock:
            if self.master:
                try:
                    self.master.close()
                except:
                    pass
                self.master = None
            self.status = ConnectionStatus.DISCONNECTED
            print("[Connection] 连接已断开")

    def recv_message(self, timeout: float = 0.01):
        """
        接收消息

        Args:
            timeout: 超时时间（秒）

        Returns:
            MAVLink消息对象或None
        """
        if not self.master or self.status != ConnectionStatus.CONNECTED:
            return None

        try:
            msg = self.master.recv_match(blocking=True, timeout=timeout)
            if msg is not None:
                _log_raw_mavlink("RX", self.connection_string, msg)
            return msg
        except Exception as e:
            # 静默处理超时，只记录真正的错误
            if "timeout" not in str(e).lower():
                print(f"[Connection] 接收消息错误: {e}")
            return None

    def send_message(self, msg):
        """
        发送消息

        Args:
            msg: MAVLink消息对象
        """
        with self.lock:
            if self.master and self.status == ConnectionStatus.CONNECTED:
                try:
                    mav = self._select_sender_for_message(msg)
                    mav.send(msg)
                    _log_raw_mavlink("TX", self.connection_string, msg)
                except Exception as e:
                    print(f"[Connection] 发送消息错误: {e}")

    def _select_sender_for_message(self, msg):
        """Select the matching MAVLink encoder for mixed nest/aircraft links."""
        custom_ids = self._custom_valid_message_ids or set()
        msg_id = _extract_msg_id(msg)
        if (
            self.use_custom_dialect
            and msg_id not in custom_ids
            and hasattr(self.master, "standard_mav")
        ):
            return self.master.standard_mav
        return self.master.mav

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self.status == ConnectionStatus.CONNECTED

    def update_heartbeat(self):
        """更新心跳时间"""
        self.last_heartbeat_time = time.time()

    def check_heartbeat_timeout(self) -> bool:
        """
        检查心跳超时

        Returns:
            bool: 是否超时
        """
        if self.status != ConnectionStatus.CONNECTED:
            return False

        elapsed = time.time() - self.last_heartbeat_time
        return elapsed > self.heartbeat_timeout

    def get_connection_info(self) -> dict:
        """
        获取连接信息

        Returns:
            dict: 连接信息字典
        """
        return {
            'connection_string': self.connection_string,
            'status': self._status_to_string(self.status),
            'source_system': self.source_system,
            'source_component': self.source_component,
            'target_system': self.master.target_system if self.master else None,
            'target_component': self.master.target_component if self.master else None,
            'use_custom_dialect': self.use_custom_dialect,
            'last_heartbeat': self.last_heartbeat_time,
            'heartbeat_age': time.time() - self.last_heartbeat_time if self.last_heartbeat_time > 0 else None
        }

    def _status_to_string(self, status: int) -> str:
        """将状态码转换为字符串"""
        status_map = {
            ConnectionStatus.DISCONNECTED: 'disconnected',
            ConnectionStatus.CONNECTING: 'connecting',
            ConnectionStatus.CONNECTED: 'connected',
            ConnectionStatus.ERROR: 'error'
        }
        return status_map.get(status, 'unknown')
