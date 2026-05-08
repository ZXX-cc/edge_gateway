"""
RTK定位模块 - 支持实时GPS位置（生产模式）和固定坐标（测试模式）
modules/rtk_module.py
"""
import socket
import base64
import time
import errno
from threading import Thread, Lock
from typing import Optional, Dict, Any, Callable
from src.modules.base_module import BaseModule
from src.core.message_dispatcher import MAVLinkMessage


def generate_gga(lat: float, lon: float, alt: float = 0.0,
                 fix_quality: int = 1, num_sats: int = 12) -> str:
    """
    生成 GNGGA 语句

    Args:
        lat: 纬度(度,正北负南)
        lon: 经度(度,正东负西)
        alt: 海拔高度(米)
        fix_quality: 定位质量 (0=无效, 1=GPS, 2=DGPS, 4=RTK固定, 5=RTK浮点)
        num_sats: 可见卫星数

    Returns:
        完整的 GNGGA 语句字符串
    """
    now = time.gmtime()
    utc_time = f"{now.tm_hour:02d}{now.tm_min:02d}{now.tm_sec:02d}.00"

    # 转换纬度为度分格式 (DDMM.MMMM)
    lat_deg = int(abs(lat))
    lat_min = (abs(lat) - lat_deg) * 60
    lat_str = f"{lat_deg:02d}{lat_min:07.4f}"
    lat_dir = 'N' if lat >= 0 else 'S'

    # 转换经度为度分格式 (DDDMM.MMMM)
    lon_deg = int(abs(lon))
    lon_min = (abs(lon) - lon_deg) * 60
    lon_str = f"{lon_deg:03d}{lon_min:07.4f}"
    lon_dir = 'E' if lon >= 0 else 'W'

    hdop = "1.0"
    alt_str = f"{alt:.1f}"
    geoid_sep = "0.0"
    diff_age = ""
    diff_station = "0000"

    gga_no_checksum = (
        f"GNGGA,{utc_time},{lat_str},{lat_dir},{lon_str},{lon_dir},"
        f"{fix_quality},{num_sats},{hdop},{alt_str},M,{geoid_sep},M,"
        f"{diff_age},{diff_station}"
    )

    checksum = 0
    for char in gga_no_checksum:
        checksum ^= ord(char)

    gga = f"${gga_no_checksum}*{checksum:02X}\r\n"
    return gga


# ⭐ 北京市固定坐标(仅用于测试模式)
BEIJING_LAT = 39.9042
BEIJING_LON = 116.4074
BEIJING_ALT = 43.0

DEFAULT_GGA = generate_gga(BEIJING_LAT, BEIJING_LON, BEIJING_ALT)
TIMEOUT_INTERVAL = 30


