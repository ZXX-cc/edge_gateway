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
import json
import shutil
import asyncio
import copy
import uuid
from typing import Dict, Any, Optional, Callable, List, Set, Tuple
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.command_trace import trace_command
from src.utils.message import MessageBuilder
from src.payload.dock3_payload import build_gimbal_payload_property, build_live_capacity_from_devices
from src.cloud_api.dock3_properties import (
    PROPERTY_SET_SUCCESS,
    official_property_names,
    validate_property_set,
    writable_property_names,
)
from src.cloud_api.dock3_device import (
    GATEWAY_DOMAIN,
    GATEWAY_SUB_TYPE,
    GATEWAY_TYPE,
    THING_VERSION,
    build_sub_device_payload,
    build_update_topo_payload,
    normalize_sub_device,
    validate_update_topo_payload,
)
from src.cloud_api.dock3_cmd import (
    TASK_STATUS_CANCELED,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_OK,
    TASK_STATUS_REJECTED,
    command_output,
    progress_event_data,
    validate_action_payload,
)
from src.cloud_api.dock3_hms_log import (
    TASK_STATUS_IN_PROGRESS as HMS_LOG_STATUS_IN_PROGRESS,
    TASK_STATUS_OK as HMS_LOG_STATUS_OK,
    TASK_STATUS_REJECTED as HMS_LOG_STATUS_REJECTED,
    hms_event_data,
    hms_list_data,
    normalize_alarm_ids,
    normalize_hms_alarms,
    normalize_page as normalize_hms_log_page,
    paginate as paginate_hms_log,
    resolve_log_root,
    scan_log_files,
    task_output as hms_log_task_output,
    upload_progress_event_data as hms_log_progress_event_data,
)
from src.utils.dji_error_codes import (
    DEVICE_STATE_UNSUPPORTED,
    DOCK_COMMUNICATION_ERROR,
    INVALID_COMMAND_FORMAT,
)
from src.database.connection import get_async_session_local, init_database_async
from src.database.device_db import DeviceDB
from src.database.alarm_db import AlarmDB
from src.database.operation_db import OperationDB
from src.device.nest_hms_mapper import (
    build_nest_hms_alarms,
    is_nest_hms_alarm,
    nest_hms_signature,
)
from web.mavlink.nest.property_mapping import build_dock3_properties_from_nest
from web.mavlink.properties.mapping import build_properties_from_osd, merge_properties

logger = get_logger("device")
alarm_logger = get_logger("alarm")
nest_plc_logger = get_logger("nest_plc")

