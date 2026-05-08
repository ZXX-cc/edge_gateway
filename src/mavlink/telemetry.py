"""
遥测数据处理模块
将MAVLink遥测数据转换为OSD/State格式
"""
import time
import math
import threading
from typing import Dict, Callable, Optional
from src.utils.logger import get_logger
from src.utils.config import Config

logger = get_logger("telemetry")


class TelemetryProcessor:
    """
    遥测数据处理器

    职责:
    1. 收集MAVLink遥测数据(GPS、姿态、电量等)
    2. 转换为OSD格式(定频推送, 0.5Hz)
    3. 检测状态变更并触发state推送
    4. 数据平滑和滤波
    """

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()

        # 最新遥测缓存
        self._latest: Dict[str, any] = {
            'position': None,
            'attitude': None,
            'battery': None,
            'gps': None,
            'heartbeat': None,
            'vfr_hud': None,
            'wind': None,
        }

        # 状态缓存(用于变更检测)
        self._last_state: Dict[str, any] = {}
        self._last_osd_time = 0
        self._osd_interval = 2.0  # OSD推送间隔: 2秒 = 0.5Hz (DJI规范)

        # 回调
        self._osd_callback: Optional[Callable] = None
        self._state_callback: Optional[Callable] = None

        # 定时推送线程
        self._osd_thread: Optional[threading.Thread] = None
        self._running = False

    def set_callbacks(self, osd_cb: Callable, state_cb: Callable):
        """设置数据推送回调"""
        self._osd_callback = osd_cb
        self._state_callback = state_cb

    def start(self):
        """启动OSD定时推送线程"""
        if self._running:
            return
        self._running = True
        self._osd_thread = threading.Thread(target=self._osd_loop, daemon=True)
        self._osd_thread.start()
        logger.info("Telemetry OSD loop started (0.5Hz)")

    def stop(self):
        """停止OSD定时推送线程"""
        self._running = False
        if self._osd_thread:
            self._osd_thread.join(timeout=2.0)
            self._osd_thread = None
        logger.info("Telemetry OSD loop stopped")

    def _osd_loop(self):
        """OSD定时推送循环"""
        while self._running:
            try:
                osd_data = self.build_osd_data()
                if self._osd_callback and osd_data:
                    self._osd_callback(osd_data)
            except Exception as e:
                logger.error(f"OSD push error: {e}")
            time.sleep(self._osd_interval)

    # ---- MAVLink消息处理器 ----

    def on_position(self, msg):
        """处理GLOBAL_POSITION_INT"""
        self._latest['position'] = {
            'lat': msg.lat / 1e7,      # 度
            'lon': msg.lon / 1e7,
            'alt': msg.relative_alt / 1000.0,  # 相对高度(m)
            'abs_alt': msg.alt / 1000.0,       # 绝对高度(m)
            'vx': msg.vx / 100.0,      # 速度(m/s)
            'vy': msg.vy / 100.0,
            'vz': msg.vz / 100.0,
            'hdg': msg.hdg / 100.0 if msg.hdg != 0 else 0,  # 航向(度)
        }
        self._check_osd_push()

    def on_attitude(self, msg):
        """处理ATTITUDE"""
        self._latest['attitude'] = {
            'roll': math.degrees(msg.roll),
            'pitch': math.degrees(msg.pitch),
            'yaw': math.degrees(msg.yaw),
            'roll_speed': msg.rollspeed,
            'pitch_speed': msg.pitchspeed,
            'yaw_speed': msg.yawspeed,
        }

    def on_battery(self, msg):
        """处理SYS_STATUS或BATTERY_STATUS"""
        if msg.get_type() == 'SYS_STATUS':
            battery_remaining = msg.battery_remaining
            voltage = msg.voltage_battery / 1000.0  # mV -> V
            current = msg.current_battery / 100.0    # cA -> A
            self._latest['battery'] = {
                'remaining': battery_remaining,
                'voltage': voltage,
                'current': current,
            }
        elif msg.get_type() == 'BATTERY_STATUS':
            self._latest['battery'] = {
                'remaining': msg.battery_remaining,
                'voltage': sum(msg.voltages[:10]) / 1000.0,
                'temperature': msg.temperature / 100.0 if msg.temperature != 0 else 0,
            }

    def on_gps(self, msg):
        """处理GPS_RAW_INT"""
        self._latest['gps'] = {
            'fix_type': msg.fix_type,           # 0-1:无 2:2D 3:3D
            'satellites_visible': msg.satellites_visible,
            'eph': msg.eph,                      # 水平精度
            'epv': msg.epv,                      # 垂直精度
        }

    def on_heartbeat(self, msg):
        """处理HEARTBEAT"""
        base_mode = msg.base_mode
        custom_mode = msg.custom_mode
        system_status = msg.system_status

        # 解析飞行模式
        flight_mode = self._parse_flight_mode(custom_mode)

        # 解析上锁状态
        armed = bool(base_mode & 0x80) if base_mode else False

        self._latest['heartbeat'] = {
            'flight_mode': flight_mode,
            'armed': armed,
            'system_status': system_status,
        }

        # 检测状态变更
        self._check_state_change('heartbeat', {
            'flight_mode': flight_mode,
            'armed': armed,
        })

    def on_vfr_hud(self, msg):
        """处理VFR_HUD"""
        self._latest['vfr_hud'] = {
            'airspeed': msg.airspeed,
            'groundspeed': msg.groundspeed,
            'heading': msg.heading,
            'throttle': msg.throttle,
            'alt': msg.alt,
            'climb': msg.climb,
        }

    def on_wind(self, msg):
        """处理WIND"""
        self._latest['wind'] = {
            'speed': msg.speed,
            'direction': msg.direction,
            'speed_z': msg.speed_z,
        }

    # ---- 数据转换 ----

    def build_osd_data(self) -> dict:
        """构建OSD定频数据（对齐DJI Cloud API规范）"""
        osd = {}

        # 位置
        pos = self._latest.get('position')
        if pos:
            osd['latitude'] = pos['lat']
            osd['longitude'] = pos['lon']
            osd['height'] = pos['alt']
            osd['altitude'] = pos.get('abs_alt', pos['alt'])
            osd['horizontal_speed'] = math.sqrt(pos['vx']**2 + pos['vy']**2)
            osd['vertical_speed'] = -pos['vz']  # NED坐标系，向上为正

        # 姿态
        att = self._latest.get('attitude')
        if att:
            osd['attitude_pitch'] = att['pitch']
            osd['attitude_roll'] = att['roll']
            osd['attitude_head'] = att.get('yaw', 0)

        # 电量
        bat = self._latest.get('battery')
        if bat:
            osd['battery'] = {
                'capacity_percent': bat.get('remaining', 0),
                'voltage': int(bat.get('voltage', 0) * 1000),  # mV
            }
            if 'temperature' in bat:
                osd['battery']['temperature'] = bat['temperature']
            if 'current' in bat:
                osd['battery']['current'] = int(bat.get('current', 0) * 1000)  # mA

        # GPS/定位状态
        gps = self._latest.get('gps')
        hb = self._latest.get('heartbeat')
        position_state = {}
        if gps:
            position_state['gps_number'] = gps.get('satellites_visible', 0)
            position_state['is_fixed'] = 1 if gps.get('fix_type', 0) >= 3 else 0
        if position_state:
            osd['position_state'] = position_state

        # 风速
        wind = self._latest.get('wind')
        if wind:
            osd['wind_speed'] = wind.get('speed', 0)
            osd['wind_direction'] = wind.get('direction', 0)

        # 飞行模式
        if hb:
            osd['mode_code'] = self._mode_to_code(hb.get('flight_mode', 'UNKNOWN'))
            osd['gear'] = 1 if hb.get('armed', False) else 0

        # 航向（如果没有attitude数据，从position中获取）
        if pos and 'attitude_head' not in osd:
            osd['attitude_head'] = pos.get('hdg', 0)

        return osd

    @staticmethod
    def _mode_to_code(mode: str) -> int:
        """将飞行模式字符串转换为DJI mode_code"""
        mode_codes = {
            'STABILIZE': 0, 'ACRO': 1, 'ALT_HOLD': 2, 'AUTO': 3,
            'GUIDED': 4, 'LOITER': 5, 'RTL': 6, 'CIRCLE': 7,
            'LAND': 9, 'DRIFT': 11, 'SPORT': 13, 'FLIP': 14,
            'AUTOTUNE': 15, 'POSHOLD': 16, 'BRAKE': 17,
            'THROW': 18, 'AVOID_ADSB': 19, 'GUIDED_NOGPS': 20,
            'SMART_RTL': 21, 'FLOWHOLD': 22, 'FOLLOW': 23,
            'ZIGZAG': 24, 'SYSTEMID': 25, 'AUTOROTATE': 26,
            'AUTO_RTL': 27,
        }
        return mode_codes.get(mode.upper(), 0)

    def build_state_data(self, changed_fields: dict) -> dict:
        """构建状态变更数据"""
        return {
            'gateway_sn': self.gateway_sn,
            'timestamp': int(time.time() * 1000),
            'changes': changed_fields
        }

    # ---- 内部方法 ----

    def _check_osd_push(self):
        """检查是否需要推送OSD"""
        now = time.time()
        if now - self._last_osd_time >= self._osd_interval:
            self._last_osd_time = now
            osd_data = self.build_osd_data()
            if self._osd_callback:
                self._osd_callback(osd_data)

    def _check_state_change(self, category: str, current: dict):
        """检测状态变更"""
        last = self._last_state.get(category)
        if last != current:
            self._last_state[category] = current.copy()
            if self._state_callback:
                self._state_callback(self.build_state_data({category: current}))

    @staticmethod
    def _parse_flight_mode(custom_mode: int) -> str:
        """解析MAVLink飞行模式为字符串"""
        # ArduPilot模式映射(根据实际飞控调整)
        mode_map = {
            0: 'STABILIZE', 1: 'ACRO', 2: 'ALT_HOLD', 3: 'AUTO',
            4: 'GUIDED', 5: 'LOITER', 6: 'RTL', 7: 'CIRCLE',
            9: 'LAND', 11: 'DRIFT', 13: 'SPORT', 14: 'FLIP',
            15: 'AUTOTUNE', 16: 'POSHOLD', 17: 'BRAKE',
            18: 'THROW', 19: 'AVOID_ADSB', 20: 'GUIDED_NOGPS',
            21: 'SMART_RTL', 22: 'FLOWHOLD', 23: 'FOLLOW',
            24: 'ZIGZAG', 25: 'SYSTEMID', 26: 'AUTOROTATE',
            27: 'AUTO_RTL',
        }
        return mode_map.get(custom_mode, f'UNKNOWN({custom_mode})')