class NTRIPClient:
    """NTRIP 客户端(支持实时位置更新)"""

    def __init__(self, host: str, port: int, mountpoint: str,
                 username: str, password: str, gga: str = DEFAULT_GGA):
        self.host = host
        self.port = port
        self.mountpoint = mountpoint
        self.username = username
        self.password = password
        self.gga = gga

        self.socket: Optional[socket.socket] = None
        self.connected = False
        self.running = False
        self.error = False

        self.received_packets = 0
        self.total_bytes = 0

        self.gga_update_callback: Optional[Callable] = None
        self._disconnect_reason = None

    def set_gga_update_callback(self, callback: Callable):
        """设置 GGA 更新回调函数"""
        self.gga_update_callback = callback

    def update_gga(self, gga: str):
        """更新 GGA 数据"""
        self.gga = gga

    def connect(self) -> bool:
        """连接到 NTRIP 服务器"""
        try:
            self.connected = False
            self.error = False
            self.received_packets = 0
            self.total_bytes = 0

            if self.socket:
                try:
                    self.socket.close()
                except:
                    pass

            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(15)

            print(f"[RTK] 正在连接 {self.host}:{self.port}...")
            self.socket.connect((self.host, self.port))
            print(f"[RTK] TCP 连接成功")

            auth = f"{self.username}:{self.password}"
            encoded_auth = base64.b64encode(auth.encode()).decode()

            request = f"GET /{self.mountpoint} HTTP/1.1\r\n"
            request += f"Host: {self.host}\r\n"
            request += "User-Agent: NTRIP GNSSInternetRadio/1.4.10\r\n"
            request += f"Authorization: Basic {encoded_auth}\r\n"
            request += "Accept: */*\r\n"
            request += "Connection: close\r\n"
            request += "\r\n"

            print(f"[RTK] 发送请求:")
            print(f"  挂载点: {self.mountpoint}")
            print(f"  用户名: {self.username}")

            self.socket.send(request.encode())

            response = b''
            max_header_size = 4096

            while b'\r\n\r\n' not in response:
                if len(response) > max_header_size:
                    print("[RTK] ✗ HTTP响应头过大")
                    return False

                try:
                    chunk = self.socket.recv(1024)
                    if not chunk:
                        print("[RTK] ✗ 未收到服务器响应")
                        return False
                    response += chunk
                except socket.timeout:
                    print("[RTK] ✗ 接收响应超时(15秒)")
                    return False

            response_text = response.decode('utf-8', errors='ignore')
            print(f"[RTK] 服务器响应:")
            print("=" * 60)
            print(response_text.split('\r\n\r\n')[0])
            print("=" * 60)

            if "ICY 200 OK" in response_text or "200 OK" in response_text:
                print("[RTK] ✅ NTRIP 连接成功")
                self.connected = True

                print(f"[RTK] 发送初始 GGA 请求差分数据...")
                print(f"[RTK] GGA: {self.gga.strip()}")
                self.socket.send(self.gga.encode())

                self.socket.setblocking(False)
                print(f"[RTK] Socket已切换为非阻塞模式")

                return True
            else:
                print(f"[RTK] ✗ 鉴权失败或响应异常")
                self.error = True
                return False

        except socket.timeout:
            print(f"[RTK] ✗ 连接超时(15秒)")
            return False
        except Exception as e:
            print(f"[RTK] ✗ 连接错误: {e}")
            return False

    def receive_data(self) -> Optional[bytes]:
        """接收RTCM数据(单次调用)"""
        if not self.connected or not self.socket:
            return None

        try:
            data = self.socket.recv(4096)
            if data:
                self.received_packets += 1
                self.total_bytes += len(data)

                if self.received_packets % 10 == 0:
                    print(f"[RTK] 📦 已接收 {self.received_packets} 个数据包,共 {self.total_bytes} bytes")

                return data
            else:
                print("[RTK] 🔴 连接断开: 服务器关闭连接")
                self.connected = False
                return None

        except socket.error as e:
            if e.errno == errno.EAGAIN or e.errno == errno.EWOULDBLOCK:
                return None
            else:
                print(f"[RTK] ❌ 接收数据错误: {e}")
                self.connected = False
                return None
        except Exception as e:
            print(f"[RTK] ❌ 接收循环错误: {e}")
            self.connected = False
            return None

    def receive_loop(self, callback: Callable[[bytes], None]):
        """接收循环(在独立线程中运行)"""
        print("[RTK] 开始接收 RTCM 数据...")
        last_gga_time = time.time()

        while self.running and self.connected:
            try:
                data = self.receive_data()
                if data:
                    callback(data)

                # 每30秒更新一次GGA
                current_time = time.time()
                if current_time - last_gga_time >= TIMEOUT_INTERVAL:
                    if self.gga_update_callback:
                        updated_gga = self.gga_update_callback()
                        if updated_gga != self.gga:
                            self.gga = updated_gga
                            try:
                                self.socket.send(self.gga.encode())
                                print(f"[RTK] 🔄 已更新GGA位置")
                            except Exception as e:
                                print(f"[RTK] ⚠️ 发送GGA失败: {e}")
                    last_gga_time = current_time

                time.sleep(0.01)

            except Exception as e:
                print(f"[RTK] ❌ 接收循环错误: {e}")
                self.connected = False
                self._disconnect_reason = f"接收循环错误: {e}"
                break

        print("[RTK] 接收循环结束")

    def disconnect(self):
        """断开连接"""
        self.connected = False
        self.running = False

        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

        print(f"[RTK] 接收结束,总计: {self.total_bytes} bytes, {self.received_packets} 个数据包")
        print("[RTK] 已断开连接")


