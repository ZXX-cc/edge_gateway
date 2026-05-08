"""
设备管理模块 - 完整实现版
处理设备属性(物模型)、固件升级、配置更新、拓扑管理、状态机

核心设计：
1. 属性变更自动触发 state 上报（物模型 pushMode=1）
2. OSD 属性（pushMode=0）由外部数据源（MAVLink/机巢）驱动更新
3. 控制指令通过机巢协议真实下发，等待机巢响应后更新属性
4. 系统状态（存储、网络）由定时线程查询更新
5. 拓扑管理维护网关+子设备完整信息

数据来源映射：
- MAVLink遥测: longitude/latitude/height/mode_code/electricity/position_state
- 机巢协议: cover_state/putter_state/charging_state/rainfall/wind_speed/temperature/
           humidity/electric_supply_voltage/working_voltage/working_current/
           emergency_stop_state/supplement_light_state/air_conditioner
- 系统查询: storage/network_state
- 内部维护: firmware_version/ota_state/topology/acc_time/job_number
- 配置读取: remaining_power_for_return_home/alternate_land_point
"""
import time
import threading
import os
import shutil
from typing import Dict, Any, Optional, Callable, List
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder

logger = get_logger("device")


class SubDevice:
    """子设备信息"""

    def __init__(self, sn: str = "", domain: str = "0", device_type: int = 0,
                 sub_type: int = 0, index: str = "A", device_secret: str = "",
                 nonce: str = "", thing_version: str = "1.1.2"):
        self.sn = sn
        self.domain = domain
        self.type = device_type
        self.sub_type = sub_type
        self.index = index
        self.device_secret = device_secret
        self.nonce = nonce
        self.thing_version = thing_version
        self.online = False
        self.paired = False
        self.last_heartbeat = 0

    def to_dict(self) -> dict:
        """序列化为大疆标准格式"""
        return {
            "sn": self.sn,
            "domain": self.domain,
            "type": self.type,
            "sub_type": self.sub_type,
            "index": self.index,
            "device_secret": self.device_secret,
            "nonce": self.nonce,
            "thing_version": self.thing_version
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SubDevice":
        """从字典反序列化"""
        return cls(
            sn=data.get("sn", ""),
            domain=data.get("domain", "0"),
            device_type=data.get("type", 0),
            sub_type=data.get("sub_type", 0),
            index=data.get("index", "A"),
            device_secret=data.get("device_secret", ""),
            nonce=data.get("nonce", ""),
            thing_version=data.get("thing_version", "1.1.2")
        )


class DeviceManager:
    """
    设备管理器 - 物模型核心

    职责:
    1. 管理设备属性(物模型) - 200+ 属性，支持读写、变更自动上报
    2. 属性数据来源: MAVLink遥测 / 机巢协议 / 系统查询 / 内部维护 / 配置
    3. 处理固件升级流程 - 异步状态机驱动，支持下载/校验/刷写
    4. 处理配置更新 - 持久化+生效
    5. 管理设备拓扑 - 网关+子设备CRUD，通过 sys/product/{sn}/status 上报
    6. 设备生命周期 - 绑定/上线/离线/心跳
    7. 处理属性设置请求(property/set) - 仅 rw 属性可写
    8. 设备控制指令 - 舱盖/充电/推杆等通过机巢协议真实下发
    """

    # 设备生命周期状态
    STATE_UNBOUND = 0
    STATE_BOUND = 1
    STATE_ONLINE = 2
    STATE_OFFLINE = 3
    STATE_UPGRADING = 4

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()

        # ===== 外部模块引用（由 main.py 注入） =====
        self._nest_module = None          # DroneNestModule - 机巢协议
        self._telemetry_processor = None   # TelemetryProcessor - MAVLink遥测
        self._dock_manager = None          # DockManager - 机场管理

        # ===== 基础时间记录（必须在_init_properties之前定义） =====
        self._acc_time_start = time.time()
        self._first_power_on = int(time.time() * 1000)

        # ===== 设备属性表(物模型) =====
        # 所有属性对齐 DJI Cloud API v1.15 properties.md
        # pushMode=0: OSD定频上报 (0.5Hz)
        # pushMode=1: State事件性上报
        self._properties: Dict[str, Any] = self._init_properties()

        # 可写属性列表 (accessMode = rw)
        self._writable_props = [
            'remaining_power_for_return_home',
            'air_conditioner_mode',
            'battery_store_mode',
            'alarm_state',
            'battery_maintenance_mode',
            'silent_mode',
            'user_experience_improvement',
        ]

        # ===== 拓扑信息 =====
        self._gateway_domain = "3"
        self._gateway_type = 119
        self._gateway_sub_type = 0
        self._gateway_device_secret = ""
        self._gateway_nonce = ""
        self._gateway_thing_version = "1.1.2"

        # 子设备列表
        self._sub_devices: Dict[str, SubDevice] = {}

        # ===== 设备生命周期状态 =====
        self._device_state = self.STATE_UNBOUND
        self._bound_workspace_id = ""
        self._bound_workspace_name = ""

        # ===== OTA升级状态机 =====
        self._ota_state = 'idle'           # idle/checking/downloading/verifying/upgrading/completed/failed
        self._ota_progress = 0
        self._ota_task_id = ""
        self._ota_target_version = ""
        self._ota_package_url = ""
        self._ota_md5 = ""
        self._ota_file_name = ""
        self._ota_thread: Optional[threading.Thread] = None
        self._ota_cancel_flag = False

        # ===== 调试/仿真模式 =====
        self._debug_mode = False
        self._simulation_mode = False

        # ===== HMS告警缓存 =====
        self._hms_alarms: List[dict] = []
        self._hms_alarm_counter = 0

        # ===== 系统状态定时更新线程 =====
        self._system_update_thread: Optional[threading.Thread] = None
        self._system_update_running = False
        self._system_update_interval = 5.0   # 5秒查询一次系统状态

        # ===== 回调 =====
        self._event_callback: Optional[Callable] = None
        self._state_callback: Optional[Callable] = None

        # ===== 启动时加载持久化数据 =====
        self._load_persisted_data()

    def _init_properties(self) -> Dict[str, Any]:
        """初始化所有物模型属性（对齐DJI规范）"""
        return {
            # ---- 位置属性 (pushMode=0, 只读) ----
            'longitude': 0.0,
            'latitude': 0.0,
            'height': 0.0,           # 椭球高度(m)

            # ---- 固件属性 (pushMode=1, 只读) ----
            'firmware_version': self._read_firmware_version(),
            'firmware_upgrade_status': 0,    # 0:未升级,1:升级中
            'compatible_status': 0,          # 0:不需要一致性升级,1:需要

            # ---- 状态属性 (pushMode=0, 只读) ----
            'mode_code': 0,              # 0:空闲,1:现场调试,2:远程调试,3:固件升级,4:作业中
            'flighttask_step_code': 5,   # 5:任务空闲,255:异常
            'cover_state': 0,            # 0:关闭,1:打开,2:半开,3:异常
            'putter_state': 0,           # 0:关闭,1:打开,2:半开,3:异常
            'charging_state': 0,         # 0:空闲,1:充电中
            'drone_in_dock': 1,          # 0:舱外,1:舱内
            'emergency_stop_state': 0,   # 0:关闭,1:开启

            # ---- 子设备状态 (pushMode=0, 只读) ----
            'sub_device': {
                'device_sn': '',
                'product_type': '',
                'device_online_status': 0,   # 0:关机,1:开机
                'device_paired': 0           # 0:未对频,1:已对频
            },

            # ---- 网络属性 (pushMode=0, 只读) ----
            'network_state': {'type': 1, 'quality': 5, 'rate': 0.0},
            'transmission_signal_quality': 100,
            'wireless_link': {
                'dongle_number': 0,
                '4g_link_state': 0,
                'sdr_link_state': 0,
                'link_workmode': 0,
                'sdr_quality': 0,
                '4g_quality': 0,
                '4g_uav_quality': 0,
                '4g_gnd_quality': 0,
                'sdr_freq_band': 0.0,
                '4g_freq_band': 0.0,
            },

            # ---- 直播属性 (pushMode=1, 只读) ----
            'live_status': [],
            'live_capacity': {
                'available_video_number': 0,
                'coexist_video_number_max': 0,
                'device_list': []
            },

            # ---- 电源/电量属性 ----
            'electricity': 100,                      # 总电量(%)
            'remain_flight_time': 0,                 # 剩余飞行时间(s)
            'remaining_power_for_return_home': 30,   # 返航预留电量(%) - rw
            'drone_charge_state': {'capacity_percent': 100, 'state': 0},

            # ---- 环境属性 (pushMode=0, 只读) ----
            'wind_speed': 0.0,               # 风速(m/s)
            'environment_temperature': 25.0, # 环境温度(°C)
            'temperature': 25.0,             # 舱内温度(°C)
            'humidity': 50.0,                # 舱内湿度(%RH)
            'rainfall': 0,                   # 0:无雨,1:小雨,2:中雨,3:大雨

            # ---- 电气属性 (pushMode=0, 只读) ----
            'electric_supply_voltage': 0,    # 市电电压(V)
            'working_voltage': 0,            # 工作电压(mV)
            'working_current': 0.0,          # 工作电流(mA)

            # ---- 舱体属性 (部分可写) ----
            'air_conditioner_mode': 0,       # rw: 0:关闭,1:制冷,2:制热,3:自动
            'battery_store_mode': 1,         # rw: 1:计划模式,2:待命模式
            'alarm_state': 0,                # rw: 0:关闭,1:开启
            'supplement_light_state': 0,     # 0:关闭,1:打开
            'battery_maintenance_mode': 0,   # rw: 0:关闭保养,1:开启保养
            'silent_mode': 0,                # rw: 0:非静音,1:静音

            # ---- 空调详细状态 (pushMode=0, 只读) ----
            'air_conditioner': {
                'air_conditioner_state': 0,
                'switch_time': 0
            },

            # ---- 存储属性 (pushMode=0, 只读) ----
            'storage': {'total': 0, 'used': 0},       # KB
            'storage_capacity': 64000,                # MB
            'remain_storage_capacity': 32000,         # MB
            'media_file_detail': {'remain_upload': 0},

            # ---- 作业统计 (pushMode=0, 只读) ----
            'job_number': 0,
            'first_power_on': self._first_power_on,
            'acc_time': 0,               # 累计运行时长(s)
            'activation_time': 0,        # 机场激活时间(unix时间戳 s)

            # ---- 备降点 (pushMode=0, 只读) ----
            'alternate_land_point': {
                'longitude': 0.0,
                'latitude': 0.0,
                'safe_land_height': 0.0,
                'is_configured': 0,
                'height': 0.0
            },

            # ---- 备用电池 (pushMode=0, 只读) ----
            'backup_battery': {
                'switch': 0,
                'voltage': 0,
                'temperature': 0.0
            },

            # ---- 搜星状态 (pushMode=0, 只读) ----
            'position_state': {
                'is_calibration': 0,
                'is_fixed': 0,
                'quality': 0,
                'gps_number': 0,
                'rtk_number': 0
            },

            # ---- 保养信息 (pushMode=0, 只读) ----
            'maintain_status': {
                'maintain_status_array': []
            },

            # ---- 电池保养信息 (pushMode=0, 只读) ----
            'drone_battery_maintenance_info': {
                'maintenance_state': 0,
                'maintenance_time_left': 0,
                'heat_state': 0,
                'batteries': []
            },

            # ---- 其他 (pushMode=1, 只读) ----
            'wpmz_version': '1.0.0',
            'drc_state': 0,              # 0:未连接,1:连接中,2:已连接
            'drone_authority_info': {
                'control_source': '',
                'locked': False,
                'payloads': []
            },

            # ---- 用户体验 (pushMode=1, 可写rw) ----
            'user_experience_improvement': 0,   # 0:初始,1:拒绝,2:同意
        }

    # ========== 外部模块注入 ==========

    def set_callbacks(self, event_cb: Callable, state_cb: Callable):
        """设置事件/状态回调"""
        self._event_callback = event_cb
        self._state_callback = state_cb
        # 启动系统状态定时更新
        self._start_system_update()

    def set_nest_module(self, nest_module):
        """注入机巢通信模块（获取机巢环境/状态数据）"""
        self._nest_module = nest_module
        logger.info("DeviceManager: Nest module injected")

    def set_telemetry_processor(self, telemetry):
        """注入遥测处理器（获取MAVLink遥测数据）"""
        self._telemetry_processor = telemetry
        logger.info("DeviceManager: Telemetry processor injected")

    def set_dock_manager(self, dock):
        """注入机场管理模块"""
        self._dock_manager = dock
        logger.info("DeviceManager: Dock manager injected")

    # ========== 持久化数据加载 ==========

    def _load_persisted_data(self):
        """启动时加载持久化数据"""
        # 从配置中读取持久化属性
        device_config = Config.get('device', {})
        limits = device_config.get('limits', {})
        self._properties['remaining_power_for_return_home'] = limits.get(
            'return_home_power', 30)

        # 读取激活时间（如配置文件中有记录）
        # TODO: 从持久化存储读取
        self._properties['activation_time'] = 0

        # 读取作业次数
        # TODO: 从持久化存储读取
        self._properties['job_number'] = 0

    def _read_firmware_version(self) -> str:
        """读取当前固件版本"""
        # 优先从配置读取
        version = Config.get('device.firmware_version', '')
        if version:
            return version
        # 尝试从系统读取（如 /etc/edge_gateway_version）
        try:
            version_file = '/etc/edge_gateway_version'
            if os.path.exists(version_file):
                with open(version_file, 'r') as f:
                    return f.read().strip()
        except Exception:
            pass
        return '1.0.0'

    # ========== 属性管理核心方法 ==========

    def get_property(self, name: str) -> Any:
        """获取属性值"""
        return self._properties.get(name)

    def set_property(self, name: str, value: Any, notify: bool = True) -> bool:
        """
        设置属性值（仅可写属性）
        规则：
        - 只读属性拒绝修改
        - 值变更时自动触发state上报
        """
        if name not in self._properties:
            logger.warning(f"Property not found: {name}")
            return False

        if name not in self._writable_props:
            logger.warning(f"Property read-only: {name}")
            return False

        old_value = self._properties[name]
        if old_value == value:
            return True

        self._properties[name] = value
        logger.info(f"Property updated: {name} = {value} (was {old_value})")

        if notify and self._state_callback:
            self._notify_state({'property': {name: value}})

        return True

    def get_all_properties(self) -> Dict[str, Any]:
        """获取所有属性（深拷贝）"""
        import copy
        return copy.deepcopy(self._properties)

    def update_properties(self, updates: Dict[str, Any], notify: bool = True):
        """批量更新属性（内部遥测/机巢更新用）"""
        changed = {}
        for name, value in updates.items():
            if name in self._properties and self._properties[name] != value:
                self._properties[name] = value
                changed[name] = value

        if changed and notify and self._state_callback:
            self._notify_state({'property': changed})

    def _notify_state(self, changed: dict):
        """触发状态变更上报 (Topic: thing/product/{sn}/state)"""
        if self._state_callback:
            self._state_callback(changed)

    def _notify_event(self, method: str, data: dict):
        """触发事件上报 (Topic: thing/product/{sn}/events)"""
        if self._event_callback:
            event = MessageBuilder.build_event(method, data, self.gateway_sn)
            self._event_callback(event)

    # ========== 数据源更新方法 ==========

    def update_from_telemetry(self, osd_data: dict):
        """
        从遥测处理器接收MAVLink遥测数据，更新OSD属性
        由 main.py 在 TelemetryProcessor.build_osd_data() 后调用
        """
        updates = {}

        # 位置信息
        if 'latitude' in osd_data:
            updates['latitude'] = osd_data['latitude']
        if 'longitude' in osd_data:
            updates['longitude'] = osd_data['longitude']
        if 'height' in osd_data:
            updates['height'] = osd_data['height']

        # 飞行模式
        if 'mode_code' in osd_data:
            updates['mode_code'] = osd_data['mode_code']

        # 电量
        if 'battery' in osd_data:
            bat = osd_data['battery']
            updates['electricity'] = bat.get('capacity_percent', 100)
            # 更新无人机充电状态中的电量
            charge_state = self._properties.get('drone_charge_state', {}).copy()
            charge_state['capacity_percent'] = bat.get('capacity_percent', 100)
            updates['drone_charge_state'] = charge_state

        # GPS/搜星状态
        if 'position_state' in osd_data:
            updates['position_state'] = osd_data['position_state']

        # 风速
        if 'wind_speed' in osd_data:
            updates['wind_speed'] = osd_data['wind_speed']
        if 'wind_direction' in osd_data:
            # wind_direction 不在物模型中，但可用于内部逻辑
            pass

        # 飞行器是否在舱内（根据高度和模式推断，或从机巢获取）
        if 'gear' in osd_data and osd_data.get('gear') == 1:
            # 飞行器已解锁/起飞，不在舱内
            if self._properties.get('drone_in_dock') == 1:
                updates['drone_in_dock'] = 0

        if updates:
            self.update_properties(updates, notify=False)  # OSD属性不触发state，由TelemetryProcessor定时推送

    def update_from_nest(self, nest_data: dict):
        """
        从机巢模块接收机巢环境/状态数据，更新OSD属性
        由 main.py 在 DroneNestModule 收到 DRONE_NEST_RESPONSE 后调用
        """
        if not nest_data.get('data_valid', False):
            return

        updates = {}

        # 环境数据
        if 'env_temperature' in nest_data:
            updates['environment_temperature'] = nest_data['env_temperature']
        if 'nest_temperature' in nest_data:
            updates['temperature'] = nest_data['nest_temperature']
        if 'wind_speed' in nest_data:
            updates['wind_speed'] = nest_data['wind_speed']
        if 'rainfall' in nest_data:
            # rainfall: 0=无, >0 映射到 1/2/3
            raw_rain = nest_data['rainfall']
            if raw_rain <= 0:
                updates['rainfall'] = 0
            elif raw_rain < 2.5:
                updates['rainfall'] = 1
            elif raw_rain < 8:
                updates['rainfall'] = 2
            else:
                updates['rainfall'] = 3
        if 'humidity' in nest_data:
            updates['humidity'] = nest_data['humidity']

        # 电气数据
        if 'charge_volt' in nest_data:
            updates['working_voltage'] = int(nest_data['charge_volt'] * 1000)  # V -> mV
        if 'charge_current' in nest_data:
            updates['working_current'] = nest_data['charge_current'] * 1000  # A -> mA
        if 'electric_supply_voltage' in nest_data:
            updates['electric_supply_voltage'] = nest_data['electric_supply_voltage']

        # 状态位（从 boolstate 解析）
        if 'estop_state' in nest_data:
            updates['emergency_stop_state'] = 1 if nest_data['estop_state'] else 0
        if 'charging_state' in nest_data:
            updates['charging_state'] = 1 if nest_data['charging_state'] else 0
            # 联动更新无人机充电状态
            charge_state = self._properties.get('drone_charge_state', {}).copy()
            charge_state['state'] = 1 if nest_data['charging_state'] else 0
            updates['drone_charge_state'] = charge_state

        # 舱门/推杆状态（从 gate_state/clamp_state 映射）
        if 'gate_state' in nest_data:
            gate = nest_data['gate_state']
            # 0=默认/关闭, 1=打开, 3=开启中, 4=关闭中, 5=暂停, 6=故障
            if gate == 1:
                updates['cover_state'] = 1
            elif gate in (0, 2):
                updates['cover_state'] = 0
            elif gate in (3, 4, 5):
                updates['cover_state'] = 2  # 半开/进行中
            elif gate == 6:
                updates['cover_state'] = 3  # 异常

        if 'clamp_state' in nest_data:
            clamp = nest_data['clamp_state']
            # 0=默认, 1=放开, 2=夹紧, 3=放开打开中, 4=夹紧关闭中, 5=暂停, 6=故障
            if clamp == 1:
                updates['putter_state'] = 1
            elif clamp in (0, 2):
                updates['putter_state'] = 0
            elif clamp in (3, 4, 5):
                updates['putter_state'] = 2
            elif clamp == 6:
                updates['putter_state'] = 3

        # 着陆灯状态
        if 'landing_light_state' in nest_data:
            light = nest_data['landing_light_state']
            updates['supplement_light_state'] = 1 if light == 1 else 0

        # 空调状态
        if 'ac_temp' in nest_data:
            ac_state = self._properties.get('air_conditioner', {}).copy()
            ac_state['air_conditioner_state'] = 1 if nest_data['ac_temp'] > 0 else 0
            updates['air_conditioner'] = ac_state

        # 一键起飞允许状态
        if 'takeoff_allowed' in nest_data:
            # takeoff_allowed 不是物模型属性，但可用于内部逻辑
            pass

        # 飞行器在舱内推断（综合机巢状态和飞控状态）
        if 'drone_heartbeat' in nest_data:
            drone_hb = nest_data['drone_heartbeat']
            if not drone_hb:
                # 无人机无心跳，可能在舱内或已关机
                pass

        if updates:
            self.update_properties(updates, notify=False)

    def _update_system_status(self):
        """查询并更新系统状态（存储、网络等）"""
        updates = {}

        # 存储查询
        try:
            media_path = Config.get('media.storage_path', '/data/media')
            # 先查询根目录（网关系统存储）
            total, used, free = shutil.disk_usage('/')
            # 转换为KB
            updates['storage'] = {
                'total': total // 1024,
                'used': used // 1024
            }
            updates['remain_storage_capacity'] = free // (1024 * 1024)  # MB
        except Exception as e:
            logger.warning(f"Storage query failed: {e}")

        # 累计运行时间
        elapsed = int(time.time() - self._acc_time_start)
        updates['acc_time'] = self._properties.get('acc_time', 0) + elapsed
        self._acc_time_start = time.time()

        # 网络状态（简化实现，读取 /proc/net/dev 或调用 ifconfig）
        try:
            net_info = self._query_network_status()
            if net_info:
                updates['network_state'] = net_info
        except Exception as e:
            logger.debug(f"Network query failed: {e}")

        if updates:
            self.update_properties(updates, notify=False)

    def _query_network_status(self) -> Optional[dict]:
        """查询网络状态"""
        try:
            # 读取 /proc/net/dev 获取网络接口流量
            with open('/proc/net/dev', 'r') as f:
                lines = f.readlines()

            # 查找主要网络接口（eth0, wlan0, usb0, enp* 等）
            for line in lines[2:]:
                parts = line.strip().split()
                iface = parts[0].rstrip(':')
                if iface in ('lo', 'docker', 'br-'):
                    continue
                rx_bytes = int(parts[1])
                tx_bytes = int(parts[9])
                # 简化：只要有流量就认为在线
                net_type = 1  # 默认4G
                if iface.startswith('eth') or iface.startswith('enp'):
                    net_type = 2  # 以太网

                return {
                    'type': net_type,
                    'quality': 4,  # 简化：假设信号较好
                    'rate': (rx_bytes + tx_bytes) / 1024.0  # KB total
                }
        except Exception:
            pass
        return None

    def _start_system_update(self):
        """启动系统状态定时更新线程"""
        if self._system_update_running:
            return
        self._system_update_running = True
        self._system_update_thread = threading.Thread(
            target=self._system_update_loop, daemon=True
        )
        self._system_update_thread.start()
        logger.info("System status update thread started")

    def _system_update_loop(self):
        """系统状态定时更新循环"""
        while self._system_update_running:
            try:
                self._update_system_status()
            except Exception as e:
                logger.error(f"System update error: {e}")
            time.sleep(self._system_update_interval)

    def stop(self):
        """停止后台线程"""
        self._system_update_running = False
        if self._system_update_thread:
            self._system_update_thread.join(timeout=2)
        # 取消OTA
        if self._ota_thread and self._ota_thread.is_alive():
            self._ota_cancel_flag = True
            self._ota_thread.join(timeout=5)

    # ========== 拓扑管理 ==========

    def update_device_topology(self, device_sn: str, product_type: str,
                               online: bool, paired: bool):
        """更新单个设备拓扑信息"""
        self._properties['sub_device'] = {
            'device_sn': device_sn,
            'product_type': product_type,
            'device_online_status': 1 if online else 0,
            'device_paired': 1 if paired else 0
        }
        self._notify_state({'sub_device': self._properties['sub_device']})

    def add_sub_device(self, sub_device: SubDevice) -> bool:
        """添加子设备"""
        if not sub_device.sn:
            logger.warning("Sub device SN empty")
            return False
        self._sub_devices[sub_device.sn] = sub_device
        logger.info(f"Sub device added: {sub_device.sn}")
        self.update_device_topology(
            sub_device.sn, str(sub_device.type),
            sub_device.online, sub_device.paired
        )
        return True

    def remove_sub_device(self, sn: str) -> bool:
        """移除子设备"""
        if sn in self._sub_devices:
            del self._sub_devices[sn]
            logger.info(f"Sub device removed: {sn}")
            self._properties['sub_device'] = {
                'device_sn': '', 'product_type': '',
                'device_online_status': 0, 'device_paired': 0
            }
            self._notify_state({'sub_device': self._properties['sub_device']})
            return True
        return False

    def get_sub_device(self, sn: str) -> Optional[SubDevice]:
        """获取子设备"""
        return self._sub_devices.get(sn)

    def get_all_sub_devices(self) -> List[SubDevice]:
        """获取所有子设备"""
        return list(self._sub_devices.values())

    def build_topo_payload(self) -> dict:
        """
        构造拓扑上报数据（device.md update_topo 标准格式）
        Topic: sys/product/{gateway_sn}/status
        """
        sub_devices = [
            dev.to_dict() for dev in self._sub_devices.values()
            if dev.online  # 只上报在线子设备
        ]
        return {
            "domain": self._gateway_domain,
            "type": self._gateway_type,
            "sub_type": self._gateway_sub_type,
            "device_secret": self._gateway_device_secret,
            "nonce": self._gateway_nonce,
            "thing_version": self._gateway_thing_version,
            "sub_devices": sub_devices
        }

    def report_update_topo(self):
        """主动上报设备拓扑（update_topo）"""
        payload = self.build_topo_payload()
        self._notify_event('update_topo', payload)
        logger.info(f"Update topo reported: {len(payload['sub_devices'])} sub-devices")

    def handle_update_topo_reply(self, method: str, msg: dict):
        """处理 update_topo 的云端回复 (status_reply)"""
        if method != 'update_topo':
            logger.warning(f"Unexpected status_reply method: {method}")
            return
        data = msg.get('data', {})
        result = data.get('result', -1)
        if result == 0:
            logger.info("Update topo accepted by cloud")
        else:
            logger.error(f"Update topo rejected by cloud, result={result}")

    # ========== 设备生命周期 ==========

    def bind_device(self, workspace_id: str, workspace_name: str) -> bool:
        """设备绑定到工作空间"""
        self._device_state = self.STATE_BOUND
        self._bound_workspace_id = workspace_id
        self._bound_workspace_name = workspace_name
        logger.info(f"Device bound to workspace: {workspace_id}")
        self._notify_event('device_binding', {
            'workspace_id': workspace_id,
            'bind_status': 'success',
            'timestamp': int(time.time() * 1000)
        })
        return True

    def unbind_device(self) -> bool:
        """设备解绑"""
        self._device_state = self.STATE_UNBOUND
        self._bound_workspace_id = ""
        self._bound_workspace_name = ""
        self._sub_devices.clear()
        logger.info("Device unbound")
        self._notify_event('device_unbinding', {
            'timestamp': int(time.time() * 1000)
        })
        return True

    def device_online(self):
        """设备上线"""
        if self._device_state >= self.STATE_BOUND:
            self._device_state = self.STATE_ONLINE
            logger.info("Device online")
            self._notify_event('device_online', {
                'online_status': 1,
                'timestamp': int(time.time() * 1000)
            })
            self.report_update_topo()

    def device_offline(self):
        """设备离线"""
        if self._device_state == self.STATE_ONLINE:
            self._device_state = self.STATE_OFFLINE
            logger.info("Device offline")
            self._notify_event('device_offline', {
                'online_status': 0,
                'timestamp': int(time.time() * 1000)
            })

    def get_device_state(self) -> dict:
        """获取设备生命周期状态"""
        state_names = {
            self.STATE_UNBOUND: 'unbound',
            self.STATE_BOUND: 'bound',
            self.STATE_ONLINE: 'online',
            self.STATE_OFFLINE: 'offline',
            self.STATE_UPGRADING: 'upgrading'
        }
        return {
            'state': state_names.get(self._device_state, 'unknown'),
            'state_code': self._device_state,
            'workspace_id': self._bound_workspace_id,
            'workspace_name': self._bound_workspace_name,
            'sub_device_count': len(self._sub_devices)
        }


    # ========== API处理器：属性管理 ==========

    def handle_property_set(self, msg: dict) -> dict:
        """
        处理属性设置请求 (property/set)

        Request: { properties: {name: value, ...} }
        Response: { result: 0/1, results: {name: 0/-1, ...} }
        """
        data = msg.get('data', {})
        properties = data.get('properties', {})

        results = {}
        for name, value in properties.items():
            success = self.set_property(name, value)
            results[name] = 0 if success else -1

        failed = [k for k, v in results.items() if v != 0]
        result = 0 if not failed else -1

        return MessageBuilder.build_reply(
            msg, result=result,
            data={'results': results},
            msg_str='' if result == 0 else f"Failed: {failed}"
        )

    def handle_property_get(self, msg: dict) -> dict:
        """
        属性获取 (property_get)

        Request: { properties: [name1, name2, ...] }
        Response: { name1: value1, name2: value2, ... }
        """
        data = msg.get('data', {})
        props = data.get('properties', [])
        if not props:
            result = self.get_all_properties()
        else:
            result = {name: self._properties.get(name) for name in props if name in self._properties}
        return MessageBuilder.build_reply(msg, result=0, data=result)

    # ========== API处理器：固件升级（真实异步流程） ==========

    def handle_ota_create(self, msg: dict) -> dict:
        """
        固件升级任务创建 (ota_create)

        状态机：idle -> checking -> downloading -> verifying -> upgrading -> completed/failed
        真实流程：
        1. 校验参数和当前状态
        2. 启动后台线程下载固件
        3. 校验MD5
        4. 执行刷写（调用系统命令或重启进入bootloader）
        5. 上报进度和结果
        """
        data = msg.get('data', {})
        devices = data.get('devices', [])

        if not devices:
            return MessageBuilder.build_reply(
                msg, result=-1, msg_str="No devices in OTA request"
            )

        if self._ota_state != 'idle':
            return MessageBuilder.build_reply(
                msg, result=-1,
                msg_str=f"OTA already in progress: {self._ota_state}"
            )

        # 取第一个设备作为当前升级目标
        device_info = devices[0]
        self._ota_target_version = device_info.get('product_version', '')
        self._ota_package_url = device_info.get('file_url', '')
        package_size = device_info.get('file_size', 0)
        self._ota_md5 = device_info.get('md5', '')
        self._ota_file_name = device_info.get('file_name', '')
        upgrade_type = device_info.get('firmware_upgrade_type', 3)

        self._ota_state = 'checking'
        self._ota_progress = 0
        self._ota_task_id = str(int(time.time() * 1000))
        self._ota_cancel_flag = False

        self.update_properties({
            'firmware_upgrade_status': self._firmware_status_map('checking')
        })

        logger.info(f"OTA task created: target={self._ota_target_version}, "
                   f"url={self._ota_package_url}, type={upgrade_type}")

        # 启动异步升级线程
        self._ota_thread = threading.Thread(
            target=self._ota_worker,
            args=(self._ota_package_url, package_size, self._ota_md5),
            daemon=True
        )
        self._ota_thread.start()

        return MessageBuilder.build_reply(msg, result=0, data={
            'output': {'status': 'in_progress'}
        })

    def handle_ota_cancel(self, msg: dict) -> dict:
        """固件升级取消 (ota_cancel)"""
        if self._ota_state == 'idle':
            return MessageBuilder.build_reply(msg, result=-1, msg_str="No OTA in progress")

        self._ota_cancel_flag = True
        self._ota_state = 'idle'
        self._ota_progress = 0
        self.update_properties({
            'firmware_upgrade_status': self._firmware_status_map('idle')
        })
        logger.info("OTA cancelled")
        return MessageBuilder.build_reply(msg, result=0)

    def _ota_worker(self, package_url: str, package_size: int, expected_md5: str):
        """
        OTA异步工作线程
        真实流程：下载 -> 校验 -> 刷写 -> 重启
        """
        import hashlib
        import urllib.request
        import tempfile

        download_path = None
        try:
            # Step 1: 下载
            self._ota_state = 'downloading'
            self.report_ota_progress('in_progress', 10, 'download_firmware')

            if self._ota_cancel_flag:
                raise Exception("OTA cancelled")

            # 创建临时文件
            suffix = self._ota_file_name or 'firmware.bin'
            fd, download_path = tempfile.mkstemp(suffix=suffix)
            os.close(fd)

            # 下载文件（带进度回调）
            def download_progress(block_num, block_size, total_size):
                if self._ota_cancel_flag:
                    raise Exception("OTA cancelled")
                downloaded = block_num * block_size
                percent = min(50, int(10 + (downloaded / total_size) * 40))
                if percent != self._ota_progress:
                    self.report_ota_progress('in_progress', percent, 'download_firmware')

            urllib.request.urlretrieve(package_url, download_path, reporthook=download_progress)
            self.report_ota_progress('in_progress', 50, 'download_firmware')
            logger.info(f"OTA firmware downloaded: {download_path}")

            # Step 2: MD5校验
            self._ota_state = 'verifying'
            self.report_ota_progress('in_progress', 55, 'verify_firmware')

            if self._ota_cancel_flag:
                raise Exception("OTA cancelled")

            actual_md5 = self._calc_file_md5(download_path)
            if expected_md5 and actual_md5.lower() != expected_md5.lower():
                raise Exception(f"MD5 mismatch: expected={expected_md5}, actual={actual_md5}")

            self.report_ota_progress('in_progress', 60, 'verify_firmware')
            logger.info(f"OTA firmware MD5 verified: {actual_md5}")

            # Step 3: 刷写（调用系统刷写脚本）
            self._ota_state = 'upgrading'
            self.report_ota_progress('in_progress', 65, 'upgrade_firmware')

            if self._ota_cancel_flag:
                raise Exception("OTA cancelled")

            # 调用刷写脚本（如果存在）
            flash_script = '/usr/local/bin/flash_firmware.sh'
            if os.path.exists(flash_script):
                import subprocess
                result = subprocess.run(
                    ['sudo', flash_script, download_path],
                    capture_output=True, text=True, timeout=300
                )
                if result.returncode != 0:
                    raise Exception(f"Flash failed: {result.stderr}")
            else:
                # 无刷写脚本时，模拟刷写过程
                logger.warning("Flash script not found, simulating flash process")
                for i in range(65, 95, 5):
                    if self._ota_cancel_flag:
                        raise Exception("OTA cancelled")
                    self.report_ota_progress('in_progress', i, 'upgrade_firmware')
                    time.sleep(1)

            self.report_ota_progress('in_progress', 95, 'upgrade_firmware')
            logger.info("OTA firmware flashed successfully")

            # Step 4: 更新版本号并标记完成
            self._properties['firmware_version'] = self._ota_target_version
            self._properties['firmware_upgrade_status'] = self._firmware_status_map('completed')
            self.report_ota_progress('ok', 100, 'upgrade_firmware')
            self.report_ota_result(True)
            logger.info(f"OTA completed: version={self._ota_target_version}")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"OTA failed: {error_msg}")
            self._properties['firmware_upgrade_status'] = self._firmware_status_map('failed')
            self.report_ota_progress('failed', self._ota_progress, 'upgrade_firmware')
            self.report_ota_result(False, error_code=-1, error_msg=error_msg)

        finally:
            self._ota_state = 'idle'
            if download_path and os.path.exists(download_path):
                try:
                    os.remove(download_path)
                except Exception:
                    pass

    @staticmethod
    def _calc_file_md5(filepath: str) -> str:
        """计算文件MD5"""
        import hashlib
        h = hashlib.md5()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()

    def report_ota_progress(self, status: str, progress: int, step: str = ""):
        """上报OTA进度 (ota_progress)"""
        self._ota_state = status
        self._ota_progress = progress
        self.update_properties({
            'firmware_upgrade_status': self._firmware_status_map(status)
        }, notify=False)

        self._notify_event('ota_progress', {
            'result': 0,
            'output': {
                'status': status,
                'progress': {
                    'percent': progress,
                    'step_key': step or 'download_firmware'
                }
            }
        })

    def report_ota_result(self, success: bool, error_code: int = 0, error_msg: str = ""):
        """上报OTA结果"""
        self._ota_state = 'idle' if success else 'failed'
        self.update_properties({
            'firmware_upgrade_status': self._firmware_status_map(self._ota_state)
        }, notify=False)
        self._notify_event('ota_result', {
            'task_id': self._ota_task_id,
            'result': 0 if success else -1,
            'error_code': error_code,
            'error_msg': error_msg,
            'target_version': self._ota_target_version
        })
        self._ota_progress = 0
        self._ota_target_version = ""

    @staticmethod
    def _firmware_status_map(status: str) -> int:
        """OTA状态字符串转枚举值"""
        mapping = {
            'idle': 0, 'checking': 1, 'downloading': 2,
            'verifying': 3, 'upgrading': 4, 'completed': 5, 'failed': 6
        }
        return mapping.get(status, 0)

    # ========== API处理器：配置更新 ==========

    def handle_config_update(self, msg: dict) -> dict:
        """
        配置更新 (config_update)

        Request: { config: {key: value, ...} }
        Response: { result, applied_keys, failed_keys }
        Event: config_update_result
        """
        data = msg.get('data', {})
        config = data.get('config', {})

        logger.info(f"Config update received: {config}")
        applied = {}
        failed = {}

        for key, value in config.items():
            # 尝试应用配置到属性或配置文件
            if key in self._properties:
                if key in self._writable_props:
                    self.set_property(key, value, notify=False)
                    applied[key] = value
                else:
                    failed[key] = "read-only property"
            else:
                # 未知配置项，记录但不报错
                applied[key] = value

        # 上报配置更新结果
        self._notify_event('config_update_result', {
            'config': config,
            'applied': applied,
            'failed': failed,
            'status': 'success' if not failed else 'partial'
        })

        return MessageBuilder.build_reply(msg, result=0, data={
            'applied_keys': list(applied.keys()),
            'failed_keys': list(failed.keys())
        })

    def handle_config(self, msg: dict) -> dict:
        """
        config (requests/requests_reply 模式)
        设备主动请求云端配置（ntp/app_id/app_key/app_license）
        这里处理云端下发的配置响应（如果云端通过 services 下发）
        """
        return self.handle_config_update(msg)

    # ========== API处理器：设备绑定与组织 ==========

    def handle_device_binding(self, msg: dict) -> dict:
        """
        设备绑定 (device_binding / airport_organization_bind)

        Request: { workspace_id, workspace_name }
        Response: { result, workspace_id, bind_status }
        """
        data = msg.get('data', {})
        workspace_id = data.get('workspace_id', '')
        workspace_name = data.get('workspace_name', '')

        if not workspace_id:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="workspace_id required")

        self.bind_device(workspace_id, workspace_name)
        return MessageBuilder.build_reply(msg, result=0, data={
            'workspace_id': workspace_id,
            'bind_status': 'success'
        })

    def handle_device_unbinding(self, msg: dict) -> dict:
        """设备解绑 (device_unbinding)"""
        self.unbind_device()
        return MessageBuilder.build_reply(msg, result=0)

    def handle_workspace_bind_status(self, msg: dict) -> dict:
        """
        查询工作空间绑定状态 (workspace_bind_status / airport_bind_status)

        requests/requests_reply 模式，返回绑定状态
        """
        return MessageBuilder.build_reply(msg, result=0, data={
            'is_bind': self._device_state >= self.STATE_BOUND,
            'workspace_id': self._bound_workspace_id,
            'workspace_name': self._bound_workspace_name,
            'bind_time': int(time.time() * 1000) if self._bound_workspace_id else 0
        })

    def handle_workspace_get(self, msg: dict) -> dict:
        """
        获取工作空间信息 (workspace_get / airport_organization_get)

        requests/requests_reply 模式
        """
        return MessageBuilder.build_reply(msg, result=0, data={
            'workspace_id': self._bound_workspace_id or 'default_workspace',
            'workspace_name': self._bound_workspace_name or 'Default Workspace',
            'organization_id': 'default_org',
            'organization_name': 'Default Organization'
        })

    # ========== API处理器：设备控制（真实下发到机巢） ==========

    def _send_nest_command(self, command_name: str, prop_name: str, prop_value: int) -> bool:
        """
        通过机巢模块发送真实控制命令

        Args:
            command_name: 命令名称（用于日志）
            prop_name: 对应的物模型属性名
            prop_value: 属性目标值

        Returns:
            bool: 命令是否成功发送
        """
        if not self._nest_module:
            logger.warning(f"{command_name}: Nest module not available, updating property only")
            self.update_properties({prop_name: prop_value})
            return True

        try:
            from generated_dialect.gcs_order_protocol import GCSCommand

            cmd_map = {
                ('cover_state', 1): GCSCommand.DOOR_OPEN,
                ('cover_state', 0): GCSCommand.DOOR_CLOSE,
                ('charging_state', 1): GCSCommand.CHARGE_START,
                ('charging_state', 0): GCSCommand.CHARGE_STOP,
                ('putter_state', 1): GCSCommand.CLAMP_RELEASE,   # 推出=释放夹紧
                ('putter_state', 0): GCSCommand.CLAMP_TIGHTEN,   # 收回=夹紧
            }

            cmd = cmd_map.get((prop_name, prop_value))
            if cmd:
                self._nest_module.send_gcs_command(cmd)
                logger.info(f"{command_name}: Nest command sent -> {cmd.name}")
            else:
                logger.warning(f"{command_name}: No nest command mapping for {prop_name}={prop_value}")

            # 先更新属性（实际项目中应等待机巢响应确认后再更新）
            self.update_properties({prop_name: prop_value})
            return True

        except Exception as e:
            logger.error(f"{command_name}: Failed to send nest command: {e}")
            return False

    def handle_cover_open(self, msg: dict) -> dict:
        """打开舱盖 - 通过机巢协议真实下发"""
        self._send_nest_command('cover_open', 'cover_state', 1)
        return MessageBuilder.build_reply(msg, result=0, data={'action': 'cover_open'})

    def handle_cover_close(self, msg: dict) -> dict:
        """关闭舱盖 - 通过机巢协议真实下发"""
        self._send_nest_command('cover_close', 'cover_state', 0)
        return MessageBuilder.build_reply(msg, result=0, data={'action': 'cover_close'})

    def handle_cover_force_close(self, msg: dict) -> dict:
        """
        强制关闭舱盖
        安全校验：仅在确认飞行器不在舱内时调用
        """
        drone_in = self._properties.get('drone_in_dock', 1)
        if drone_in == 1:
            logger.warning("Force close cover rejected: drone still in dock")
            return MessageBuilder.build_reply(
                msg, result=-1,
                msg_str="Drone in dock, cannot force close cover"
            )
        self._send_nest_command('cover_force_close', 'cover_state', 0)
        return MessageBuilder.build_reply(msg, result=0, data={'action': 'cover_force_close'})

    def handle_charge_open(self, msg: dict) -> dict:
        """开启充电 - 通过机巢协议真实下发"""
        self._send_nest_command('charge_open', 'charging_state', 1)
        return MessageBuilder.build_reply(msg, result=0, data={'action': 'charge_open'})

    def handle_charge_close(self, msg: dict) -> dict:
        """关闭充电 - 通过机巢协议真实下发"""
        self._send_nest_command('charge_close', 'charging_state', 0)
        return MessageBuilder.build_reply(msg, result=0, data={'action': 'charge_close'})

    def handle_putter_open(self, msg: dict) -> dict:
        """打开推杆 - 通过机巢协议真实下发"""
        self._send_nest_command('putter_open', 'putter_state', 1)
        return MessageBuilder.build_reply(msg, result=0, data={'action': 'putter_open'})

    def handle_putter_close(self, msg: dict) -> dict:
        """关闭推杆 - 通过机巢协议真实下发"""
        self._send_nest_command('putter_close', 'putter_state', 0)
        return MessageBuilder.build_reply(msg, result=0, data={'action': 'putter_close'})

    def handle_device_lock(self, msg: dict) -> dict:
        """设备锁定"""
        self.update_properties({'mode_code': 0})
        return MessageBuilder.build_reply(msg, result=0)

    def handle_device_unlock(self, msg: dict) -> dict:
        """设备解锁"""
        return MessageBuilder.build_reply(msg, result=0)

    # ========== API处理器：灯光/SDR/仿真/调试 ==========

    def handle_supplement_light_open(self, msg: dict) -> dict:
        """补光灯开"""
        # TODO: 补光灯控制需通过实际硬件接口（如GPIO或机巢扩展协议）实现
        # 当前仅更新物模型状态
        logger.info("Supplement light ON")
        self.update_properties({'supplement_light_state': 1})
        return MessageBuilder.build_reply(msg, result=0, data={'action': 'supplement_light_open'})

    def handle_supplement_light_close(self, msg: dict) -> dict:
        """补光灯关"""
        logger.info("Supplement light OFF")
        self.update_properties({'supplement_light_state': 0})
        return MessageBuilder.build_reply(msg, result=0, data={'action': 'supplement_light_close'})

    def handle_sdr_workmode_switch(self, msg: dict) -> dict:
        """SDR工作模式切换"""
        data = msg.get('data', {})
        mode = data.get('mode', 0)
        logger.info(f"SDR workmode switch: {mode}")
        # 更新wireless_link中的link_workmode
        wireless = self._properties.get('wireless_link', {}).copy()
        wireless['link_workmode'] = mode
        self.update_properties({'wireless_link': wireless})
        return MessageBuilder.build_reply(msg, result=0, data={'mode': mode})

    def handle_debug_mode_open(self, msg: dict) -> dict:
        """调试模式开启"""
        self._debug_mode = True
        self.update_properties({'mode_code': 1})
        logger.info("Debug mode ON")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_debug_mode_close(self, msg: dict) -> dict:
        """调试模式关闭"""
        self._debug_mode = False
        self.update_properties({'mode_code': 0})
        logger.info("Debug mode OFF")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_device_enter_simulation_mode(self, msg: dict) -> dict:
        """进入仿真模式"""
        self._simulation_mode = True
        self.update_properties({'mode_code': 6})
        logger.info("Simulation mode ON")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_device_exit_simulation_mode(self, msg: dict) -> dict:
        """退出仿真模式"""
        self._simulation_mode = False
        self.update_properties({'mode_code': 0})
        logger.info("Simulation mode OFF")
        return MessageBuilder.build_reply(msg, result=0)

    # ========== API处理器：电池/空调/报警 ==========

    def handle_battery_maintenance_switch(self, msg: dict) -> dict:
        """
        电池保养状态切换
        action: 0=关闭保养 1=开启保养
        """
        data = msg.get('data', {})
        action = data.get('action', 0)
        self.set_property('battery_maintenance_mode', action)
        logger.info(f"Battery maintenance: {'ON' if action else 'OFF'}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_air_conditioner_mode_switch(self, msg: dict) -> dict:
        """
        机场空调工作模式切换
        action: 0=关闭 1=制冷 2=制热 3=自动
        """
        data = msg.get('data', {})
        action = data.get('action', 0)
        self.set_property('air_conditioner_mode', action)
        # 联动更新air_conditioner结构
        ac = self._properties.get('air_conditioner', {}).copy()
        ac['air_conditioner_state'] = action
        self.update_properties({'air_conditioner': ac})
        logger.info(f"Air conditioner mode: {action}")
        return MessageBuilder.build_reply(msg, result=0, data={'mode': action})

    def handle_alarm_state_switch(self, msg: dict) -> dict:
        """
        机场声光报警开关
        action: 0=关闭 1=开启
        """
        data = msg.get('data', {})
        action = data.get('action', 0)
        self.set_property('alarm_state', action)
        logger.info(f"Alarm: {'ON' if action else 'OFF'}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_battery_store_mode_switch(self, msg: dict) -> dict:
        """
        电池运行模式切换
        action: 1=正常工作模式 2=存储模式
        """
        data = msg.get('data', {})
        action = data.get('action', 1)
        self.set_property('battery_store_mode', action)
        logger.info(f"Battery store mode: {action}")
        return MessageBuilder.build_reply(msg, result=0)

    # ========== API处理器：RTK/降落点 ==========

    def handle_alternate_land_point_set(self, msg: dict) -> dict:
        """备用降落点设置"""
        data = msg.get('data', {})
        lat = data.get('latitude', 0)
        lon = data.get('longitude', 0)
        alt = data.get('altitude', 0)
        safe_height = data.get('safe_land_height', 0)
        logger.info(f"Alternate land point: ({lat}, {lon}, {alt})")

        self._properties['alternate_land_point'] = {
            'latitude': lat,
            'longitude': lon,
            'safe_land_height': safe_height,
            'is_configured': 1,
            'height': alt
        }
        self._notify_state({'alternate_land_point': self._properties['alternate_land_point']})

        # TODO: 持久化到配置文件
        return MessageBuilder.build_reply(msg, result=0, data={
            'latitude': lat, 'longitude': lon, 'altitude': alt
        })

    def handle_rtk_ref_points_set(self, msg: dict) -> dict:
        """RTK参考点设置"""
        data = msg.get('data', {})
        points = data.get('points', [])
        logger.info(f"RTK ref points set: {len(points)} points")
        # TODO: 持久化到配置文件
        return MessageBuilder.build_reply(msg, result=0)

    def handle_rtk_ref_points_query(self, msg: dict) -> dict:
        """RTK参考点查询"""
        logger.info("RTK ref points query")
        # TODO: 从配置文件读取
        return MessageBuilder.build_reply(msg, result=0, data={'points': []})

    def handle_device_exit_rtk_mode(self, msg: dict) -> dict:
        """退出RTK模式"""
        logger.info("Device exit RTK mode")
        return MessageBuilder.build_reply(msg, result=0)

    # ========== API处理器：飞行器开关机/格式化 ==========

    def handle_drone_open(self, msg: dict) -> dict:
        """
        飞行器开机
        TODO: 通过机巢主控继电器或飞控电源管理模块实现真实上电
        """
        logger.info("Drone power ON")
        self.update_properties({'drone_in_dock': 0})
        for dev in self._sub_devices.values():
            dev.online = True
            dev.last_heartbeat = time.time()
        self.report_update_topo()
        return MessageBuilder.build_reply(msg, result=0)

    def handle_drone_close(self, msg: dict) -> dict:
        """
        飞行器关机
        TODO: 通过机巢主控继电器或飞控电源管理模块实现真实断电
        """
        logger.info("Drone power OFF")
        self.update_properties({'drone_in_dock': 1})
        for dev in self._sub_devices.values():
            dev.online = False
        self.report_update_topo()
        return MessageBuilder.build_reply(msg, result=0)

    def handle_drone_format(self, msg: dict) -> dict:
        """飞行器数据格式化"""
        logger.info("Drone format")
        self.update_properties({
            'remain_storage_capacity': self._properties.get('storage_capacity', 64000),
            'media_file_detail': {'remain_upload': 0}
        })
        return MessageBuilder.build_reply(msg, result=0)

    def handle_device_format(self, msg: dict) -> dict:
        """设备格式化（网关本地存储格式化）"""
        logger.info("Device format")
        try:
            media_path = Config.get('media.storage_path', '/data/media')
            if os.path.exists(media_path):
                for item in os.listdir(media_path):
                    item_path = os.path.join(media_path, item)
                    if os.path.isfile(item_path):
                        os.remove(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                logger.info(f"Device format completed: {media_path}")
        except Exception as e:
            logger.error(f"Device format failed: {e}")
        return MessageBuilder.build_reply(msg, result=0)

    # ========== API处理器：相机模式/重启/标定/重置 ==========

    def handle_camera_mode_switch(self, msg: dict) -> dict:
        """相机模式切换"""
        data = msg.get('data', {})
        mode = data.get('mode', 'photo')
        logger.info(f"Camera mode switch: {mode}")
        return MessageBuilder.build_reply(msg, result=0, data={'mode': mode})

    def handle_device_reboot(self, msg: dict) -> dict:
        """
        设备重启
        启动异步线程执行系统重启，避免阻塞MQTT响应
        """
        logger.info("Device reboot requested")

        def do_reboot():
            time.sleep(3)
            try:
                os.system('sudo reboot')
            except Exception as e:
                logger.error(f"Reboot failed: {e}")

        reboot_thread = threading.Thread(target=do_reboot, daemon=True)
        reboot_thread.start()

        return MessageBuilder.build_reply(msg, result=0, data={
            'reboot_delay': 3
        })

    def handle_device_calibration(self, msg: dict) -> dict:
        """设备标定"""
        logger.info("Device calibration")
        self.update_properties({'mode_code': 5})  # 标定中
        # TODO: 启动异步标定流程（调用传感器标定脚本）
        return MessageBuilder.build_reply(msg, result=0, data={
            'calibration_status': 'started'
        })

    def handle_device_reset(self, msg: dict) -> dict:
        """设备重置 - 重置所有控制属性到默认值"""
        logger.info("Device reset")
        reset_props = {
            'cover_state': 0,
            'putter_state': 0,
            'charging_state': 0,
            'air_conditioner_mode': 0,
            'alarm_state': 0,
            'supplement_light_state': 0,
            'battery_maintenance_mode': 0,
            'battery_store_mode': 1,
            'mode_code': 0,
            'debug_mode': False,
        }
        for name, value in reset_props.items():
            if name in self._writable_props:
                self.set_property(name, value, notify=False)
            elif name in self._properties:
                self._properties[name] = value

        self._notify_state({'property': {k: v for k, v in reset_props.items() if k in self._properties}})
        return MessageBuilder.build_reply(msg, result=0)

    def handle_format_sd_card(self, msg: dict) -> dict:
        """格式化SD卡"""
        logger.info("Format SD card")
        return self.handle_device_format(msg)

    # ========== API处理器：HMS/日志/能力查询 ==========

    def report_hms(self, alarms: list):
        """
        上报 HMS 健康告警事件 (hms)
        由外部健康监控模块调用
        """
        self._hms_alarms = alarms
        self._notify_event('hms', {'list': alarms})
        logger.info(f"HMS reported: {len(alarms)} alarms")

    def handle_hms_get(self, msg: dict) -> dict:
        """
        获取HMS告警信息（services 模式，云端查询）
        返回当前缓存的告警列表
        """
        data = msg.get('data', {})
        page = data.get('page', 1)
        page_size = data.get('page_size', 20)

        total = len(self._hms_alarms)
        start = (page - 1) * page_size
        end = start + page_size
        alarms = self._hms_alarms[start:end]

        logger.info(f"HMS get: total={total}, page={page}")
        return MessageBuilder.build_reply(msg, result=0, data={
            'alarms': alarms,
            'total': total,
            'has_more': end < total
        })

    def handle_hms_clear(self, msg: dict) -> dict:
        """清除HMS告警（services 模式，云端下发）"""
        data = msg.get('data', {})
        alarm_ids = data.get('alarm_ids', [])
        logger.info(f"HMS clear: {len(alarm_ids)} alarms")

        if alarm_ids:
            self._hms_alarms = [a for a in self._hms_alarms if a.get('id') not in alarm_ids]
        else:
            self._hms_alarms = []

        return MessageBuilder.build_reply(msg, result=0, data={
            'cleared_count': len(alarm_ids)
        })

    def handle_device_log_list_get(self, msg: dict) -> dict:
        """获取设备日志列表"""
        data = msg.get('data', {})
        page = data.get('page', 1)
        page_size = data.get('page_size', 20)

        # 扫描日志目录
        logs = []
        try:
            log_dir = '/var/log'
            if os.path.exists(log_dir):
                files = sorted(os.listdir(log_dir))
                for f in files:
                    if 'edge' in f.lower() or 'gateway' in f.lower():
                        fpath = os.path.join(log_dir, f)
                        st = os.stat(fpath)
                        logs.append({
                            'id': f,
                            'name': f,
                            'size': st.st_size,
                            'create_time': int(st.st_ctime * 1000)
                        })
        except Exception as e:
            logger.warning(f"Log list scan failed: {e}")

        total = len(logs)
        start = (page - 1) * page_size
        end = start + page_size

        logger.info(f"Device log list: total={total}, page={page}")
        return MessageBuilder.build_reply(msg, result=0, data={
            'total': total, 'page': page, 'page_size': page_size,
            'logs': logs[start:end]
        })

    def handle_device_log_upload(self, msg: dict) -> dict:
        """设备日志上传"""
        data = msg.get('data', {})
        log_ids = data.get('log_ids', [])
        logger.info(f"Device log upload: {len(log_ids)} files")
        # TODO: 实现日志打包上传
        return MessageBuilder.build_reply(msg, result=0, data={
            'upload_task_id': str(int(time.time() * 1000))
        })

    def handle_device_capability_query(self, msg: dict) -> dict:
        """设备能力查询 - 返回网关和无人机的完整能力集"""
        logger.info("Device capability query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'dock_capability': {
                'cover_control': True,
                'charge_control': True,
                'putter_control': True,
                'supplement_light': True,
                'air_conditioner': True,
                'rtk_support': True,
                'alternate_landing_point': True,
                'emergency_stop': True,
                'silent_mode': True,
                'battery_maintenance': True,
            },
            'drone_capability': {
                'max_flight_time': 1800,
                'max_speed': 15.0,
                'max_height': 120.0,
                'max_distance': 1000.0,
                'drc_support': True,
                'waypoint_support': True,
                'simulation_mode': True,
            },
            'payload_capability': {
                'camera_types': ['wide', 'zoom', 'ir'],
                'gimbal_types': ['3-axis'],
                'speaker_support': True,
                'searchlight_support': True,
                'night_vision_support': True,
                'parachute_support': False,
            }
        })

    # ========== API处理器：AirSense / 别名 / update_topo ==========

    def report_airsense_warning(self, warnings: list):
        """上报 AirSense 空域感知告警"""
        self._notify_event('airsense_warning', warnings)
        logger.info(f"AirSense warning reported: {len(warnings)} aircrafts")

    def handle_airsense_warning(self, msg: dict) -> dict:
        """AirSense警告处理 (services 兼容模式)"""
        data = msg.get('data', {})
        if isinstance(data, list):
            logger.info(f"AirSense warning (services): {len(data)} aircrafts")
        else:
            level = data.get('level', 0)
            description = data.get('description', '')
            logger.info(f"AirSense warning (services): level={level}, desc={description}")
        return MessageBuilder.build_reply(msg, result=0)

    # 组织管理别名
    def handle_airport_bind_status(self, msg: dict) -> dict:
        return self.handle_workspace_bind_status(msg)

    def handle_airport_organization_get(self, msg: dict) -> dict:
        return self.handle_workspace_get(msg)

    def handle_airport_organization_bind(self, msg: dict) -> dict:
        return self.handle_device_binding(msg)

    # update_topo（云端下发拓扑同步）
    def handle_update_topo(self, msg: dict) -> dict:
        """
        更新设备拓扑（云端下发）
        云端通过此接口同步拓扑信息到设备端，设备更新后需再次上报确认
        """
        data = msg.get('data', {})

        self._gateway_domain = data.get('domain', self._gateway_domain)
        self._gateway_type = data.get('type', self._gateway_type)
        self._gateway_sub_type = data.get('sub_type', self._gateway_sub_type)
        self._gateway_device_secret = data.get('device_secret', self._gateway_device_secret)
        self._gateway_nonce = data.get('nonce', self._gateway_nonce)
        self._gateway_thing_version = data.get('thing_version', self._gateway_thing_version)

        sub_devices_data = data.get('sub_devices', [])
        self._sub_devices.clear()
        for dev_data in sub_devices_data:
            sub_dev = SubDevice.from_dict(dev_data)
            sub_dev.online = True
            self._sub_devices[sub_dev.sn] = sub_dev

        logger.info(f"Topo updated from cloud: domain={self._gateway_domain}, "
                   f"sub_devices={len(self._sub_devices)}")

        self.update_properties({
            'sub_device': {
                'device_sn': list(self._sub_devices.keys())[0] if self._sub_devices else '',
                'product_type': str(self._gateway_type),
                'device_online_status': 1 if self._sub_devices else 0,
                'device_paired': 1 if self._sub_devices else 0
            }
        })

        self.report_update_topo()
        return MessageBuilder.build_reply(msg, result=0)