NEST_COMMAND_TARGET_HOLD_SECONDS = 5.0


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
        return build_sub_device_payload(
            sn=self.sn,
            domain=self.domain,
            device_type=self.type,
            sub_type=self.sub_type,
            index=self.index,
            device_secret=self.device_secret,
            nonce=self.nonce,
            thing_version=self.thing_version,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "SubDevice":
        """从字典反序列化"""
        normalized = normalize_sub_device(data or {})
        device = cls(
            sn=normalized.get("sn", ""),
            domain=normalized.get("domain", "0"),
            device_type=normalized.get("type", 0),
            sub_type=normalized.get("sub_type", 0),
            index=normalized.get("index", "A"),
            device_secret=normalized.get("device_secret", ""),
            nonce=normalized.get("nonce", ""),
            thing_version=normalized.get("thing_version", THING_VERSION),
        )
        if isinstance(data, dict):
            device.online = bool(data.get("online", False))
            device.paired = bool(data.get("paired", False))
            try:
                device.last_heartbeat = float(data.get("last_heartbeat", 0) or 0)
            except (TypeError, ValueError):
                device.last_heartbeat = 0
        return device


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
        self._cloud_conn = None            # CloudConnectionManager - 云端请求
        self._payload_state_provider = None # Direct payload state provider

        # ===== 组织绑定信息持久化 =====
        self._org_data_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'data', 'organization.json'
        )
        self._org_data = self._load_organization_data()
        # 绑定信息本地缓存结构：
        # { "organization": {...}, "device_bindings": {sn: {...}} }
        self._config_data_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'data', 'device_config.json'
        )

        # ===== 基础时间记录（必须在_init_properties之前定义） =====
        self._acc_time_start = time.time()
        self._first_power_on = int(time.time() * 1000)

        # ===== 设备属性表(物模型) =====
        # 以 DJI Dock 3 properties 为目标；仍保留部分历史/模拟字段，迁移进度见文档。
        # pushMode=0: OSD定频上报 (0.5Hz)
        # pushMode=1: State事件性上报
        self._properties: Dict[str, Any] = self._init_properties()

        # Dock3 properties 页面中 accessMode=rw 的顶层属性。
        # 其他机场动作通过 services method 修改，不允许云端直接 property/set。
        self._writable_props = sorted(writable_property_names())

        # ===== 拓扑信息 =====
        self._gateway_domain = str(Config.get('device.topology.domain', GATEWAY_DOMAIN) or GATEWAY_DOMAIN)
        self._gateway_type = self._config_int('device.topology.type', GATEWAY_TYPE)
        self._gateway_sub_type = self._config_int('device.topology.sub_type', GATEWAY_SUB_TYPE)
        self._gateway_device_secret = str(Config.get('device.topology.device_secret', '') or '')
        self._gateway_nonce = str(Config.get('device.topology.nonce', '') or '')
        self._gateway_thing_version = str(Config.get('device.topology.thing_version', THING_VERSION) or THING_VERSION)

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

        # ===== 配置更新缓存 =====
        config_data = self._load_config_data()
        self._config_store: Dict[str, Any] = config_data.get("config", {})
        self._config_revision = int(config_data.get("revision", 0) or 0)
        self._config_history: List[Dict[str, Any]] = config_data.get("history", [])

        # ===== 调试/仿真模式 =====
        self._debug_mode = False
        self._simulation_mode = False

        # ===== HMS告警缓存 =====
        self._hms_alarms: List[dict] = []
        self._hms_alarm_counter = 0
        self._last_nest_hms_signature = tuple()
        self._last_nest_operation_signature = ""
        self._pending_nest_command_targets: Dict[str, Dict[str, Any]] = {}
        self._topo_lock = threading.Lock()
        self._pending_update_topo: Dict[str, Dict[str, Any]] = {}
        self._topo_retry_limit = max(0, int(Config.get('device.topology.retry_limit', 2) or 0))
        self._topo_retry_delay = max(0.5, float(Config.get('device.topology.retry_delay', 3.0) or 3.0))

        # ===== 系统状态定时更新线程 =====
        self._system_update_thread: Optional[threading.Thread] = None
        self._system_update_running = False
        self._system_update_interval = 5.0   # 5秒查询一次系统状态

        # ===== 回调 =====
        self._event_callback: Optional[Callable] = None
        self._state_callback: Optional[Callable] = None

        # ===== 异步事件循环 =====
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        # ===== 数据库初始化标志 =====
        self._db_initialized = False

        # ===== 启动时加载持久化数据 =====
        self._load_persisted_data()

    @staticmethod
    def _config_int(key_path: str, default: int) -> int:
        value = Config.get(key_path, default)
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _gateway_topology_keys(self) -> Set[Tuple[str, int, int]]:
        return {(self._gateway_domain, self._gateway_type, self._gateway_sub_type)}

    def _sub_device_topology_keys(self) -> Set[Tuple[str, int, int]]:
        keys = {("0", 60, 0), ("0", 91, 0)}
        for device in self._sub_devices.values():
            try:
                keys.add((device.domain, int(device.type), int(device.sub_type)))
            except (TypeError, ValueError):
                continue
        return keys

    def set_event_loop(self, loop):
        """注入异步事件循环引用"""
        self._main_loop = loop

    def _init_properties(self) -> Dict[str, Any]:
        """初始化所有物模型属性（对齐DJI规范）"""
        return {
            # ---- 位置属性 (pushMode=0, 只读) ----
            'longitude': 0.0,
            'latitude': 0.0,
            'height': 0.0,           # 椭球高度(m)
            'heading': 0.0,          # 机场朝向角(-180..180°)，纯模拟阶段复用SITL航向
            'home_position_is_valid': 0,
            'self_converge_coordinate': {
                'longitude': 0.0,
                'latitude': 0.0,
                'height': 0.0,
            },

            # ---- RTK/图传拓扑 (pushMode=1, 只读) ----
            # 无真机阶段提供官方结构的明确“未标定/未连接”状态，不伪造硬件标识。
            'rtcm_info': {
                'mount_point': '',
                'port': '',
                'host': '',
                'rtcm_device_type': 1,
                'source_type': 0,
            },
            'wireless_link_topo': {
                'secret_code': [0] * 28,
                'center_node': {'sdr_id': 0, 'sn': ''},
                'leaf_nodes': [],
            },
            'dongle_infos': [],

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
                'sim_slot': 0,
                'sdr_quality': 0,
                '4g_quality': 0,
                '4g_uav_quality': 0,
                '4g_gnd_quality': 0,
                'sdr_freq_band': 0.0,
                '4g_freq_band': 0.0,
            },
            'esim': {'activated': 0, 'operator': 0, 'status': 'inactive'},

            # ---- 直播/媒体属性 (pushMode=1, 部分可写) ----
            'air_transfer_enable': False,    # rw: 空中回传
            'live_status': [],
            'live_capacity': self._build_live_capacity_property(),

            # ---- 电源/电量属性 ----
            'electricity': 100,                      # 总电量(%)
            'remain_flight_time': 0,                 # 剩余飞行时间(s)
            'remaining_power_for_return_home': 30,   # 历史配置字段，Dock3 property/set 不直接写
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

            # ---- 舱体属性 (services 控制，property/set 只读) ----
            'air_conditioner_mode': 0,       # 历史模拟字段，控制走 services
            'battery_store_mode': 1,         # Dock3 properties 为只读，控制走 services
            'alarm_state': 0,                # Dock3 properties 为只读，控制走 services
            'supplement_light_state': 0,     # 0:关闭,1:打开
            'battery_maintenance_mode': 0,   # 历史模拟字段，控制走 services
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
            'type_subtype_gimbalindex': self._build_gimbal_payload_property(),

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

    def set_cloud_connection(self, cloud_conn):
        """注入云端连接管理器（用于发起 requests/requests_reply）"""
        self._cloud_conn = cloud_conn
        logger.info("DeviceManager: Cloud connection manager injected")

    def set_payload_state_provider(self, provider: Callable):
        """Inject direct payload state provider for Dock3 payload properties."""
        self._payload_state_provider = provider
        self._refresh_payload_properties(notify=False)

    # ========== 持久化数据加载 ==========

    def _load_persisted_data(self):
        """启动时加载持久化数据"""
        device_config = Config.get('device', {})
        limits = device_config.get('limits', {})
        self._properties['remaining_power_for_return_home'] = limits.get(
            'return_home_power', 30)

        self._properties['activation_time'] = 0
        self._properties['job_number'] = 0

        configured_sub_devices = device_config.get('sub_devices', [])
        if isinstance(configured_sub_devices, list):
            for item in configured_sub_devices:
                if not isinstance(item, dict):
                    continue
                sub_device = SubDevice.from_dict(item)
                if not sub_device.sn:
                    continue
                sub_device.online = bool(item.get('online', sub_device.online))
                sub_device.paired = bool(item.get('paired', sub_device.paired))
                self._sub_devices[sub_device.sn] = sub_device
            if self._sub_devices:
                self._refresh_sub_device_property(notify=False)

    def _schedule_async(self, coro):
        """安全调度异步任务"""
        import asyncio
        if self._main_loop and self._main_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._main_loop)
        else:
            try:
                loop = asyncio.get_running_loop()
                asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError:
                if hasattr(coro, "close"):
                    coro.close()
                logger.warning("Cannot schedule async task: no running event loop")

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

    # ========== 数据库持久化 ==========

    async def _init_database(self):
        """初始化数据库表"""
        if self._db_initialized:
            return
        await init_database_async()
        await self._load_binding_from_db()
        await self._load_alarms_from_db()
        self._db_initialized = True
        logger.info("DeviceManager database initialized")

    async def _load_binding_from_db(self):
        """从数据库加载设备绑定信息"""
        AsyncSessionLocal = get_async_session_local()
        async with AsyncSessionLocal() as session:
            binding = await DeviceDB.get_device_binding(session, self.gateway_sn)
            if binding:
                self._device_state = self.STATE_BOUND if binding.is_bound else self.STATE_UNBOUND
                self._bound_workspace_id = binding.workspace_id or ""
                self._bound_workspace_name = binding.workspace_name or ""
                self._org_data['organization'] = {
                    'organization_id': binding.organization_id or "",
                    'organization_name': binding.organization_name or "",
                    'bind_time': binding.bind_time,
                    'device_binding_code': ""
                }
                logger.info(f"Loaded binding from DB: workspace={self._bound_workspace_id}")

    async def _save_binding_to_db(self):
        """保存设备绑定信息到数据库"""
        AsyncSessionLocal = get_async_session_local()
        async with AsyncSessionLocal() as session:
            await DeviceDB.upsert_device_binding(
                session,
                gateway_sn=self.gateway_sn,
                workspace_id=self._bound_workspace_id,
                workspace_name=self._bound_workspace_name,
                organization_id=self._org_data.get('organization', {}).get('organization_id', ''),
                organization_name=self._org_data.get('organization', {}).get('organization_name', ''),
                is_bound=1 if self._device_state >= self.STATE_BOUND else 0,
                bind_time=self._org_data.get('organization', {}).get('bind_time', 0)
            )
            await session.commit()
            logger.info(f"Saved binding to DB: bound={self._device_state >= self.STATE_BOUND}")

    async def _load_alarms_from_db(self):
        """从数据库加载告警信息"""
        AsyncSessionLocal = get_async_session_local()
        async with AsyncSessionLocal() as session:
            alarms = await AlarmDB.get_unread_alarms(session, self.gateway_sn)
            self._hms_alarms = [
                {
                    'id': alarm.alarm_id,
                    'level': alarm.alarm_level,
                    'content': alarm.alarm_content,
                    'type': alarm.alarm_type,
                    'create_time': alarm.create_time
                }
                for alarm in alarms
            ]
            logger.info(f"Loaded {len(self._hms_alarms)} alarms from DB")

    async def _save_alarm_to_db(self, alarm_id: str, alarm_level: int, alarm_content: str, **kwargs):
        """保存告警到数据库"""
        AsyncSessionLocal = get_async_session_local()
        async with AsyncSessionLocal() as session:
            await AlarmDB.create_alarm(
                session,
                gateway_sn=self.gateway_sn,
                alarm_id=alarm_id,
                alarm_level=alarm_level,
                alarm_content=alarm_content,
                **kwargs
            )
            await session.commit()
            logger.info(f"Saved alarm to DB: {alarm_id}")

    async def _clear_alarms_from_db(self, alarm_ids: List[str] = None):
        """从数据库清除告警"""
        AsyncSessionLocal = get_async_session_local()
        async with AsyncSessionLocal() as session:
            if alarm_ids:
                cleared = await AlarmDB.delete_alarms_by_ids(session, alarm_ids)
            else:
                cleared = await AlarmDB.delete_all_alarms(session, self.gateway_sn)
            await session.commit()
            logger.info(f"Cleared {cleared} alarms from DB")

    # ========== 组织绑定信息持久化 ==========

    def _load_organization_data(self) -> dict:
        """从本地文件加载组织绑定数据"""
        default_data = {
            "organization": {
                "organization_id": "",
                "organization_name": "",
                "bind_time": 0,
                "device_binding_code": ""
            },
            "device_bindings": {}
        }
        try:
            if os.path.exists(self._org_data_path):
                with open(self._org_data_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 合并默认值（防止文件格式不完整）
                    for key, value in default_data.items():
                        if key not in data:
                            data[key] = value
                    return data
        except Exception as e:
            logger.warning(f"Failed to load organization data: {e}")
        return default_data

    def _save_organization_data(self) -> bool:
        """保存组织绑定数据到本地文件"""
        try:
            os.makedirs(os.path.dirname(self._org_data_path), exist_ok=True)
            with open(self._org_data_path, 'w', encoding='utf-8') as f:
                json.dump(self._org_data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Failed to save organization data: {e}")
            return False

    def _load_config_data(self) -> dict:
        """加载本地配置更新状态，用于 config_get/config_rollback。"""
        default_data = {
            "revision": 0,
            "config": {},
            "history": [],
        }
        try:
            if os.path.exists(self._config_data_path):
                with open(self._config_data_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    return default_data
                config = data.get("config")
                history = data.get("history")
                return {
                    "revision": int(data.get("revision", 0) or 0),
                    "config": config if isinstance(config, dict) else {},
                    "history": history if isinstance(history, list) else [],
                }
        except Exception as e:
            logger.warning("Failed to load config data: %s", e)
        return default_data

    def _save_config_data(self) -> bool:
        """保存本地配置更新状态。"""
        try:
            os.makedirs(os.path.dirname(self._config_data_path), exist_ok=True)
            payload = {
                "revision": self._config_revision,
                "config": self._config_store,
                "history": self._config_history[-20:],
                "updated_at": int(time.time() * 1000),
            }
            with open(self._config_data_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error("Failed to save config data: %s", e)
            return False

    def _get_device_binding_info(self, sn: str) -> dict:
        """获取单个设备的绑定信息"""
        bindings = self._org_data.get('device_bindings', {})
        return bindings.get(sn, {
            'sn': sn,
            'device_model_key': '',
            'device_callsign': '',
            'is_bound': False,
            'organization_id': '',
            'organization_name': '',
            'bind_time': 0
        })

    def _set_device_binding_info(self, sn: str, info: dict):
        """设置单个设备的绑定信息"""
        if 'device_bindings' not in self._org_data:
            self._org_data['device_bindings'] = {}
        self._org_data['device_bindings'][sn] = info
        self._save_organization_data()

    def _set_organization_info(self, org_id: str, org_name: str,
                                bind_code: str = "", bind_time: int = 0):
        """设置组织信息"""
        self._org_data['organization'] = {
            'organization_id': org_id,
            'organization_name': org_name,
            'device_binding_code': bind_code,
            'bind_time': bind_time
        }
        self._save_organization_data()

    def _clear_organization_info(self):
        """清除组织绑定信息（解绑时调用）"""
        self._org_data['organization'] = {
            'organization_id': '',
            'organization_name': '',
            'device_binding_code': '',
            'bind_time': 0
        }
        # 清除 pending_organization 缓存
        if 'pending_organization' in self._org_data:
            del self._org_data['pending_organization']
        self._save_organization_data()

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

        result, normalized_value, reason = validate_property_set(name, value)
        if result != PROPERTY_SET_SUCCESS:
            logger.warning(
                "Property set rejected: name=%s, value=%r, reason=%s",
                name,
                value,
                reason,
            )
            return False

        old_value = self._properties[name]
        if old_value == normalized_value:
            return True

        self._properties[name] = normalized_value
        logger.info(f"Property updated: {name} = {normalized_value} (was {old_value})")

        if notify and self._state_callback:
            self._notify_state({'property': {name: normalized_value}})

        return True

    def get_all_properties(self) -> Dict[str, Any]:
        """获取所有属性（深拷贝）"""
        import copy
        return copy.deepcopy(self._properties)

    def build_dock3_dock_osd(
        self,
        wrap_value: bool = False,
        include_payload: bool = False,
    ) -> Dict[str, Any]:
        """
        构造 Dock3 机场 OSD。

        DJI Demo 页面按机场物模型判断舱盖、充电、作业模式等状态，网关
        SN 的 OSD 不能使用飞行器遥测结构。DJI Cloud API 示例直接在
        ``data`` 中携带原始属性值；仅在显式请求 ``wrap_value=True`` 时
        生成旧 Demo 所需的 ``{"value": ...}`` 兼容结构。
        """
        import copy

        # Official pushMode=0 properties only. The official catalog is the
        # single source of truth, preventing legacy/internal properties from
        # leaking into the Dock 3 OSD and pushMode=1 fields from being routed
        # to the wrong MQTT topic.
        dock_osd_fields = sorted(official_property_names(push_mode=0))

        osd = {
            field: copy.deepcopy(self._properties[field])
            for field in dock_osd_fields
            if field in self._properties
        }
        if include_payload:
            self._refresh_payload_properties(notify=False)
            if "type_subtype_gimbalindex" in self._properties:
                osd["type_subtype_gimbalindex"] = copy.deepcopy(
                    self._properties["type_subtype_gimbalindex"]
                )

        if not wrap_value:
            return osd
        return {field: {"value": value} for field, value in osd.items()}

    def _gimbal_payload_config(self) -> Dict[str, Any]:
        payload_config = Config.get_payload_config()
        devices = payload_config.get("devices", {}) if isinstance(payload_config, dict) else {}
        gimbal = devices.get("gimbal", {}) if isinstance(devices, dict) else {}
        return gimbal if isinstance(gimbal, dict) else {}

    def _build_gimbal_payload_property(self) -> Dict[str, Any]:
        state = {}
        provider = getattr(self, "_payload_state_provider", None)
        if provider:
            try:
                states = provider() or {}
                if isinstance(states, dict) and isinstance(states.get("gimbal"), dict):
                    state = states["gimbal"]
            except Exception as exc:
                logger.warning("Refresh gimbal payload property failed: %s", exc)
        return build_gimbal_payload_property(state, self._gimbal_payload_config())

    def _build_live_capacity_property(self) -> Dict[str, Any]:
        payload_config = Config.get_payload_config()
        devices = payload_config.get("devices", {}) if isinstance(payload_config, dict) else {}
        return build_live_capacity_from_devices(
            gateway_sn=getattr(self, "gateway_sn", Config.get_gateway_sn()),
            devices=devices if isinstance(devices, dict) else {},
            default_device_sn=self._default_live_capacity_device_sn(),
        )

    def _default_live_capacity_device_sn(self) -> str:
        sub_devices = getattr(self, "_sub_devices", {})
        selected = next((dev for dev in sub_devices.values() if dev.online), None)
        return selected.sn if selected else ""

    def _refresh_payload_properties(self, notify: bool = False) -> Dict[str, Any]:
        updates = {
            "type_subtype_gimbalindex": self._build_gimbal_payload_property(),
            "live_capacity": self._build_live_capacity_property(),
        }
        changed = {
            key: value
            for key, value in updates.items()
            if self._properties.get(key) != value
        }
        if changed:
            self._properties.update(changed)
            if notify and self._state_callback:
                self._notify_state({"property": changed})
        return updates

    def publish_live_capacity_state(self, reason: str = "") -> None:
        """Publish the Dock3 livestream ability once the payload topology is known."""
        updates = self._refresh_payload_properties(notify=False)
        live_capacity = updates.get("live_capacity", self._properties.get("live_capacity"))
        if live_capacity is None:
            return
        logger.info(
            "Live capacity state reported%s: %s",
            f": {reason}" if reason else "",
            json.dumps(live_capacity, ensure_ascii=False, separators=(",", ":")),
        )
        self._notify_state({"property": {"live_capacity": live_capacity}})

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
        properties = changed.get("property") if isinstance(changed, dict) else None
        if not isinstance(properties, dict):
            logger.debug("Skip non-property Dock3 state payload: %s", changed)
            return

        state_properties = {}
        official_state_names = official_property_names(push_mode=1)
        for name, value in properties.items():
            if name in official_state_names:
                state_properties[name] = value
            else:
                logger.debug("Keep pushMode=0 property on OSD only: %s", name)

        if state_properties and self._state_callback:
            self._state_callback({"property": state_properties})

    def _notify_event(self, method: str, data: dict):
        """触发事件上报 (Topic: thing/product/{sn}/events)"""
        event = None
        if self._event_callback:
            event = MessageBuilder.build_event(method, data, self.gateway_sn)
            self._event_callback(event)
        return event

    # ========== 数据源更新方法 ==========

    def update_from_telemetry(self, osd_data: dict):
        """
        从遥测处理器接收MAVLink遥测数据，更新OSD属性
        由 main.py 在 TelemetryProcessor.build_osd_data() 后调用
        """
        updates = build_properties_from_osd(osd_data)
        updates = self._merge_updates_with_current(updates)

        if updates:
            self.update_properties(updates, notify=False)  # OSD属性不触发state，由TelemetryProcessor定时推送

    def update_from_nest(self, nest_data: dict):
        """
        从机巢模块接收机巢环境/状态数据，更新OSD属性
        由 main.py 在 DroneNestModule 收到 DRONE_NEST_RESPONSE 后调用
        """
        updates = build_dock3_properties_from_nest(nest_data)
        nest_plc_logger.info(
            "Nest PLC state received: heartbeat=%s plc_heartbeat=%s drone_heartbeat=%s "
            "gate_state=%s error_code=%s",
            nest_data.get("nest_heartbeat"),
            nest_data.get("plc_heartbeat"),
            nest_data.get("drone_heartbeat"),
            nest_data.get("gate_state"),
            nest_data.get("error_code"),
        )
        updates = self._merge_updates_with_current(updates)
        updates = self._filter_pending_nest_command_targets(updates)
        state_updates = self._extract_state_updates(updates)
        changed_state = {
            key: value
            for key, value in state_updates.items()
            if self._properties.get(key) != value
        }

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
        if changed_state:
            self._notify_state({'property': changed_state})

        self._sync_nest_hms_alarms(nest_data)
        self._notify_nest_operation_if_needed(nest_data, changed_state)

    def _remember_nest_command_target(self, command_name: str, prop_name: str, prop_value: int):
        """Keep the desired command state ahead of stale nest telemetry snapshots."""
        self._pending_nest_command_targets[prop_name] = {
            "value": prop_value,
            "command": command_name,
            "timestamp": time.time(),
        }

    def _filter_pending_nest_command_targets(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        if not updates or not self._pending_nest_command_targets:
            return updates

        now = time.time()
        filtered = dict(updates)
        for prop_name, target in list(self._pending_nest_command_targets.items()):
            if now - float(target.get("timestamp", 0)) > NEST_COMMAND_TARGET_HOLD_SECONDS:
                del self._pending_nest_command_targets[prop_name]
                continue
            if prop_name not in filtered:
                continue

            target_value = target.get("value")
            incoming_value = filtered[prop_name]
            if incoming_value == target_value:
                del self._pending_nest_command_targets[prop_name]
                continue

            trace_command(
                "gateway_nest_command_pending_state_hold",
                method=target.get("command", ""),
                property=prop_name,
                target=target_value,
                incoming=incoming_value,
            )
            del filtered[prop_name]
        return filtered

    @staticmethod
    def _extract_state_updates(updates: Dict[str, Any]) -> Dict[str, Any]:
        """Return only official Dock3 pushMode=1 properties for state."""
        official_state_names = official_property_names(push_mode=1)
        return {
            key: value
            for key, value in updates.items()
            if key in official_state_names
        }

    def _sync_nest_hms_alarms(self, nest_data: dict):
        """Synchronize active nest status alarms into the HMS cache."""
        nest_alarms = build_nest_hms_alarms(nest_data, self.gateway_sn)
        signature = nest_hms_signature(nest_alarms)
        if signature == self._last_nest_hms_signature:
            return

        old_nest_alarm_ids = [
            str(alarm.get('alarm_id') or alarm.get('id'))
            for alarm in self._hms_alarms
            if is_nest_hms_alarm(alarm) and (alarm.get('alarm_id') or alarm.get('id'))
        ]
        new_nest_alarm_ids = {
            str(alarm.get('alarm_id') or alarm.get('id'))
            for alarm in nest_alarms
            if alarm.get('alarm_id') or alarm.get('id')
        }
        removed_alarm_ids = [
            alarm_id for alarm_id in old_nest_alarm_ids
            if alarm_id not in new_nest_alarm_ids
        ]

        non_nest_alarms = [
            alarm for alarm in self._hms_alarms
            if not is_nest_hms_alarm(alarm)
        ]
        self._hms_alarms = non_nest_alarms + nest_alarms
        self._last_nest_hms_signature = signature

        self._notify_event('hms', hms_event_data(self._hms_alarms))
        alarm_logger.info(
            "Nest HMS synchronized: active=%s, removed=%s",
            len(nest_alarms),
            len(removed_alarm_ids),
        )

        for alarm in nest_alarms:
            self._schedule_async(self._save_alarm_to_db(
                alarm_id=alarm.get('id', ''),
                alarm_level=alarm.get('level', 0),
                alarm_content=alarm.get('content', ''),
                alarm_type=alarm.get('type', 'nest_status'),
                create_time=alarm.get('create_time', int(time.time()))
            ))
        if removed_alarm_ids:
            self._schedule_async(self._clear_alarms_from_db(removed_alarm_ids))

    def _notify_nest_operation_if_needed(self, nest_data: dict, changed_state: Dict[str, Any]):
        """Emit a lightweight operation event when important nest state changes."""
        status_flags = {
            'nest_heartbeat': nest_data.get('nest_heartbeat'),
            'plc_heartbeat': nest_data.get('plc_heartbeat'),
            'drone_heartbeat': nest_data.get('drone_heartbeat'),
            'estop_state': bool(nest_data.get('estop_state')),
            'fault_state': bool(nest_data.get('fault_state')),
            'system_busy': bool(nest_data.get('system_busy')),
            'takeoff_allowed': bool(nest_data.get('takeoff_allowed')),
            'takeoff_in_progress': bool(nest_data.get('takeoff_in_progress')),
            'landing_in_progress': bool(nest_data.get('landing_in_progress')),
            'emergency_landing': bool(nest_data.get('emergency_landing')),
            'error_code': nest_data.get('error_code', 0),
        }
        signature = json.dumps(
            {
                'changed_state': changed_state,
                'status_flags': status_flags,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if signature == self._last_nest_operation_signature:
            return
        fault_or_activity = any(
            status_flags.get(key) for key in (
                'estop_state', 'fault_state', 'system_busy',
                'takeoff_in_progress', 'landing_in_progress',
                'emergency_landing', 'error_code',
            )
        ) or any(
            status_flags.get(key) is False for key in (
                'nest_heartbeat', 'plc_heartbeat', 'drone_heartbeat',
            )
        )
        if not changed_state and not fault_or_activity:
            self._last_nest_operation_signature = signature
            return

        self._last_nest_operation_signature = signature
        now_ms = int(time.time() * 1000)
        operation_id = f'nest-status-{now_ms}-{uuid.uuid4().hex[:8]}'
        event_data = {
            'operation_id': operation_id,
            'event_type': 'state_changed',
            'command': 'nest_status_sync',
            'timestamp': now_ms,
            'result': 0,
            'output': {'status': 'ok'},
            'changed_state': changed_state,
            'nest_status': status_flags,
        }
        self._notify_event('dock_operation', event_data)
        self._schedule_async(self._save_nest_operation_to_db(operation_id, event_data))

    async def _save_nest_operation_to_db(self, operation_id: str, event_data: Dict[str, Any]):
        """把机巢状态变化事件作为已完成操作写入 SQLite/MongoDB。"""
        try:
            session_factory = get_async_session_local()
            async with session_factory() as session:
                async with session.begin():
                    await OperationDB.create_operation_async(
                        session,
                        self.gateway_sn,
                        "nest_status_sync",
                        {
                            "changed_state": event_data.get("changed_state", {}),
                            "nest_status": event_data.get("nest_status", {}),
                        },
                        operator="nest_simulator",
                        operation_id=operation_id,
                    )
                    await OperationDB.complete_operation_async(
                        session,
                        operation_id,
                        {
                            "result": event_data.get("result", 0),
                            "output": event_data.get("output", {}),
                            "timestamp": event_data.get("timestamp"),
                        },
                    )
        except Exception as exc:
            logger.error("Failed to save nest status operation record: %s", exc, exc_info=True)

    def _merge_updates_with_current(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """把嵌套属性更新与当前属性深合并，避免局部字段互相覆盖。"""
        merged: Dict[str, Any] = {}
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(self._properties.get(key), dict):
                merged[key] = merge_properties(self._properties[key].copy(), value)
            else:
                merged[key] = value
        return merged

    def update_from_mission(self, event: dict):
        """
        同步航线任务状态到设备物模型

        Args:
            event: MissionManager 上报的事件字典，包含 method 和 data
        """
        method = event.get('method', '')
        data = event.get('data', {})
        updates = {}

        # 根据事件类型更新任务步骤码
        step_map = {
            'flighttask_prepare': 5,       # 任务空闲/准备中
            'flighttask_execute': 6,       # 执行任务
            'flighttask_progress': 6,      # 任务执行中
            'flighttask_pause': 7,         # 任务暂停
            'flighttask_resume': 8,        # 任务恢复
            'flighttask_ready': 5,         # 任务就绪
            'flighttask_undo': 9,          # 任务取消
            'return_home_info': 10,        # 返航中
            'flight_setup_exception_notify': 255,  # 异常
        }

        if method in step_map:
            updates['flighttask_step_code'] = step_map[method]

        # 根据任务状态更新 mode_code
        if method == 'flighttask_execute':
            updates['mode_code'] = 4  # 作业中
        elif method in ('flighttask_undo', 'return_home_info'):
            # 取消/返航时，检查是否还在作业中
            pass

        # 从 flighttask_progress 中提取更详细的步骤码
        if method == 'flighttask_progress':
            output = data.get('output', {})
            progress = output.get('progress', {})
            current_step = progress.get('current_step')
            if current_step is not None:
                updates['flighttask_step_code'] = current_step
            # 提取 wayline_mission_state 映射到 mode_code
            ext = output.get('ext', {})
            wayline_state = ext.get('wayline_mission_state')
            if wayline_state == 6:
                updates['mode_code'] = 4  # 作业中
            elif wayline_state == 9:
                updates['mode_code'] = 0  # 空闲

        if updates:
            self.update_properties(updates, notify=True)
            logger.debug(f"DeviceManager updated from mission: {updates}")

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

        # 累计运行时间是 Dock3 pushMode=1 属性，需要走 state；其余系统
        # 查询字段均为 pushMode=0，仅保留在 OSD。
        elapsed = int(time.time() - self._acc_time_start)
        acc_time = self._properties.get('acc_time', 0) + elapsed
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
        if acc_time != self._properties.get('acc_time'):
            self.update_properties({'acc_time': acc_time}, notify=True)

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
        self._refresh_wireless_link_topo()

    def _refresh_wireless_link_topo(self, notify: bool = True):
        """Build an official Dock 3 wireless topology from simulated topology.

        The simulator deliberately uses zero SDR IDs and an all-zero secret;
        those values mean that no real radio credentials were observed. Device
        SN relationships are still useful for validating the state schema.
        """
        selected = next((dev for dev in self._sub_devices.values() if dev.online), None)
        if not selected:
            selected = next(iter(self._sub_devices.values()), None)
        topology = {
            'secret_code': [0] * 28,
            'center_node': {
                'sdr_id': 0,
                'sn': selected.sn if selected else '',
            },
            'leaf_nodes': ([{
                'sdr_id': 0,
                'sn': self.gateway_sn,
                'control_source_index': 1,
            }] if selected else []),
        }
        if self._properties.get('wireless_link_topo') == topology:
            return
        self._properties['wireless_link_topo'] = topology
        if notify:
            self._notify_state({'property': {'wireless_link_topo': topology}})

    def _refresh_sub_device_property(self, notify: bool = True):
        """
        Refresh Dock3 state property for the most relevant child device.

        update_topo reports all online sub-devices through sys/status. The Dock3
        state property is a compact single-device snapshot, so prefer an online
        device and fall back to the first known offline device to preserve
        reconnect context.
        """
        selected = next((dev for dev in self._sub_devices.values() if dev.online), None)
        if not selected:
            selected = next(iter(self._sub_devices.values()), None)

        state = {
            'device_sn': selected.sn if selected else '',
            'product_type': str(selected.type) if selected else '',
            'device_online_status': 1 if selected and selected.online else 0,
            'device_paired': 1 if selected and selected.paired else 0,
        }
        if self._properties.get('sub_device') == state:
            self._refresh_wireless_link_topo(notify=notify)
            return
        self._properties['sub_device'] = state
        self._refresh_wireless_link_topo(notify=notify)
        if notify:
            self._notify_state({'property': {'sub_device': state}})

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
            self._refresh_sub_device_property()
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
            self._sub_device_topo_payload(dev) for dev in self._sub_devices.values()
            if dev.online  # Dock3 offline topology uses an empty sub_devices list.
        ]
        return build_update_topo_payload(
            domain=self._gateway_domain,
            device_type=self._gateway_type,
            sub_type=self._gateway_sub_type,
            device_secret=self._gateway_device_secret,
            nonce=self._gateway_nonce,
            thing_version=self._gateway_thing_version,
            sub_devices=sub_devices,
        )

    def _sub_device_topo_payload(self, dev: SubDevice) -> dict:
        return dev.to_dict()

    def report_update_topo(self):
        """主动上报设备拓扑（update_topo）"""
        payload = self.build_topo_payload()
        valid, errors = validate_update_topo_payload(
            payload,
            strict=True,
            gateway_topology_keys=self._gateway_topology_keys(),
            sub_device_topology_keys=self._sub_device_topology_keys(),
        )
        if not valid:
            logger.error("Update topo payload invalid: %s", "; ".join(errors))
            return False
        event = MessageBuilder.build(
            'update_topo',
            payload,
            self.gateway_sn,
            need_reply=True,
        )
        self._publish_update_topo_event(event)
        self._track_update_topo(event)
        logger.info(
            "Update topo reported: %s sub-devices, tid=%s",
            len(payload['sub_devices']),
            event.get('tid'),
        )
        self.publish_live_capacity_state("topology_update")
        return True

    def handle_update_topo_reply(self, method: str, msg: dict):
        """处理 update_topo 的云端回复 (status_reply)"""
        if method != 'update_topo':
            logger.warning(f"Unexpected status_reply method: {method}")
            return
        data = msg.get('data', {})
        result = data.get('result', -1)
        tid = msg.get('tid', '')
        with self._topo_lock:
            pending = self._pending_update_topo.get(tid)
            if pending and pending.get('timer'):
                pending['timer'].cancel()
        if result == 0:
            with self._topo_lock:
                self._pending_update_topo.pop(tid, None)
            logger.info("Update topo accepted by cloud, tid=%s", tid)
        else:
            logger.error(f"Update topo rejected by cloud, tid={tid}, result={result}")
            self._retry_update_topo_event(tid, pending, reason=f"rejected result={result}")

    def _publish_update_topo_event(self, event: dict):
        if self._event_callback:
            self._event_callback(event)

    def _track_update_topo(self, event: dict, attempt: int = 0):
        tid = event.get('tid')
        if not tid or self._topo_retry_limit <= 0:
            return
        timer = threading.Timer(
            self._topo_retry_delay,
            self._handle_update_topo_timeout,
            args=(tid,),
        )
        timer.daemon = True
        with self._topo_lock:
            previous = self._pending_update_topo.get(tid)
            if previous and previous.get('timer'):
                previous['timer'].cancel()
            self._pending_update_topo[tid] = {
                'event': copy.deepcopy(event),
                'attempt': attempt,
                'timer': timer,
            }
        timer.start()

    def _handle_update_topo_timeout(self, tid: str):
        with self._topo_lock:
            pending = self._pending_update_topo.get(tid)
            if not pending:
                return
            attempt = int(pending.get('attempt', 0))
            if attempt >= self._topo_retry_limit:
                self._pending_update_topo.pop(tid, None)
                logger.error(
                    "Update topo status_reply timeout after retries: tid=%s, attempts=%s",
                    tid,
                    attempt + 1,
                )
                return
            event = copy.deepcopy(pending.get('event', {}))
        event['timestamp'] = int(time.time() * 1000)
        logger.warning(
            "Update topo status_reply timeout, retrying: tid=%s, attempt=%s/%s",
            tid,
            attempt + 1,
            self._topo_retry_limit,
        )
        self._publish_update_topo_event(event)
        self._track_update_topo(event, attempt=attempt + 1)

    def _retry_update_topo_event(self, tid: str, pending: Optional[Dict[str, Any]], reason: str):
        if self._topo_retry_limit <= 0 or not pending:
            return
        attempt = int(pending.get('attempt', 0))
        if attempt >= self._topo_retry_limit:
            with self._topo_lock:
                self._pending_update_topo.pop(tid, None)
            logger.error(
                "Update topo retry limit reached: tid=%s, attempts=%s, reason=%s",
                tid,
                attempt + 1,
                reason,
            )
            return
        event = copy.deepcopy(pending.get('event', {}))
        event['timestamp'] = int(time.time() * 1000)
        logger.warning(
            "Update topo retrying: tid=%s, attempt=%s/%s, reason=%s",
            tid,
            attempt + 1,
            self._topo_retry_limit,
            reason,
        )
        self._publish_update_topo_event(event)
        self._track_update_topo(event, attempt=attempt + 1)

    # ========== 设备生命周期 ==========

    def bind_device(self, workspace_id: str, workspace_name: str,
                     org_id: str = "", org_name: str = "",
                     bind_code: str = "", callsign: str = "") -> bool:
        """
        设备绑定到工作空间/组织

        Args:
            workspace_id: 工作空间ID
            workspace_name: 工作空间名称
            org_id: 组织ID（组织管理用）
            org_name: 组织名称
            bind_code: 设备绑定码
            callsign: 设备在组织中的名称
        """
        bind_time = int(time.time() * 1000)

        # 更新设备生命周期状态
        self._device_state = self.STATE_BOUND
        self._bound_workspace_id = workspace_id
        self._bound_workspace_name = workspace_name

        # 更新组织绑定信息
        self._set_organization_info(org_id or workspace_id,
                                     org_name or workspace_name,
                                     bind_code, bind_time)

        # 更新网关自身的绑定信息
        self._set_device_binding_info(self.gateway_sn, {
            'sn': self.gateway_sn,
            'device_model_key': f"{self._gateway_domain}-{self._gateway_type}-{self._gateway_sub_type}",
            'device_callsign': callsign or self.gateway_sn,
            'is_bound': True,
            'organization_id': org_id or workspace_id,
            'organization_name': org_name or workspace_name,
            'bind_time': bind_time
        })

        logger.info(f"Device bound: workspace={workspace_id}, org={org_id}, code={bind_code}")
        self._notify_event('device_binding', {
            'workspace_id': workspace_id,
            'bind_status': 'success',
            'timestamp': bind_time
        })
        self.report_update_topo()

        self._schedule_async(self._save_binding_to_db())

        return True

    def unbind_device(self) -> bool:
        """设备解绑"""
        unbind_time = int(time.time() * 1000)
        known_sns = [self.gateway_sn] + list(self._sub_devices.keys())
        self._device_state = self.STATE_UNBOUND
        self._bound_workspace_id = ""
        self._bound_workspace_name = ""

        # 清除组织绑定信息
        self._org_data = {
            'organization': {
                'organization_id': '',
                'organization_name': '',
                'bind_time': 0,
                'device_binding_code': ''
            },
            'device_bindings': {}
        }
        self._save_organization_data()

        logger.info("Device unbound")
        for sn in known_sns:
            self._notify_event('device_unbinding', {
                'device_sn': sn,
                'bind_status': 'success',
                'timestamp': unbind_time
            })

        self._sub_devices.clear()
        self._refresh_sub_device_property()
        self.report_update_topo()

        self._schedule_async(self._save_binding_to_db())

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
            for dev in self._sub_devices.values():
                dev.online = False
            self._refresh_sub_device_property()
            logger.info("Device offline")
            self._notify_event('device_offline', {
                'online_status': 0,
                'timestamp': int(time.time() * 1000)
            })
            self.report_update_topo()

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

        Dock3 Request: { property_name: property_value, ... }
        Legacy Request: { properties: {name: value, ...} }
        Dock3 Response: { property_name: { state: { result: 0/1/2 } } }
        """
        data = msg.get('data', {})
        properties = data.get('properties', data)
        if not isinstance(properties, dict):
            properties = {}

        results = {}
        for name, value in properties.items():
            success = self.set_property(name, value)
            results[name] = 0 if success else 1

        return MessageBuilder.build_property_set_reply(msg, results)

    def handle_property_get(self, msg: dict) -> dict:
        """
        属性获取 (property_get)

        Request: { properties: [name1, name2, ...] }
        Response: { name1: value1, name2: value2, ... }
        """
        data = msg.get('data', {})
        props = data.get('properties', [])
        self._refresh_payload_properties(notify=False)
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
        当 file_url 使用 mock:// 或 data.simulate=true 时走可重复模拟流程；
        真实 URL 仍保留下载、校验和刷写脚本路径。
        """
        data = msg.get('data', {})
        devices = data.get('devices', [])

        if not isinstance(devices, list) or not devices:
            return self._build_ota_rejected(msg, "ota_create.devices must be non-empty array")

        if self._ota_state != 'idle':
            return self._build_ota_rejected(
                msg,
                f"OTA already in progress: {self._ota_state}",
                result=DEVICE_STATE_UNSUPPORTED,
            )

        # 取第一个设备作为当前升级目标
        device_info = devices[0]
        if not isinstance(device_info, dict):
            return self._build_ota_rejected(msg, "ota_create.devices[0] must be object")

        self._ota_target_version = (
            device_info.get('product_version')
            or device_info.get('firmware_version')
            or device_info.get('version')
            or data.get('product_version')
            or data.get('firmware_version')
            or ""
        )
        self._ota_package_url = (
            device_info.get('file_url')
            or device_info.get('url')
            or data.get('file_url')
            or data.get('url')
            or ""
        )
        try:
            package_size = int(device_info.get('file_size') or data.get('file_size') or 0)
        except (TypeError, ValueError):
            return self._build_ota_rejected(msg, "ota_create.file_size must be int")
        self._ota_md5 = device_info.get('md5', data.get('md5', ''))
        self._ota_file_name = device_info.get('file_name', data.get('file_name', ''))
        upgrade_type = device_info.get('firmware_upgrade_type', 3)
        simulate = bool(data.get('simulate') or data.get('simulation_mode'))
        if str(self._ota_package_url).startswith('mock://'):
            simulate = True
        auto_complete = bool(data.get('auto_complete', simulate))

        if not self._ota_target_version:
            return self._build_ota_rejected(msg, "ota_create product_version required")

        self._ota_state = 'checking'
        self._ota_progress = 0
        self._ota_task_id = str(data.get('task_id') or device_info.get('task_id') or int(time.time() * 1000))
        self._ota_cancel_flag = False

        self.update_properties({
            'firmware_upgrade_status': self._firmware_status_map('checking')
        })

        logger.info(f"OTA task created: target={self._ota_target_version}, "
                   f"url={self._ota_package_url}, type={upgrade_type}")

        if simulate:
            if auto_complete:
                self._ota_thread = threading.Thread(
                    target=self._ota_simulation_worker,
                    daemon=True
                )
                self._ota_thread.start()
            else:
                self._ota_state = 'upgrading'
                self.report_ota_progress(TASK_STATUS_IN_PROGRESS, 10, 'wait_for_upgrade')
                logger.info("OTA simulation waiting for cancel/next step: task_id=%s", self._ota_task_id)
        else:
            if not self._ota_package_url:
                self._ota_state = 'idle'
                self.update_properties({
                    'firmware_upgrade_status': self._firmware_status_map('idle')
                }, notify=False)
                return self._build_ota_rejected(msg, "ota_create.file_url required for real firmware upgrade")
            # 启动异步升级线程
            self._ota_thread = threading.Thread(
                target=self._ota_worker,
                args=(self._ota_package_url, package_size, self._ota_md5),
                daemon=True
            )
            self._ota_thread.start()

        return MessageBuilder.build_reply(msg, result=0, data=command_output(
            TASK_STATUS_IN_PROGRESS,
            extra={
                'task_id': self._ota_task_id,
                'target_version': self._ota_target_version,
                'firmware_upgrade_type': upgrade_type,
                'simulation_mode': simulate,
            },
        ))

    def handle_ota_cancel(self, msg: dict) -> dict:
        """固件升级取消 (ota_cancel)"""
        if self._ota_state == 'idle':
            return self._build_ota_rejected(
                msg,
                "No OTA in progress",
                result=DEVICE_STATE_UNSUPPORTED,
            )

        self._ota_cancel_flag = True
        self._ota_state = 'idle'
        self._ota_progress = 0
        self.update_properties({
            'firmware_upgrade_status': self._firmware_status_map('idle')
        })
        self._notify_event('ota_result', {
            'output': {'status': TASK_STATUS_CANCELED},
            'task_id': self._ota_task_id,
            'result': 0,
            'error_code': 0,
            'error_msg': '',
            'target_version': self._ota_target_version,
        })
        logger.info("OTA cancelled")
        return MessageBuilder.build_reply(msg, result=0, data=command_output(
            TASK_STATUS_CANCELED,
            extra={'task_id': self._ota_task_id},
        ))

    def _build_ota_rejected(
        self,
        msg: dict,
        reason: str,
        result: int = INVALID_COMMAND_FORMAT,
    ) -> dict:
        logger.warning("OTA request rejected: %s", reason)
        return MessageBuilder.build_reply(
            msg,
            result=result,
            data=command_output(TASK_STATUS_REJECTED),
            msg_str=reason,
        )

    def _ota_simulation_worker(self):
        """可重复的固件升级模拟流程，不依赖外网下载或真实刷写脚本。"""
        try:
            for progress, step in (
                (10, 'download_firmware'),
                (50, 'download_firmware'),
                (60, 'verify_firmware'),
                (95, 'upgrade_firmware'),
            ):
                if self._ota_cancel_flag:
                    raise Exception("OTA cancelled")
                self.report_ota_progress(TASK_STATUS_IN_PROGRESS, progress, step)
                time.sleep(0.05)

            if self._ota_cancel_flag:
                raise Exception("OTA cancelled")
            self._properties['firmware_version'] = self._ota_target_version or self._properties.get('firmware_version', '')
            self._properties['firmware_upgrade_status'] = self._firmware_status_map('completed')
            self.report_ota_progress(TASK_STATUS_OK, 100, 'upgrade_firmware')
            self.report_ota_result(True)
            logger.info("OTA simulation completed: version=%s", self._ota_target_version)
        except Exception as e:
            error_msg = str(e)
            logger.warning("OTA simulation stopped: %s", error_msg)
            if error_msg != "OTA cancelled":
                self._properties['firmware_upgrade_status'] = self._firmware_status_map('failed')
                self.report_ota_progress(TASK_STATUS_FAILED, self._ota_progress, 'upgrade_firmware')
                self.report_ota_result(False, error_code=-1, error_msg=error_msg)
        finally:
            if self._ota_state != 'idle':
                self._ota_state = 'idle'

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
            'output': {
                'status': TASK_STATUS_OK if success else TASK_STATUS_FAILED,
            },
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
            'verifying': 3, 'upgrading': 4, 'completed': 5, 'failed': 6,
            TASK_STATUS_IN_PROGRESS: 1,
            TASK_STATUS_OK: 5,
            TASK_STATUS_CANCELED: 0,
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
        config, reason = self._extract_config_update(msg.get('data', {}))
        if reason:
            return self._build_config_rejected(msg, reason)

        logger.info(f"Config update received: {config}")
        applied = {}
        failed = {}
        property_snapshot = {}
        before_config = copy.deepcopy(self._config_store)

        for key, value in config.items():
            key = str(key)
            if key in self._properties:
                if key in self._writable_props:
                    property_snapshot[key] = copy.deepcopy(self._properties.get(key))
                    if self.set_property(key, value, notify=False):
                        applied[key] = value
                        self._config_store[key] = value
                    else:
                        failed[key] = "invalid writable property value"
                else:
                    failed[key] = "read-only property"
            else:
                # 配置项不一定等同于物模型属性，未知项作为本地业务配置持久化。
                applied[key] = value
                self._config_store[key] = value

        status = TASK_STATUS_OK if not failed else TASK_STATUS_FAILED
        result = 0 if not failed else INVALID_COMMAND_FORMAT
        if applied:
            self._config_history.append({
                "revision": self._config_revision,
                "config": before_config,
                "properties": property_snapshot,
                "timestamp": int(time.time() * 1000),
            })
            self._config_history = self._config_history[-20:]
            self._config_revision += 1
            self._save_config_data()

        # 上报配置更新结果
        event_data = command_output(status, extra={
            'config_revision': self._config_revision,
            'applied_keys': list(applied.keys()),
            'failed_keys': list(failed.keys()),
            'failed': failed,
        })
        event_data['result'] = result
        self._notify_event('config_update_result', event_data)

        return MessageBuilder.build_reply(msg, result=result, data=command_output(status, extra={
            'config_revision': self._config_revision,
            'applied_keys': list(applied.keys()),
            'failed_keys': list(failed.keys()),
            'failed': failed,
        }))

    def handle_config_get(self, msg: dict) -> dict:
        """查询当前本地配置缓存。"""
        data = msg.get('data', {})
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return self._build_config_rejected(msg, "config_get data must be an object")

        keys = (
            data.get('keys')
            or data.get('config_keys')
            or data.get('config_names')
            or data.get('config')
            or []
        )
        if isinstance(keys, str):
            keys = [keys]
        if keys and not isinstance(keys, list):
            return self._build_config_rejected(msg, "config_get keys must be an array")

        if keys:
            selected = {
                str(key): copy.deepcopy(self._config_store[str(key)])
                for key in keys
                if str(key) in self._config_store
            }
        else:
            selected = copy.deepcopy(self._config_store)

        return MessageBuilder.build_reply(msg, result=0, data=command_output(
            TASK_STATUS_OK,
            extra={
                'config_revision': self._config_revision,
                'config': selected,
                'total': len(selected),
            },
        ))

    def handle_config_rollback(self, msg: dict) -> dict:
        """回滚到上一版本地配置。"""
        if not self._config_history:
            return self._build_config_rejected(
                msg,
                "No config history to rollback",
                result=DEVICE_STATE_UNSUPPORTED,
            )

        snapshot = self._config_history.pop()
        rolled_back_to_revision = int(snapshot.get("revision", 0) or 0)
        self._config_store = copy.deepcopy(snapshot.get("config", {}))
        for key, value in (snapshot.get("properties") or {}).items():
            if key in self._properties:
                self._properties[key] = value

        self._config_revision += 1
        self._save_config_data()
        data = command_output(TASK_STATUS_OK, extra={
            'config_revision': self._config_revision,
            'rolled_back_to_revision': rolled_back_to_revision,
            'config': copy.deepcopy(self._config_store),
        })
        data['result'] = 0
        self._notify_event('config_rollback_result', data)
        return MessageBuilder.build_reply(msg, result=0, data=data)

    def _extract_config_update(self, data: Any) -> tuple[dict, str]:
        """兼容 Dock3 config_update 常见 dict/list 载荷。"""
        if data is None:
            return {}, "config_update data must be an object"
        if not isinstance(data, dict):
            return {}, "config_update data must be an object"

        raw_config = None
        for key in ("config", "configs", "settings", "items"):
            if key in data:
                raw_config = data.get(key)
                break

        if raw_config is None:
            return {}, "config_update missing config"

        if isinstance(raw_config, dict):
            config = copy.deepcopy(raw_config)
        elif isinstance(raw_config, list):
            config = {}
            for index, item in enumerate(raw_config):
                if not isinstance(item, dict):
                    return {}, f"config_update configs[{index}] must be object"
                if "key" in item and "value" in item:
                    config[str(item["key"])] = item["value"]
                else:
                    for item_key, item_value in item.items():
                        config[str(item_key)] = item_value
        else:
            return {}, "config_update config must be object or array"

        if not config:
            return {}, "config_update config cannot be empty"
        return config, ""

    def _build_config_rejected(
        self,
        msg: dict,
        reason: str,
        result: int = INVALID_COMMAND_FORMAT,
    ) -> dict:
        logger.warning("Config request rejected: %s", reason)
        return MessageBuilder.build_reply(
            msg,
            result=result,
            data=command_output(TASK_STATUS_REJECTED),
            msg_str=reason,
        )

    def handle_config(self, msg: dict) -> dict:
        """
        config (requests/requests_reply 模式)
        设备主动请求云端配置（ntp/app_id/app_key/app_license）
        这里处理云端下发的配置响应（如果云端通过 services 下发）
        """
        data = msg.get('data', {})
        if isinstance(data, dict) and any(key in data for key in ("config", "configs", "settings", "items")):
            return self.handle_config_update(msg)
        return self.handle_config_get(msg)

    # ========== API处理器：设备绑定与组织 ==========

    def handle_device_binding(self, msg: dict) -> dict:
        """
        设备绑定 (device_binding)

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
        查询工作空间绑定状态 (workspace_bind_status)

        返回网关自身的绑定状态摘要
        """
        org = self._org_data.get('organization', {})
        return MessageBuilder.build_reply(msg, result=0, data={
            'is_bind': self._device_state >= self.STATE_BOUND,
            'workspace_id': self._bound_workspace_id,
            'workspace_name': self._bound_workspace_name,
            'organization_id': org.get('organization_id', ''),
            'organization_name': org.get('organization_name', ''),
            'bind_time': org.get('bind_time', 0)
        })

    def handle_airport_bind_status(self, msg: dict) -> dict:
        """
        查询机场绑定状态 (airport_bind_status)

        Direction: legacy down (services) - 仅保留本地兼容 smoke。
        Dock3 官方 organization 路径由设备主动发 requests，云端回 requests_reply。
        Request: { devices: [{sn: string}] }
        Response: { result, output: { bind_status: [...] } }
        """
        data = msg.get('data', {})
        devices = data.get('devices', [])
        
        bind_status = []
        for device in devices:
            sn = device.get('sn', '')
            is_bound = sn == self.gateway_sn and self._device_state >= self.STATE_BOUND
            bind_status.append({
                'sn': sn,
                'is_device_bind_organization': is_bound,
                'organization_id': self._org_data.get('organization', {}).get('organization_id', ''),
                'organization_name': self._org_data.get('organization', {}).get('organization_name', ''),
                'workspace_id': self._bound_workspace_id,
                'workspace_name': self._bound_workspace_name,
            })
        
        return MessageBuilder.build_reply(msg, result=0, data={
            'output': {
                'bind_status': bind_status
            }
        })

    def handle_workspace_get(self, msg: dict) -> dict:
        """
        获取工作空间信息 (workspace_get)

        返回当前绑定的组织/工作空间详细信息
        """
        org = self._org_data.get('organization', {})
        gateway_binding = self._get_device_binding_info(self.gateway_sn)

        return MessageBuilder.build_reply(msg, result=0, data={
            'workspace_id': self._bound_workspace_id,
            'workspace_name': self._bound_workspace_name,
            'organization_id': org.get('organization_id', ''),
            'organization_name': org.get('organization_name', ''),
            'device_callsign': gateway_binding.get('device_callsign', self.gateway_sn),
            'is_bound': gateway_binding.get('is_bound', False),
            'bind_time': gateway_binding.get('bind_time', 0)
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
            trace_command(
                "gateway_nest_command_fallback",
                method=command_name,
                property=prop_name,
                value=prop_value,
                reason="nest_module_not_available",
                simulation=False,
            )
            logger.warning(f"{command_name}: Nest module not available, MAVLink command not sent")
            return False

        try:
            from src.generated_dialect.gcs_order_protocol import GCSCommand

            cmd_map = {
                ('cover_state', 1): GCSCommand.DOOR_OPEN,
                ('cover_state', 0): GCSCommand.DOOR_CLOSE,
                ('charging_state', 1): GCSCommand.CHARGE_START,
                ('charging_state', 0): GCSCommand.CHARGE_STOP,
                ('putter_state', 1): GCSCommand.CLAMP_RELEASE,   # 推出=释放夹紧
                ('putter_state', 0): GCSCommand.CLAMP_TIGHTEN,   # 收回=夹紧
                ('supplement_light_state', 1): GCSCommand.LIGHT_ON,
                ('supplement_light_state', 0): GCSCommand.LIGHT_OFF,
            }

            cmd = cmd_map.get((prop_name, prop_value))
            if cmd:
                trace_command(
                    "gateway_nest_command_send",
                    method=command_name,
                    property=prop_name,
                    value=prop_value,
                    gcs_command=cmd.name,
                )
                self._nest_module.send_gcs_command(cmd)
                logger.info(f"{command_name}: Nest command sent -> {cmd.name}")
            else:
                trace_command(
                    "gateway_nest_command_unmapped",
                    method=command_name,
                    property=prop_name,
                    value=prop_value,
                )
                logger.warning(f"{command_name}: No nest command mapping for {prop_name}={prop_value}")
                return False

            self._remember_nest_command_target(command_name, prop_name, prop_value)
            return True

        except Exception as e:
            logger.error(f"{command_name}: Failed to send nest command: {e}")
            return False

    def _send_drone_power_command(self, command_name: str, power_on: bool) -> bool:
        """通过机巢协议下发飞机上下电；无机巢模块时失败。"""
        if not self._nest_module:
            trace_command(
                "gateway_nest_command_fallback",
                method=command_name,
                action="drone_power",
                power_on=power_on,
                reason="nest_module_not_available",
                simulation=False,
            )
            logger.warning("%s: Nest module not available, drone power command not sent", command_name)
            return False

        try:
            from src.generated_dialect.gcs_order_protocol import GCSCommand

            cmd = GCSCommand.DRONE_POWER_ON if power_on else GCSCommand.DRONE_POWER_OFF
            trace_command(
                "gateway_nest_command_send",
                method=command_name,
                action="drone_power",
                power_on=power_on,
                gcs_command=cmd.name,
            )
            self._nest_module.send_gcs_command(cmd)
            logger.info("%s: Nest command sent -> %s", command_name, cmd.name)
            return True
        except Exception as e:
            logger.error("%s: Failed to send drone power command: %s", command_name, e)
            return False

    def _build_cmd_reply(
        self,
        msg: dict,
        result: int = 0,
        status: str = TASK_STATUS_OK,
        msg_str: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> dict:
        return MessageBuilder.build_reply(
            msg,
            result=result,
            data=command_output(status=status, extra=extra),
            msg_str=msg_str,
        )

    def _notify_cmd_progress(
        self,
        method: str,
        status: str = TASK_STATUS_OK,
        percent: int = 100,
        result: int = 0,
    ):
        self._notify_event(
            method,
            progress_event_data(method, status=status, percent=percent, result=result),
        )

    def _build_nest_command_reply(self, msg: dict, command_name: str, sent: bool) -> dict:
        if sent:
            self._notify_cmd_progress(command_name)
            return self._build_cmd_reply(
                msg,
                result=0,
                status=TASK_STATUS_OK,
                extra={'action': command_name},
            )
        return self._build_cmd_reply(
            msg,
            result=DOCK_COMMUNICATION_ERROR,
            status=TASK_STATUS_FAILED,
            msg_str=f"{command_name}: MAVLink nest command not sent",
            extra={'action': command_name},
        )

    def _reject_cmd(self, msg: dict, reason: str, result: int = INVALID_COMMAND_FORMAT) -> dict:
        logger.warning("Dock3 cmd rejected: method=%s, reason=%s", msg.get('method'), reason)
        return self._build_cmd_reply(
            msg,
            result=result,
            status=TASK_STATUS_REJECTED,
            msg_str=reason,
        )

    def _real_device_command_unavailable(self, msg: dict, reason: str) -> dict:
        logger.warning(
            "Dock3 cmd real adapter unavailable: method=%s, reason=%s",
            msg.get('method'),
            reason,
        )
        return self._build_cmd_reply(
            msg,
            result=DOCK_COMMUNICATION_ERROR,
            status=TASK_STATUS_FAILED,
            msg_str=reason,
        )

    def _handle_enum_switch(
        self,
        msg: dict,
        updates: Dict[str, Any],
        extra: Optional[Dict[str, Any]] = None,
    ) -> dict:
        method = msg.get('method', '')
        valid, value, used_field, errors = validate_action_payload(method, msg.get('data', {}))
        if not valid:
            return self._reject_cmd(msg, "; ".join(errors))

        update_values = {}
        for prop_name, prop_value in updates.items():
            update_values[prop_name] = value if prop_value is None else prop_value
        self.update_properties(update_values)

        reply_extra = dict(extra or {})
        reply_extra[used_field or 'action'] = value
        if used_field == 'mode':
            reply_extra['link_workmode'] = value

        logger.info("Dock3 cmd switch applied: method=%s, %s=%s", method, used_field, value)
        return self._build_cmd_reply(msg, result=0, status=TASK_STATUS_OK, extra=reply_extra)

    def handle_cover_open(self, msg: dict) -> dict:
        """打开舱盖 - 通过机巢协议真实下发"""
        sent = self._send_nest_command('cover_open', 'cover_state', 1)
        return self._build_nest_command_reply(msg, 'cover_open', sent)

    def handle_cover_close(self, msg: dict) -> dict:
        """关闭舱盖 - 通过机巢协议真实下发"""
        sent = self._send_nest_command('cover_close', 'cover_state', 0)
        return self._build_nest_command_reply(msg, 'cover_close', sent)

    def handle_cover_force_close(self, msg: dict) -> dict:
        """
        强制关闭舱盖
        安全校验：仅在确认飞行器不在舱内时调用
        """
        drone_in = self._properties.get('drone_in_dock', 1)
        if drone_in == 1:
            logger.warning("Force close cover rejected: drone still in dock")
            return self._reject_cmd(
                msg,
                "Drone in dock, cannot force close cover",
                result=DEVICE_STATE_UNSUPPORTED,
            )
        sent = self._send_nest_command('cover_force_close', 'cover_state', 0)
        return self._build_nest_command_reply(msg, 'cover_force_close', sent)

    def handle_charge_open(self, msg: dict) -> dict:
        """开启充电 - 通过机巢协议真实下发"""
        sent = self._send_nest_command('charge_open', 'charging_state', 1)
        return self._build_nest_command_reply(msg, 'charge_open', sent)

    def handle_charge_close(self, msg: dict) -> dict:
        """关闭充电 - 通过机巢协议真实下发"""
        sent = self._send_nest_command('charge_close', 'charging_state', 0)
        return self._build_nest_command_reply(msg, 'charge_close', sent)

    def handle_putter_open(self, msg: dict) -> dict:
        """打开推杆 - 通过机巢协议真实下发"""
        sent = self._send_nest_command('putter_open', 'putter_state', 1)
        return self._build_nest_command_reply(msg, 'putter_open', sent)

    def handle_putter_close(self, msg: dict) -> dict:
        """关闭推杆 - 通过机巢协议真实下发"""
        sent = self._send_nest_command('putter_close', 'putter_state', 0)
        return self._build_nest_command_reply(msg, 'putter_close', sent)

    def handle_device_lock(self, msg: dict) -> dict:
        """设备锁定"""
        self.update_properties({'mode_code': 0})
        return MessageBuilder.build_reply(msg, result=0)

    def handle_device_unlock(self, msg: dict) -> dict:
        """设备解锁"""
        return MessageBuilder.build_reply(msg, result=0)

    # ========== API处理器：灯光/SDR/仿真/调试 ==========

    def handle_supplement_light_open(self, msg: dict) -> dict:
        """补光灯开 - 通过机巢协议真实下发"""
        sent = self._send_nest_command('supplement_light_open', 'supplement_light_state', 1)
        return self._build_nest_command_reply(msg, 'supplement_light_open', sent)

    def handle_supplement_light_close(self, msg: dict) -> dict:
        """补光灯关 - 通过机巢协议真实下发"""
        sent = self._send_nest_command('supplement_light_close', 'supplement_light_state', 0)
        return self._build_nest_command_reply(msg, 'supplement_light_close', sent)

    def handle_sdr_workmode_switch(self, msg: dict) -> dict:
        """SDR工作模式切换"""
        valid, mode, used_field, errors = validate_action_payload(
            'sdr_workmode_switch',
            msg.get('data', {}),
        )
        if not valid:
            return self._reject_cmd(msg, "; ".join(errors))

        return self._real_device_command_unavailable(
            msg,
            "sdr_workmode_switch requires a real wireless-link adapter",
        )

    def handle_sim_slot_switch(self, msg: dict) -> dict:
        """SIM/eSIM 卡槽切换，模拟记录当前卡槽。"""
        data = msg.get('data', {})
        sim_slot = data.get('sim_slot', data.get('slot', 0))
        if not isinstance(sim_slot, int) or isinstance(sim_slot, bool) or sim_slot not in (0, 1):
            return self._reject_cmd(msg, "sim_slot_switch.sim_slot out of range, allowed: 0,1")

        return self._real_device_command_unavailable(
            msg,
            "sim_slot_switch requires a real SIM/eSIM adapter",
        )

    def handle_esim_activate(self, msg: dict) -> dict:
        """eSIM 激活。无真实蜂窝适配器时不得伪造成功。"""
        return self._real_device_command_unavailable(
            msg,
            "esim_activate requires a real SIM/eSIM adapter",
        )

    def handle_esim_operator_switch(self, msg: dict) -> dict:
        """eSIM 运营商切换模拟入口。"""
        data = msg.get('data', {})
        operator = data.get('operator', 0)
        if not isinstance(operator, int) or isinstance(operator, bool) or operator < 0:
            return self._reject_cmd(msg, "esim_operator_switch.operator must be non-negative int")

        return self._real_device_command_unavailable(
            msg,
            "esim_operator_switch requires a real SIM/eSIM adapter",
        )

    def handle_debug_mode_open(self, msg: dict) -> dict:
        """调试模式开启"""
        self._debug_mode = True
        self.update_properties({'mode_code': 2})
        logger.info("Debug mode ON")
        return self._build_cmd_reply(msg, result=0, status=TASK_STATUS_OK)

    def handle_debug_mode_close(self, msg: dict) -> dict:
        """调试模式关闭"""
        self._debug_mode = False
        self.update_properties({'mode_code': 0})
        logger.info("Debug mode OFF")
        return self._build_cmd_reply(msg, result=0, status=TASK_STATUS_OK)

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
        valid, action, _, errors = validate_action_payload('battery_maintenance_switch', data)
        if not valid:
            return self._reject_cmd(msg, "; ".join(errors))
        return self._real_device_command_unavailable(
            msg,
            "battery_maintenance_switch requires a real battery maintenance adapter",
        )

    def handle_air_conditioner_mode_switch(self, msg: dict) -> dict:
        """
        机场空调工作模式切换
        action: 0=关闭 1=制冷 2=制热 3=自动
        """
        data = msg.get('data', {})
        valid, action, _, errors = validate_action_payload('air_conditioner_mode_switch', data)
        if not valid:
            return self._reject_cmd(msg, "; ".join(errors))
        return self._real_device_command_unavailable(
            msg,
            "air_conditioner_mode_switch requires a real air-conditioner adapter",
        )

    def handle_alarm_state_switch(self, msg: dict) -> dict:
        """
        机场声光报警开关
        action: 0=关闭 1=开启
        """
        data = msg.get('data', {})
        valid, action, _, errors = validate_action_payload('alarm_state_switch', data)
        if not valid:
            return self._reject_cmd(msg, "; ".join(errors))
        return self._real_device_command_unavailable(
            msg,
            "alarm_state_switch requires a real alarm adapter",
        )

    def handle_battery_store_mode_switch(self, msg: dict) -> dict:
        """
        电池运行模式切换
        action: 1=正常工作模式 2=存储模式
        """
        data = msg.get('data', {})
        valid, action, _, errors = validate_action_payload('battery_store_mode_switch', data)
        if not valid:
            return self._reject_cmd(msg, "; ".join(errors))
        return self._real_device_command_unavailable(
            msg,
            "battery_store_mode_switch requires a real battery mode adapter",
        )

    # ========== API处理器：RTK/降落点 ==========

    def handle_alternate_land_point_set(self, msg: dict) -> dict:
        """备用降落点设置"""
        data = msg.get('data', {})
        lat = data.get('latitude', 0)
        lon = data.get('longitude', 0)
        alt = data.get('altitude', 0)
        safe_height = data.get('safe_land_height', 0)
        logger.info(f"Alternate land point: ({lat}, {lon}, {alt})")

        self.update_properties({'alternate_land_point': {
            'latitude': lat,
            'longitude': lon,
            'safe_land_height': safe_height,
            'is_configured': 1,
            'height': alt
        }}, notify=False)

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

    def handle_rtk_calibration(self, msg: dict) -> dict:
        """一键 RTK 标定。无真实 RTK 标定适配器时不得伪造成功。"""
        data = msg.get('data', {})
        devices = data.get('devices', [])
        if devices is None:
            devices = []
        if not isinstance(devices, list):
            return self._reject_cmd(msg, "rtk_calibration.devices must be array")

        return self._real_device_command_unavailable(
            msg,
            "rtk_calibration requires a real RTK calibration adapter",
        )

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
        """
        logger.info("Drone power ON")
        sent = self._send_drone_power_command('drone_open', power_on=True)
        if not sent:
            return self._build_nest_command_reply(msg, 'drone_open', False)
        for dev in self._sub_devices.values():
            dev.online = True
            dev.paired = True
            dev.last_heartbeat = time.time()
        first_dev = next(iter(self._sub_devices.values()), None)
        if first_dev:
            self.update_properties({
                'sub_device': {
                    'device_sn': first_dev.sn,
                    'product_type': str(first_dev.type),
                    'device_online_status': 1,
                    'device_paired': 1 if first_dev.paired else 0
                }
            })
        self.report_update_topo()
        self._notify_cmd_progress('drone_open')
        return self._build_cmd_reply(msg, result=0, status=TASK_STATUS_OK)

    def handle_drone_close(self, msg: dict) -> dict:
        """
        飞行器关机
        """
        logger.info("Drone power OFF")
        sent = self._send_drone_power_command('drone_close', power_on=False)
        if not sent:
            return self._build_nest_command_reply(msg, 'drone_close', False)
        for dev in self._sub_devices.values():
            dev.online = False
        first_dev = next(iter(self._sub_devices.values()), None)
        if first_dev:
            self.update_properties({
                'sub_device': {
                    'device_sn': first_dev.sn,
                    'product_type': str(first_dev.type),
                    'device_online_status': 0,
                    'device_paired': 1 if first_dev.paired else 0
                }
            })
        self.report_update_topo()
        self._notify_cmd_progress('drone_close')
        return self._build_cmd_reply(msg, result=0, status=TASK_STATUS_OK)

    def handle_drone_format(self, msg: dict) -> dict:
        """飞行器数据格式化"""
        logger.info("Drone format")
        return self._real_device_command_unavailable(
            msg,
            "drone_format requires a real aircraft storage adapter",
        )

    def handle_device_format(self, msg: dict) -> dict:
        """设备格式化（网关本地存储格式化）"""
        method = msg.get('method') or 'device_format'
        logger.info("%s requested", method)
        allow_storage_format = bool(Config.get('device.allow_storage_format', False))
        media_path = Config.get('media.storage_path', '/data/media')
        if allow_storage_format:
            try:
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
                return self._build_cmd_reply(
                    msg,
                    result=DOCK_COMMUNICATION_ERROR,
                    status=TASK_STATUS_FAILED,
                    msg_str=f"Device format failed: {e}",
                )
        else:
            logger.warning(
                "%s rejected; set device.allow_storage_format=true only when real storage formatting is intended: %s",
                method,
                media_path,
            )
            return self._real_device_command_unavailable(
                msg,
                f"{method} requires device.allow_storage_format=true and a real storage target",
            )

        self._notify_cmd_progress(method)
        return self._build_cmd_reply(msg, result=0, status=TASK_STATUS_OK)

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
        默认拒绝，避免联调服务器被真实 reboot。
        如确需真实重启，配置 device.allow_system_reboot=true。
        """
        logger.info("Device reboot requested")

        allow_system_reboot = bool(Config.get('device.allow_system_reboot', False))
        reboot_delay = 3
        if allow_system_reboot:
            def do_reboot():
                time.sleep(reboot_delay)
                try:
                    os.system('sudo reboot')
                except Exception as e:
                    logger.error(f"Reboot failed: {e}")

            reboot_thread = threading.Thread(target=do_reboot, daemon=True)
            reboot_thread.start()
            logger.warning("System reboot thread scheduled by device_reboot")
        else:
            logger.warning("Device reboot rejected; set device.allow_system_reboot=true only for real reboot")
            return self._real_device_command_unavailable(
                msg,
                "device_reboot requires device.allow_system_reboot=true",
            )

        self._notify_cmd_progress('device_reboot')
        return self._build_cmd_reply(
            msg,
            result=0,
            status=TASK_STATUS_OK,
            extra={'reboot_delay': reboot_delay},
        )

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
        self._notify_cmd_progress('device_reset')
        return self._build_cmd_reply(msg, result=0, status=TASK_STATUS_OK)

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
        normalized = normalize_hms_alarms(alarms, self.gateway_sn)
        self._hms_alarms = normalized
        self._notify_event('hms', hms_event_data(normalized))
        alarm_logger.info("HMS reported: count=%s alarms=%s", len(normalized), normalized)

        for alarm in normalized:
            self._schedule_async(self._save_alarm_to_db(
                alarm_id=alarm.get('id', ''),
                alarm_level=alarm.get('level', 0),
                alarm_content=alarm.get('content', ''),
                alarm_type=alarm.get('type', 'hms'),
                create_time=alarm.get('create_time', int(time.time()))
            ))

    def handle_hms_get(self, msg: dict) -> dict:
        """
        获取HMS告警信息（services 模式，云端查询）
        返回当前缓存的告警列表
        """
        data = msg.get('data', {})
        page, page_size = normalize_hms_log_page(data)

        if data.get('simulate') or data.get('inject_alarm'):
            if str(Config.get('runtime.mode', 'test')).lower() == 'production':
                return MessageBuilder.build_reply(
                    msg,
                    result=INVALID_COMMAND_FORMAT,
                    data=hms_log_task_output(HMS_LOG_STATUS_REJECTED, {
                        'reason': 'HMS alarm injection is disabled in production',
                    }),
                    msg_str='HMS alarm injection is disabled in production',
                )
            simulated = normalize_hms_alarms(data.get('alarms', data.get('alarm')), self.gateway_sn)
            self._hms_alarms = simulated
            self._notify_event('hms', hms_event_data(simulated))

        total = len(self._hms_alarms)
        alarms = paginate_hms_log(self._hms_alarms, page, page_size)

        alarm_logger.info("HMS get: total=%s page=%s", total, page)
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=hms_list_data(alarms, page=page, page_size=page_size, total=total),
        )

    def handle_hms_clear(self, msg: dict) -> dict:
        """清除HMS告警（services 模式，云端下发）"""
        data = msg.get('data', {})
        alarm_ids = normalize_alarm_ids(data)
        alarm_logger.info("HMS clear: count=%s alarm_ids=%s", len(alarm_ids), alarm_ids)

        before_count = len(self._hms_alarms)
        if alarm_ids:
            self._hms_alarms = [a for a in self._hms_alarms if a.get('id') not in alarm_ids]
        else:
            self._hms_alarms = []

        self._schedule_async(self._clear_alarms_from_db(alarm_ids))
        self._notify_event('hms', hms_event_data(self._hms_alarms))

        return MessageBuilder.build_reply(msg, result=0, data={
            **hms_log_task_output(HMS_LOG_STATUS_OK),
            'cleared_count': before_count - len(self._hms_alarms),
            'alarm_ids': alarm_ids,
        })

    def handle_device_log_list_get(self, msg: dict) -> dict:
        """获取设备日志列表"""
        data = msg.get('data', {})
        page, page_size = normalize_hms_log_page(data)
        logs = []
        try:
            log_dir = resolve_log_root(Config.get('logging.dir', './logs'))
            logs = scan_log_files(log_dir, self.gateway_sn, module=3)
        except Exception as e:
            logger.warning(f"Log list scan failed: {e}")

        total = len(logs)
        selected = paginate_hms_log(logs, page, page_size)

        logger.info(f"Device log list: total={total}, page={page}")
        return MessageBuilder.build_reply(msg, result=0, data={
            **hms_log_task_output(HMS_LOG_STATUS_OK),
            'total': total,
            'page': page,
            'page_size': page_size,
            'logs': selected,
            'list': selected,
        })

    def handle_device_log_upload(self, msg: dict) -> dict:
        """设备日志上传"""
        data = msg.get('data', {})
        log_ids = data.get('log_ids', [])
        logger.info(f"Device log upload: {len(log_ids)} files")
        if not isinstance(log_ids, list) or not log_ids:
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=hms_log_task_output(HMS_LOG_STATUS_REJECTED, {
                    'errors': ['device_log_upload missing log_ids'],
                }),
                msg_str='device_log_upload missing log_ids',
            )

        normalized_log_ids = [str(item) for item in log_ids if item]
        log_dir = resolve_log_root(Config.get('logging.dir', './logs'))
        known_logs = scan_log_files(log_dir, self.gateway_sn, module=3)
        by_id = {item['file_id']: item for item in known_logs}
        selected = [by_id[item] for item in normalized_log_ids if item in by_id]
        if selected:
            self._notify_event(
                'fileupload_progress',
                hms_log_progress_event_data(
                    selected,
                    device_sn=self.gateway_sn,
                    status=HMS_LOG_STATUS_IN_PROGRESS,
                    progress=0,
                ),
            )

        return MessageBuilder.build_reply(msg, result=0, data=hms_log_task_output(
            HMS_LOG_STATUS_IN_PROGRESS,
            {
                'upload_task_id': str(int(time.time() * 1000)),
                'accepted_count': len(selected),
                'missing_ids': [item for item in normalized_log_ids if item not in by_id],
            },
        ))

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

    # ========== DJI 组织管理接口 (Organization) 完整实现 ==========

    # ========== DJI 组织管理接口 - 边缘端主动发起（requests/requests_reply） ==========
    #
    # 注意：这三个接口在 DJI 规范中的 Direction = up（设备→云端）
    # 属于 requests/requests_reply 模式，不是 services/services_reply 模式
    # 因此边缘端应该【主动发起请求】，而不是【被动等待云端下发】
    #
    # 正确调用方式：
    #   dm.query_bind_status_from_cloud([sn1, sn2], callback)
    #   dm.query_organization_from_cloud(bind_code, org_id, callback)
    #   dm.bind_to_organization(bind_devices, callback)
    #
    # 这些方法内部调用 CloudConnectionManager.send_request() 发送 requests
    # 云端通过 requests_reply 回复，由 handle_org_requests_reply() 处理

    def query_bind_status_from_cloud(self, device_sns: list = None,
                                      callback: Callable = None,
                                      timeout: int = 30) -> str:
        """
        主动向云端查询设备绑定状态 (airport_bind_status)

        边缘端调用场景：
        - 设备启动时，查询自身及子设备的绑定状态
        - 用户操作后，刷新绑定状态

        Args:
            device_sns: 要查询的设备SN列表，None则查询网关自身+子设备
            callback: 回调函数，签名为 (method, msg) -> None
            timeout: 云端回包超时时间（秒）

        Returns:
            tid: 事务ID，用于匹配响应
        """
        if not self._cloud_conn:
            logger.error("query_bind_status: CloudConnectionManager not available")
            return ""

        if not device_sns:
            device_sns = [self.gateway_sn]
            for dev in self._sub_devices.values():
                device_sns.append(dev.sn)

        data = {'devices': [{'sn': sn} for sn in device_sns]}

        def _on_reply(method: str, msg: dict):
            """处理云端回复"""
            self._handle_airport_bind_status_reply(msg)
            if callback:
                callback(method, msg)

        tid = self._cloud_conn.send_request('airport_bind_status', data, _on_reply, timeout=timeout)
        logger.info(f"Query bind status sent: tid={tid}, devices={len(device_sns)}")
        return tid

    def _handle_airport_bind_status_reply(self, msg: dict):
        """处理 airport_bind_status 的云端回复

        处理场景：
        - is_device_bind_organization=true: 设备已绑定，更新本地缓存
        - is_device_bind_organization=false: 设备未绑定/已解绑，清理本地缓存
        """
        data = msg.get('data', {})
        result = data.get('result', -1)
        output = data.get('output', {})
        bind_status = output.get('bind_status', [])

        if result != 0:
            logger.warning(f"Bind status query failed: result={result}")
            return

        topology_changed = False
        for item in bind_status:
            sn = item.get('sn', '')
            is_bound = item.get('is_device_bind_organization', False)
            org_id = item.get('organization_id', '')
            org_name = item.get('organization_name', '')
            callsign = item.get('device_callsign', sn)

            if sn == self.gateway_sn:
                if is_bound:
                    was_unbound = self._device_state < self.STATE_BOUND
                    org_changed = (
                        self._bound_workspace_id != org_id
                        or self._bound_workspace_name != org_name
                        or self._org_data.get('organization', {}).get('organization_id', '') != org_id
                    )
                    if was_unbound or org_changed:
                        bind_code = self._org_data.get('organization', {}).get('device_binding_code', '')
                        self._device_state = self.STATE_BOUND
                        self._bound_workspace_id = org_id
                        self._bound_workspace_name = org_name
                        self._set_organization_info(org_id, org_name, bind_code, bind_time=int(time.time()*1000))
                        topology_changed = True
                        logger.info(f"Gateway binding refreshed from cloud reply: org={org_id}")
                        if was_unbound:
                            self._notify_event('device_binding', {
                                'device_sn': sn,
                                'organization_id': org_id,
                                'bind_status': 'success',
                                'timestamp': int(time.time() * 1000)
                            })
                else:
                    # 网关已解绑（从云端确认）
                    if self._device_state >= self.STATE_BOUND:
                        logger.info(f"Gateway unbound detected from cloud: was org={self._bound_workspace_id}")
                        self._device_state = self.STATE_ONLINE
                        self._bound_workspace_id = ''
                        self._bound_workspace_name = ''
                        self._clear_organization_info()
                        topology_changed = True
                        self._notify_event('device_unbinding', {
                            'device_sn': sn,
                            'bind_status': 'success',
                            'timestamp': int(time.time() * 1000)
                        })

            elif sn in self._sub_devices:
                sub_device = self._sub_devices[sn]
                if sub_device.paired != bool(is_bound):
                    sub_device.paired = bool(is_bound)
                    topology_changed = True

            # 更新设备绑定缓存（不管是不是网关自身都更新）
            existing = self._get_device_binding_info(sn)
            existing.update({
                'is_bound': is_bound,
                'organization_id': org_id,
                'organization_name': org_name,
                'device_callsign': callsign
            })
            if not is_bound:
                existing['bind_time'] = 0
            self._set_device_binding_info(sn, existing)

        logger.info(f"Bind status reply processed: {len(bind_status)} devices")
        if topology_changed:
            self._refresh_sub_device_property()
            self.report_update_topo()

        self._schedule_async(self._save_binding_to_db())

    def query_organization_from_cloud(self, binding_code: str, org_id: str,
                                       callback: Callable = None,
                                       timeout: int = 30) -> str:
        """
        主动向云端查询组织信息 (airport_organization_get)

        边缘端调用场景：
        - 用户输入绑定码后，先查询对应的组织名称确认

        Args:
            binding_code: 设备绑定码
            org_id: 组织ID
            callback: 回调函数
            timeout: 云端回包超时时间（秒）

        Returns:
            tid: 事务ID
        """
        if not self._cloud_conn:
            logger.error("query_organization: CloudConnectionManager not available")
            return ""

        data = {'device_binding_code': binding_code, 'organization_id': org_id}

        def _on_reply(method: str, msg: dict):
            self._handle_airport_organization_get_reply(msg)
            if callback:
                callback(method, msg)

        tid = self._cloud_conn.send_request('airport_organization_get', data, _on_reply, timeout=timeout)
        logger.info(f"Query organization sent: tid={tid}, org={org_id}")
        return tid

    def _handle_airport_organization_get_reply(self, msg: dict):
        """处理 airport_organization_get 的云端回复

        查询到组织名称后，缓存到 _org_data['pending_organization']，
        供后续 bind_to_organization 时直接使用。
        """
        data = msg.get('data', {})
        result = data.get('result', -1)
        output = data.get('output', {})
        org_name = output.get('organization_name', '')

        if result == 0 and org_name:
            logger.info(f"Organization query success: name={org_name}")
            # 缓存组织名称，供后续绑定使用
            if 'pending_organization' not in self._org_data:
                self._org_data['pending_organization'] = {}
            self._org_data['pending_organization']['organization_name'] = org_name
            self._save_organization_data()
        else:
            logger.warning(f"Organization query failed: result={result}, name={org_name}")

    def bind_to_organization(self, bind_devices: list,
                              callback: Callable = None,
                              timeout: int = 30) -> str:
        """
        主动向云端发起设备绑定到组织 (airport_organization_bind)

        边缘端调用场景：
        - 用户确认绑定码和组织信息后，发起正式绑定请求

        Args:
            bind_devices: 绑定参数列表
                [{sn, device_binding_code, organization_id,
                  device_callsign, device_model_key}, ...]
            callback: 回调函数
            timeout: 云端回包超时时间（秒）

        Returns:
            tid: 事务ID
        """
        if not self._cloud_conn:
            logger.error("bind_to_organization: CloudConnectionManager not available")
            return ""

        if not bind_devices:
            logger.error("bind_to_organization: bind_devices is empty")
            return ""

        # 参数校验（device_model_key 为 DJI 文档要求的必填字段）
        for dev in bind_devices:
            if not dev.get('sn') or not dev.get('device_binding_code') or not dev.get('organization_id'):
                logger.error(f"bind_to_organization: invalid device params (missing sn/bind_code/org_id): {dev}")
                return ""
            if not dev.get('device_model_key'):
                logger.error(f"bind_to_organization: missing device_model_key for {dev.get('sn')}")
                return ""

        data = {'bind_devices': bind_devices}

        def _on_reply(method: str, msg: dict):
            self._handle_airport_organization_bind_reply(msg, bind_devices)
            if callback:
                callback(method, msg)

        tid = self._cloud_conn.send_request('airport_organization_bind', data, _on_reply, timeout=timeout)
        logger.info(f"Bind to organization sent: tid={tid}, devices={len(bind_devices)}")
        return tid

    def _handle_airport_organization_bind_reply(self, msg: dict, original_devices: list):
        """处理 airport_organization_bind 的云端回复

        DJI 文档说明：
        - result=0 且 err_infos 为空：全部绑定成功
        - result=0 且 err_infos 非空：部分成功（err_infos 中为失败的设备）
        - result≠0：整体请求失败
        """
        data = msg.get('data', {})
        result = data.get('result', -1)
        output = data.get('output', {})
        err_infos = output.get('err_infos', [])

        if result != 0:
            logger.error(f"Bind to organization failed: result={result}")
            return

        # 构建失败设备SN集合（用于过滤）
        failed_sns = {err.get('sn', '') for err in err_infos}
        if err_infos:
            logger.warning(f"Bind to organization partial success: "
                          f"{len(failed_sns)}/{len(original_devices)} devices failed, "
                          f"errors={err_infos}")

        bind_time = int(time.time() * 1000)
        success_count = 0

        for dev in original_devices:
            sn = dev.get('sn', '')
            if sn in failed_sns:
                # 该设备绑定失败，记录错误信息
                err_code = next((e.get('err_code', -1) for e in err_infos if e.get('sn') == sn), -1)
                err_msg = next((e.get('err_msg', '') for e in err_infos if e.get('sn') == sn), '')
                if sn in self._sub_devices:
                    self._sub_devices[sn].paired = False
                self._set_device_binding_info(sn, {
                    'sn': sn,
                    'device_model_key': dev.get('device_model_key', ''),
                    'device_callsign': dev.get('device_callsign', sn),
                    'is_bound': False,
                    'organization_id': dev.get('organization_id', ''),
                    'organization_name': '',
                    'bind_time': 0,
                    'last_err_code': err_code,
                    'last_err_msg': err_msg
                })
                logger.warning(f"Device bind failed: sn={sn}, err_code={err_code}, err_msg={err_msg}")
                continue

            org_id = dev.get('organization_id', '')
            bind_code = dev.get('device_binding_code', '')
            callsign = dev.get('device_callsign', sn)
            model_key = dev.get('device_model_key', '')

            # 优先使用 pending_organization 缓存的组织名称
            org_name = self._org_data.get('pending_organization', {}).get('organization_name', '')
            if not org_name:
                org_name = self._org_data.get('organization', {}).get('organization_name', org_id)

            # 更新本地绑定缓存
            self._set_device_binding_info(sn, {
                'sn': sn,
                'device_model_key': model_key,
                'device_callsign': callsign,
                'is_bound': True,
                'organization_id': org_id,
                'organization_name': org_name,
                'bind_time': bind_time
            })
            success_count += 1

            # 网关自身绑定联动
            if sn == self.gateway_sn:
                self._device_state = self.STATE_BOUND
                self._bound_workspace_id = org_id
                self._bound_workspace_name = org_name
                self._set_organization_info(org_id, org_name, bind_code, bind_time)
                self._notify_event('device_binding', {
                    'device_sn': sn,
                    'organization_id': org_id,
                    'bind_status': 'success',
                    'timestamp': bind_time
                })

            # 子设备绑定联动
            elif sn in self._sub_devices:
                self._sub_devices[sn].paired = True
                self._notify_event('device_binding', {
                    'device_sn': sn,
                    'organization_id': org_id,
                    'bind_status': 'success',
                    'timestamp': bind_time
                })

        logger.info(f"Bind to organization result: {success_count}/{len(original_devices)} devices bound successfully")
        self._refresh_sub_device_property()
        self.report_update_topo()

        self._schedule_async(self._save_binding_to_db())

    def auto_query_bind_status_on_startup(self):
        """
        设备启动时自动查询绑定状态

        边缘端调用场景：
        - main.py 中设备上线后，自动调用此方法查询绑定状态
        - 如果已绑定，上报设备在线；如果未绑定，等待后续操作
        """
        if self._device_state >= self.STATE_BOUND:
            logger.info("Device already bound, skip bind status query")
            self.report_update_topo()
            return

        if not self._cloud_conn:
            logger.warning("CloudConnectionManager not available, cannot query bind status")
            return

        if not self._cloud_conn.is_cloud_connected:
            logger.warning("MQTT not connected yet, will query bind status later")
            return

        logger.info("Auto querying bind status on startup...")
        self.query_bind_status_from_cloud()

    # update_topo（云端下发拓扑同步）
    def handle_update_topo(self, msg: dict) -> dict:
        """
        更新设备拓扑（云端下发）
        云端通过此接口同步拓扑信息到设备端，设备更新后需再次上报确认
        """
        data = msg.get('data', {})

        valid, errors = validate_update_topo_payload(
            data,
            strict=True,
            gateway_topology_keys=self._gateway_topology_keys(),
            sub_device_topology_keys=self._sub_device_topology_keys(),
        )
        if not valid:
            reason = "; ".join(errors)
            logger.warning("Rejected invalid update_topo payload: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        normalized = build_update_topo_payload(
            domain=data.get('domain', self._gateway_domain),
            device_type=data.get('type', self._gateway_type),
            sub_type=data.get('sub_type', self._gateway_sub_type),
            device_secret=data.get('device_secret', self._gateway_device_secret),
            nonce=data.get('nonce', self._gateway_nonce),
            thing_version=data.get('thing_version', self._gateway_thing_version),
            sub_devices=data.get('sub_devices', []),
        )

        self._gateway_domain = normalized.get('domain', self._gateway_domain)
        self._gateway_type = normalized.get('type', self._gateway_type)
        self._gateway_sub_type = normalized.get('sub_type', self._gateway_sub_type)
        self._gateway_device_secret = normalized.get('device_secret') or self._gateway_device_secret
        self._gateway_nonce = normalized.get('nonce') or self._gateway_nonce
        self._gateway_thing_version = normalized.get('thing_version') or self._gateway_thing_version

        incoming_sub_devices = normalized.get('sub_devices', [])
        seen_sns = set()
        for dev_data in incoming_sub_devices:
            sub_dev = SubDevice.from_dict(dev_data)
            sub_dev.online = True
            sub_dev.last_heartbeat = time.time()
            existing = self._sub_devices.get(sub_dev.sn)
            if existing:
                sub_dev.paired = existing.paired
            binding = self._get_device_binding_info(sub_dev.sn)
            if binding.get('is_bound'):
                sub_dev.paired = True
            self._sub_devices[sub_dev.sn] = sub_dev
            seen_sns.add(sub_dev.sn)

        for sn, sub_dev in self._sub_devices.items():
            if sn not in seen_sns:
                sub_dev.online = False

        logger.info(f"Topo updated from cloud: domain={self._gateway_domain}, "
                   f"online_sub_devices={len(incoming_sub_devices)}, "
                   f"known_sub_devices={len(self._sub_devices)}")

        self._refresh_sub_device_property()

        self.report_update_topo()
        return MessageBuilder.build_reply(msg, result=0)