class RTKModule(BaseModule):
    """RTK 定位模块 - 支持实时GPS和测试模式"""

    def __init__(self, ntrip_config: Optional[Dict[str, Any]] = None,
                 telemetry_module=None):
        super().__init__("RTKModule")

        self.ntrip_config = ntrip_config or {}
        self.ntrip_client: Optional[NTRIPClient] = None
        self.receive_thread: Optional[Thread] = None

        # ⭐ 关键：保存telemetry_module引用
        self.telemetry_module = telemetry_module

        self.lock = Lock()

        self.rtk_status = {
            'connected': False,
            'authenticated': False,
            'connection_error': None,
            'last_error': None,
            'disconnect_reason': None,
            'gps_fix_type': 0,
            'gps_fix_type_str': '未知',
            'satellites_visible': 0,
            'rtcm_packets_received': 0,
            'rtcm_packets_sent': 0,
            'last_rtcm_time': 0.0,
            'enabled': self.ntrip_config.get('enabled', False)
        }

        self.total_rtcm_bytes = 0
        self.start_time = 0.0

        # ⭐ 根据是否有telemetry_module判断模式
        if self.telemetry_module:
            self.log(f"🚁 生产模式:使用实时GPS位置（来自TelemetryModule）")
        else:
            self.log(f"🧪 测试模式:使用固定北京坐标 ({BEIJING_LAT:.4f}, {BEIJING_LON:.4f})")

    def set_telemetry_module(self, telemetry_module):
        """设置TelemetryModule引用（可选，构造函数已支持）"""
        self.telemetry_module = telemetry_module
        self.log(f"✅ 已关联TelemetryModule（将使用实时GPS位置）")

    def on_start(self):
        """模块启动"""
        super().on_start()

        if self.rtk_status['enabled'] and self.ntrip_config:
            self.start_ntrip()

    def on_stop(self):
        """模块停止"""
        self.stop_ntrip()
        super().on_stop()

    def start_ntrip(self) -> bool:
        """启动 NTRIP 客户端"""
        with self.lock:
            self.rtk_status['connection_error'] = None
            self.rtk_status['last_error'] = None
            self.rtk_status['disconnect_reason'] = None
            self.rtk_status['authenticated'] = False

        if not self.ntrip_config:
            error_msg = "NTRIP配置为空"
            self.log(error_msg, "WARNING")
            with self.lock:
                self.rtk_status['connection_error'] = error_msg
            return False

        required_keys = ['host', 'port', 'mountpoint', 'username', 'password']
        if not all(k in self.ntrip_config for k in required_keys):
            error_msg = f"NTRIP配置缺少必要字段"
            self.log(error_msg, "ERROR")
            with self.lock:
                self.rtk_status['connection_error'] = error_msg
            return False

        try:
            self.log("正在启动 NTRIP 客户端...")

            # ⭐ 关键修改：获取初始位置（实时或固定）
            position = self._get_current_position()
            initial_gga = generate_gga(
                position['lat'],
                position['lon'],
                position['alt']
            )

            self.log(f"📍 使用GPS位置: 纬度={position['lat']:.6f}, "
                     f"经度={position['lon']:.6f}, 高度={position['alt']:.1f}m")

            self.ntrip_client = NTRIPClient(
                host=self.ntrip_config['host'],
                port=self.ntrip_config['port'],
                mountpoint=self.ntrip_config['mountpoint'],
                username=self.ntrip_config['username'],
                password=self.ntrip_config['password'],
                gga=initial_gga
            )

            self.ntrip_client.set_gga_update_callback(self._get_current_gga)

            if not self.ntrip_client.connect():
                error_msg = "NTRIP连接失败"
                self.log(error_msg, "ERROR")

                with self.lock:
                    self.rtk_status['connection_error'] = error_msg
                    self.rtk_status['authenticated'] = False

                return False

            with self.lock:
                self.rtk_status['authenticated'] = True
                self.rtk_status['connection_error'] = None

            self.ntrip_client.running = True
            self.receive_thread = Thread(
                target=self.ntrip_client.receive_loop,
                args=(self._on_rtcm_data,),
                daemon=True
            )
            self.receive_thread.start()

            with self.lock:
                self.rtk_status['connected'] = True

            self.start_time = time.time()

            # ⭐ 根据模式显示不同消息
            if self.telemetry_module:
                self.log("✅ NTRIP 客户端已启动（使用实时GPS位置）")
            else:
                self.log("✅ NTRIP 客户端已启动（🧪 测试模式:固定北京坐标）")

            return True

        except Exception as e:
            error_msg = f"启动 NTRIP 失败: {e}"
            self.log(error_msg, "ERROR")

            with self.lock:
                self.rtk_status['connection_error'] = error_msg
                self.rtk_status['last_error'] = str(e)

            return False

    def _get_current_position(self) -> Dict[str, float]:
        """
        ⭐ 获取当前位置（核心方法）

        优先级:
        1. 如果有telemetry_module，使用实时GPS位置
        2. 否则使用固定北京坐标（测试模式）
        """
        if self.telemetry_module:
            try:
                telemetry_data = self.telemetry_module.get_telemetry_data()

                # ⭐ 验证GPS数据有效性
                lat = telemetry_data.get('latitude', 0.0)
                lon = telemetry_data.get('longitude', 0.0)
                alt = telemetry_data.get('altitude', 0.0)

                # 如果GPS数据无效（全0或异常），回退到固定坐标
                if abs(lat) < 0.0001 and abs(lon) < 0.0001:
                    self.log("⚠️ GPS数据无效，使用固定北京坐标", "WARNING")
                    return {
                        'lat': BEIJING_LAT,
                        'lon': BEIJING_LON,
                        'alt': BEIJING_ALT
                    }

                return {
                    'lat': lat,
                    'lon': lon,
                    'alt': alt
                }
            except Exception as e:
                self.log(f"⚠️ 获取实时GPS失败: {e}，使用固定北京坐标", "WARNING")
                return {
                    'lat': BEIJING_LAT,
                    'lon': BEIJING_LON,
                    'alt': BEIJING_ALT
                }
        else:
            # 测试模式：固定北京坐标
            return {
                'lat': BEIJING_LAT,
                'lon': BEIJING_LON,
                'alt': BEIJING_ALT
            }

    def _get_current_gga(self) -> str:
        """⭐ 生成当前位置的GGA语句"""
        position = self._get_current_position()

        with self.lock:
            gga = generate_gga(
                position['lat'],
                position['lon'],
                position['alt'],
                fix_quality=min(self.rtk_status['gps_fix_type'], 5),
                num_sats=self.rtk_status['satellites_visible']
            )

        return gga

    def stop_ntrip(self, reason: str = "用户手动停止"):
        """停止 NTRIP 客户端"""
        if self.ntrip_client:
            self.ntrip_client.running = False
            self.ntrip_client.disconnect()

            if self.receive_thread:
                self.receive_thread.join(timeout=2)

            with self.lock:
                self.rtk_status['connected'] = False
                self.rtk_status['authenticated'] = False
                self.rtk_status['disconnect_reason'] = reason

            self.log(f"NTRIP 客户端已停止: {reason}")

    def _on_rtcm_data(self, data: bytes):
        """接收到 RTCM 数据的回调"""
        with self.lock:
            self.total_rtcm_bytes += len(data)
            self.rtk_status['rtcm_packets_received'] += 1
            self.rtk_status['last_rtcm_time'] = time.time()

        # ⭐ 添加日志：确认回调被触发
        if self.rtk_status['rtcm_packets_received'] % 10 == 0:
            self.log(f"📥 收到RTCM数据包 #{self.rtk_status['rtcm_packets_received']} ({len(data)} bytes)", "DEBUG")

        # 注入到飞控
        self._inject_rtcm_to_mavlink(data)

    def _inject_rtcm_to_mavlink(self, data: bytes):
        """通过 MAVLink 注入 RTCM 数据到飞控（修复版）"""

        if not self.dispatcher or not self.dispatcher.connection:
            self.log("❌ [注入失败] dispatcher或connection为None", "ERROR")
            return

        if not self.dispatcher.connection.is_connected():
            self.log("❌ [注入失败] MAVLink未连接", "ERROR")
            return

        try:
            master = self.dispatcher.connection.master

            # ⭐ 修复1: 使用正确的目标ID
            target_system = master.target_system if master.target_system > 0 else 1
            target_component = master.target_component if master.target_component > 0 else 0

            # ⭐ 修复2: 使用110字节分片（ArduPilot标准）
            chunk_size = 110
            injected_count = 0

            for i in range(0, len(data), chunk_size):
                chunk = data[i:i + chunk_size]
                chunk_len = len(chunk)

                # ⭐ 修复3: 填充到110字节
                padded_data = list(chunk) + [0] * (chunk_size - chunk_len)

                # ⭐ 修复4: 优先使用 GPS_INJECT_DATA（ArduPilot标准）
                try:
                    msg = master.mav.gps_inject_data_encode(
                        target_system,
                        target_component,
                        chunk_len,
                        padded_data
                    )

                    self.dispatcher.connection.send_message(msg)
                    injected_count += 1

                    with self.lock:
                        self.rtk_status['rtcm_packets_sent'] += 1

                    # ⭐ 修复5: 减少延迟（ArduPilot可处理更快速率）
                    time.sleep(0.005)  # 5ms而不是10ms

                except AttributeError:
                    # 如果GPS_INJECT_DATA不存在，尝试GPS_RTCM_DATA
                    self.log("⚠️ GPS_INJECT_DATA不支持，回退到GPS_RTCM_DATA", "WARNING")

                    # 使用180字节分片（GPS_RTCM_DATA的最大值）
                    chunk_180 = data[i:i + 180]
                    padded_180 = list(chunk_180) + [0] * (180 - len(chunk_180))

                    msg = master.mav.gps_rtcm_data_encode(
                        flags=1,
                        len=len(chunk_180),
                        data=padded_180
                    )

                    self.dispatcher.connection.send_message(msg)
                    injected_count += 1

                    with self.lock:
                        self.rtk_status['rtcm_packets_sent'] += 1

                    time.sleep(0.005)

            # 每100个包打印统计
            if self.rtk_status['rtcm_packets_sent'] % 100 == 0:
                self.log(f"📤 已注入 {self.rtk_status['rtcm_packets_sent']} 个RTCM包 "
                         f"(目标: {target_system}/{target_component}, "
                         f"本次: {len(data)}B → {injected_count}个MAVLink包)", "INFO")

        except Exception as e:
            self.log(f"❌ 注入RTCM失败: {e}", "ERROR")
            import traceback
            self.log(f"堆栈: {traceback.format_exc()}", "ERROR")

    def on_message(self, message: MAVLinkMessage):
        """处理 GPS 和 RTK 相关消息"""
        msg = message.msg
        msg_type = message.msg_type

        if msg_type == "GPS_RAW_INT":
            with self.lock:
                old_fix = self.rtk_status['gps_fix_type']
                self.rtk_status['gps_fix_type'] = msg.fix_type
                self.rtk_status['satellites_visible'] = msg.satellites_visible

                if old_fix != msg.fix_type:
                    self.log(f"🛰️ GPS定位类型变化: {old_fix} → {msg.fix_type} "
                             f"({self._get_fix_type_name(msg.fix_type)})", "INFO")

        elif msg_type == "GPS2_RAW":
            with self.lock:
                self.rtk_status['gps_fix_type'] = msg.fix_type
                self.rtk_status['satellites_visible'] = msg.satellites_visible

        elif msg_type == "GPS_RTK":
            self.log(f"📡 RTK状态: baseline={msg.baseline_coords_type}, "
                     f"rtk_rate={msg.rtk_rate}, nsats={msg.nsats}, "
                     f"accuracy={msg.accuracy}", "DEBUG")

        elif msg_type == "GPS2_RTK":
            self.log(f"📡 RTK2状态: baseline={msg.baseline_coords_type}, "
                     f"rtk_rate={msg.rtk_rate}, nsats={msg.nsats}", "DEBUG")

        elif msg_type == "GPS_INJECT_STATUS":
            self.log(f"💉 GPS注入状态: received={msg.received_count}, "
                     f"processed={msg.processed_count}, "
                     f"⚠️ 未处理={msg.received_count - msg.processed_count}",
                     "WARNING" if msg.received_count != msg.processed_count else "INFO")

    def _get_fix_type_name(self, fix_type: int) -> str:
        """获取定位类型名称"""
        fix_type_map = {
            0: "无定位",
            1: "无定位",
            2: "2D定位",
            3: "3D定位",
            4: "DGPS",
            5: "RTK Float",
            6: "RTK Fixed"
        }
        return fix_type_map.get(fix_type, f"未知({fix_type})")

    def diagnose_rtk(self) -> Dict[str, Any]:
        """RTK诊断"""
        status = self.get_rtk_status()

        diagnostics = {
            "timestamp": time.time(),
            "ntrip_connected": status['connected'],
            "rtcm_flow": {
                "receiving": status['rtcm_packets_received'] > 0,
                "injecting": status['rtcm_packets_sent'] > 0,
                "received_packets": status['rtcm_packets_received'],
                "sent_packets": status['rtcm_packets_sent'],
                "total_bytes": status.get('total_rtcm_bytes', 0),
                "data_rate_bps": status.get('rtcm_rate_bps', 0)
            },
            "gps_status": {
                "fix_type": status['gps_fix_type'],
                "fix_type_str": status['gps_fix_type_str'],
                "satellites": status['satellites_visible'],
                "is_rtk": status['gps_fix_type'] in [5, 6]
            },
            "mavlink_status": {
                "connected": self.dispatcher is not None and self.dispatcher.connection.is_connected(),
                "target_system": self.dispatcher.connection.master.target_system if self.dispatcher else None,
                "target_component": self.dispatcher.connection.master.target_component if self.dispatcher else None
            },
            "possible_issues": []
        }

        if not status['connected']:
            diagnostics['possible_issues'].append({
                "level": "ERROR",
                "message": "NTRIP未连接",
                "suggestion": "检查网络和账号配置"
            })

        if status['rtcm_packets_received'] == 0:
            diagnostics['possible_issues'].append({
                "level": "ERROR",
                "message": "未接收到RTCM数据",
                "suggestion": "检查挂载点和GGA坐标"
            })

        if status['rtcm_packets_sent'] == 0 and status['rtcm_packets_received'] > 0:
            diagnostics['possible_issues'].append({
                "level": "ERROR",
                "message": "RTCM数据未发送到飞控",
                "suggestion": "检查MAVLink连接和GPS参数配置"
            })

        if status['rtcm_packets_sent'] > 0 and status['gps_fix_type'] < 5:
            diagnostics['possible_issues'].append({
                "level": "WARNING",
                "message": f"RTCM已注入但GPS仍为{status['gps_fix_type_str']}",
                "suggestion": "检查: 1)GPS模块是否支持RTK 2)基站距离是否过远 3)飞控GPS参数"
            })

        if status['satellites_visible'] < 8:
            diagnostics['possible_issues'].append({
                "level": "WARNING",
                "message": f"卫星数量不足({status['satellites_visible']})",
                "suggestion": "移动到开阔地带，等待更多卫星"
            })

        return diagnostics

    def get_rtk_status(self) -> Dict[str, Any]:
        """获取 RTK 状态（增强版）"""
        with self.lock:
            status = self.rtk_status.copy()

        # 当前位置
        status['current_position'] = self._get_current_position()

        # 运行时间和速率
        if self.start_time > 0:
            runtime = time.time() - self.start_time
            status['runtime_seconds'] = runtime
            status['rtcm_rate_bps'] = self.total_rtcm_bytes * 8 / runtime if runtime > 0 else 0
        else:
            status['runtime_seconds'] = 0
            status['rtcm_rate_bps'] = 0

        status['total_rtcm_bytes'] = self.total_rtcm_bytes

        # GPS定位类型
        fix_type_map = {
            0: "无定位",
            1: "无定位",
            2: "2D定位",
            3: "3D定位",
            4: "DGPS",
            5: "RTK Float",
            6: "RTK Fixed"
        }
        status['gps_fix_type_str'] = fix_type_map.get(status['gps_fix_type'], "未知")

        # MAVLink连接状态
        status['mavlink_connected'] = (
            self.dispatcher is not None and
            self.dispatcher.connection.is_connected()
        )

        # 数据流健康度
        if status['rtcm_packets_received'] > 0:
            status['injection_rate'] = (
                status['rtcm_packets_sent'] / status['rtcm_packets_received']
            )
        else:
            status['injection_rate'] = 0.0

        return status

    def set_config(self, config: Dict[str, Any]) -> bool:
        """
        更新 NTRIP 配置

        Args:
            config: 新的配置字典

        Returns:
            是否成功
        """
        if self.rtk_status['connected']:
            self.stop_ntrip("配置更新")

        self.ntrip_config.update(config)
        self.rtk_status['enabled'] = config.get('enabled', False)

        if self.rtk_status['enabled']:
            return self.start_ntrip()

        return True

    def get_config(self) -> Dict[str, Any]:
        """获取当前NTRIP配置"""
        return self.ntrip_config.copy()

    def idle_task(self, delta_time: float):
        """
        空闲任务:监控连接状态

        Args:
            delta_time: 距离上次调用的时间间隔(秒)
        """
        if self.ntrip_client:
            if not self.ntrip_client.connected:
                with self.lock:
                    if self.rtk_status['connected']:
                        disconnect_reason = getattr(self.ntrip_client, '_disconnect_reason', '连接异常断开')
                        self.rtk_status['connected'] = False
                        self.rtk_status['authenticated'] = False
                        self.rtk_status['connection_error'] = disconnect_reason
                        self.rtk_status['disconnect_reason'] = disconnect_reason
                        self.log(f"⚠️ RTK异常断开: {disconnect_reason}", "WARNING")


# ==================== 工具函数 ====================

def get_ntrip_sourcetable(host: str, port: int, username: str, password: str) -> Optional[str]:
    """
    获取NTRIP服务器的挂载点列表(Sourcetable)

    Args:
        host: NTRIP服务器地址
        port: 端口
        username: 用户名
        password: 密码

    Returns:
        Sourcetable字符串,失败返回None
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)

        print(f"[Sourcetable] 连接到 {host}:{port}...")
        sock.connect((host, port))

        auth = f"{username}:{password}"
        encoded_auth = base64.b64encode(auth.encode()).decode()

        request = f"GET / HTTP/1.1\r\n"
        request += f"Host: {host}\r\n"
        request += "User-Agent: NTRIP GNSSInternetRadio/1.4.10\r\n"
        request += f"Authorization: Basic {encoded_auth}\r\n"
        request += "Accept: */*\r\n"
        request += "Connection: close\r\n"
        request += "\r\n"

        sock.send(request.encode())

        response = sock.recv(16384).decode('utf-8', errors='ignore')
        sock.close()

        print("=" * 80)
        print("🗺️  NTRIP Sourcetable(挂载点列表):")
        print("=" * 80)
        print(response)
        print("=" * 80)

        return response
    except Exception as e:
        print(f"❌ 获取Sourcetable失败: {e}")
        return None


# ==================== 测试代码 ====================

if __name__ == "__main__":
    """
    单独运行此文件进行NTRIP连接测试

    使用方法:
        python modules/rtk_module.py
    """
    print("=" * 80)
    print("🧪 RTK模块独立测试")
    print("=" * 80)

    test_config = {
        'enabled': True,
        'host': 'gnss.ytcors.cn',
        'port': 8002,
        'mountpoint': 'RTCM33GRCEJpro',
        'username': 'ytcors42231',
        'password': 'fyx73537'
    }

    print("\n当前测试配置:")
    print(f"  服务器: {test_config['host']}:{test_config['port']}")
    print(f"  挂载点: {test_config['mountpoint']}")
    print(f"  用户名: {test_config['username']}")
    print(f"  坐标: 北京 ({BEIJING_LAT:.4f}, {BEIJING_LON:.4f})")
    print()

    # 选项1:获取Sourcetable
    print("📋 选项1:获取挂载点列表")
    print("-" * 80)
    choice = input("是否获取Sourcetable?(y/n): ").lower().strip()
    if choice == 'y':
        get_ntrip_sourcetable(
            test_config['host'],
            test_config['port'],
            test_config['username'],
            test_config['password']
        )
        print()

    # 选项2:测试连接
    print("📡 选项2:测试NTRIP连接")
    print("-" * 80)
    choice = input("是否测试连接并接收数据?(y/n): ").lower().strip()
    if choice == 'y':
        client = NTRIPClient(
            host=test_config['host'],
            port=test_config['port'],
            mountpoint=test_config['mountpoint'],
            username=test_config['username'],
            password=test_config['password'],
            gga=DEFAULT_GGA
        )

        if client.connect():
            print("\n✅ 连接成功,开始接收数据(按Ctrl+C停止)...\n")
            client.running = True

            def test_callback(data):
                pass  # 数据统计已在 receive_data() 中完成

            try:
                client.receive_loop(test_callback)
            except KeyboardInterrupt:
                print("\n\n⏹️  用户中断")
            finally:
                client.running = False
                client.disconnect()
        else:
            print("\n❌ 连接失败")

    print("\n" + "=" * 80)
    print("测试结束")
    print("=" * 80)

