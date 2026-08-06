#!/usr/bin/env python3
"""
边缘网关主程序入口
树莓派 + NanoMQ + MAVLink 架构

架构说明:
- MQTT: 通过paho-mqtt连接本地NanoMQ Broker
- MAVLink: 通过pymavlink连接飞控
- 控制信令与视频流物理分离
- 各业务模块通过TopicRouter注册API处理器

启动流程:
1. 加载配置文件
2. 初始化日志
3. 连接NanoMQ
4. 连接MAVLink飞控
5. 注册API处理器
6. 启动主循环
"""

import sys
import time
import signal
import threading
import asyncio
from pathlib import Path
from typing import Any, Optional

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.config import Config, load_config
from src.utils.logger import init_logger, get_logger
from src.utils.message import MessageBuilder
from src.utils.production_readiness import validate_production_config
from src.utils.runtime_overrides import build_runtime_overrides
from src.utils.local_history_logger import LocalHistoryLogger
from src.utils.mission_platform_snapshot import (
    flush_latest_mission_platform_snapshot,
    publish_platform_event_with_latest_snapshot,
)
from src.mqtt.client import MQTTClient
from src.mqtt.topic_router import TopicRouter
from src.core.connection import MAVLinkConnection
from src.core.message_dispatcher import MessageDispatcher
from src.modules.flight_control import FlightControl
from src.modules.mode_control import ModeControl
from src.modules.telemetry_module import TelemetryModule
from src.modules.drone_nest_module import DroneNestModule
from src.modules.waypoint_manager import WaypointManager
from src.mavlink.telemetry import TelemetryProcessor
from src.device.manager import DeviceManager
from src.mission.manager import MissionManager
from src.mission.point_flight import PointFlightController
from src.payload.camera import CameraController
from src.payload.gimbal import GimbalController
from src.payload.third_party import ThirdPartyController
from src.payload.spectral_camera import SpectralCameraAdapter
from src.payload.topology import PayloadTopology
from src.media.manager import MediaManager
from src.drc.controller import DrcController
from src.cloud_api.dock_manager import DockManager
from src.cloud_api.connection_manager import CloudConnectionManager
from src.cloud_api.telemetry_reporter import TelemetryReporter
from src.cloud_api.capability_reporter import CapabilityReporter
from src.modules.log_manager import LogManager
from src.modules.flysafe_manager import FlySafeManager
from src.modules.flight_area_manager import FlightAreaManager
from src.modules.flight_area_device_applier import FlightAreaDeviceApplier
from src.modules.custom_data_manager import CustomDataManager
from src.modules.rtk_module import RTKModule
from src.modules.gimbal_control import GimbalControl
from src.database.retention_manager import DataRetentionManager
from src.database.cloud_sync_manager import CloudSyncManager
from src.database.connection import (
    SessionLocal,
    close_database,
    configure_database,
    get_database_backend,
    init_database,
)
from src.database.telemetry_db import TelemetryDB
from web.mavlink.telemetry.mode_code import derive_aircraft_mode_code_from_gateway_modules
from web.mavlink.nest.property_mapping import build_dock3_properties_from_nest
from src.state_machine import CommandAdmissionEngine, GatewayEventReducer, GatewayStateProvider

logger = get_logger("main")


def _json_safe(value: Any) -> Any:
    """Convert runtime diagnostics to values that MQTT JSON publishing accepts."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _finite_non_negative_number(*values: Any) -> float | None:
    parsed = None
    for value in values:
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        break
    if parsed is None or parsed != parsed or parsed < 0:
        return None
    return parsed


def _bounded_percent(*values: Any) -> int | None:
    parsed = _finite_non_negative_number(*values)
    if parsed is None:
        return None
    return max(0, min(100, int(parsed)))


def _normalize_udp_target_host(host: str) -> str:
    """Convert wildcard bind addresses to a usable UDP destination."""
    if host in ("0.0.0.0", "::", ""):
        return "127.0.0.1"
    return host


# 这些 handler 会递归扫描日志/媒体目录或执行本地删除。必须移出 Paho MQTT
# 网络回调线程；飞控、DRC 和机巢控制仍保持原有同步顺序，避免状态机并发。
BACKGROUND_IO_METHODS = frozenset({
    'fileupload_list',
    'fileupload_start',
    'device_log_list_get',
    'device_log_upload',
    'file_list_get',
    'file_delete',
    'adjust_upload_priority',
})

# Platform-to-gateway services exposure policy.  Keep it explicit: platform
# /services may only call official Dock3 methods or project-owned real-chain
# methods.  Legacy compatibility handlers can still be used internally, but are
# not exposed to cloud services.
ALLOWED_SERVICE_METHODS = frozenset({
    # Device / dock management documented by Dock3 or required by this project.
    'ota_create', 'ota_cancel',
    'config_update', 'config_get', 'config_rollback', 'property_get',
    'gateway_state_query',
    'device_reboot', 'cover_open', 'cover_close', 'cover_force_close',
    'charge_open', 'charge_close', 'putter_open', 'putter_close',
    'supplement_light_open', 'supplement_light_close',
    'sdr_workmode_switch', 'sim_slot_switch', 'esim_activate',
    'esim_operator_switch', 'alternate_land_point_set',
    'rtk_ref_points_set', 'rtk_calibration', 'rtk_ref_points_query',
    'device_format',
    'hms_get', 'hms_clear', 'device_log_list_get', 'device_log_upload',
    'drone_open', 'drone_close', 'drone_format',
    'debug_mode_open', 'debug_mode_close', 'battery_maintenance_switch',
    'air_conditioner_mode_switch', 'alarm_state_switch',
    'battery_store_mode_switch',

    # Dock3 flighttask / wayline services plus project-owned orchestration.
    'one_key_takeoff_flow',
    'flighttask_prepare', 'flighttask_execute', 'flighttask_undo',
    'flighttask_stop', 'flighttask_return', 'flighttask_progress',
    'flighttask_progress_get', 'flighttask_pause', 'flighttask_resume',
    'resume_wayline_task', 'flight_setup_abort', 'flighttask_create',
    'flighttask_recovery', 'return_home', 'return_home_cancel',
    'return_specific_home', 'flighttask_resource_get',
    'in_flight_wayline_deliver', 'in_flight_wayline_stop',
    'in_flight_wayline_recover', 'in_flight_wayline_cancel',

    # Payload / media / live services documented on Dock3 pages.
    'gimbalRotate', 'camera_screen_drag', 'drc_light_calibration',
    'drc_light_fine_tuning_set', 'drc_speaker_tts_set',
    'drc_speaker_play_volume_set', 'payload_authority_release',
    'psdk_ui_resource_upload_result', 'psdk_floating_window_text',
    'psdk_widget_value_set', 'psdk_input_box_text_set', 'speaker_replay',
    'speaker_play_mode_set', 'speaker_play_stop', 'speaker_audio_play_start',
    'speaker_tts_play_start', 'speaker_play_volume_set',
    'file_list_get', 'file_download', 'file_delete',
    'adjust_upload_priority',
    'live_start', 'live_start_push', 'live_stop', 'live_stop_push',
    'live_camera_change',
    'live_capability_query', 'highest_priority_upload_flighttask_media',
    'upload_flighttask_media_prioritize', 'live_lens_change',
    'live_set_quality', 'camera_frame_zoom', 'camera_mode_switch',
    'camera_photo_take', 'camera_photo_stop', 'camera_recording_start',
    'camera_recording_stop', 'camera_focal_length_set',
    'camera_exposure_set', 'camera_focus_mode_set', 'camera_focus_value_set',
    'camera_point_focus_action', 'camera_exposure_mode_set', 'camera_aim',
    'camera_screen_split', 'gimbal_reset', 'camera_look_at',
    'photo_storage_set', 'video_storage_set', 'payload_authority_grab',
    'ir_metering_mode_set', 'ir_metering_point_set', 'ir_metering_area_set',

    # Services-topic DRC session and point-flight methods.
    'drc_mode_enter', 'drc_mode_exit', 'flight_authority_grab',
    'takeoff_to_point', 'fly_to_point', 'fly_to_point_stop',
    'fly_to_point_update',

    # Log / flysafe / flight-area / custom-data services.
    'fileupload_list', 'fileupload_start', 'fileupload_update',
    'unlock_license_switch', 'unlock_license_update', 'unlock_license_list',
    'flight_areas_update',
    'custom_data_transmission_from_esdk', 'custom_data_transmission_to_esdk',
    'custom_data_transmission_from_psdk', 'custom_data_transmission_to_psdk',
})

IGNORED_NON_SERVICE_METHODS = frozenset({
    # Official organization binding is requests/requests_reply, topology is
    # status/status_reply, and device_binding/device_unbinding are internal
    # binding-cache/event semantics.  If these arrive on /services, do not emit
    # a services_reply that makes them look like exposed cloud services.
    'airport_bind_status',
    'airport_organization_get',
    'airport_organization_bind',
    'device_binding',
    'device_unbinding',
    'workspace_bind_status',
    'workspace_get',
    'storage_config_get',
    'flight_areas_get',
    'device_capability_query',
    'update_topo',
})

DOWNSTREAM_REPLY_REQUIRED_METHODS = frozenset({'property_set'}) | ALLOWED_SERVICE_METHODS

def filter_dock_status_fields(updates: dict) -> dict:
    """把 Dock3 机巢属性适配到当前 DockManager 状态缓存。"""
    dock_updates = {}
    direct_fields = {
        "cover_state",
        "putter_state",
        "drone_in_dock",
        "environment_temperature",
        "wind_speed",
        "rainfall",
        "humidity",
        "supplement_light_state",
        "alarm_state",
    }
    for key in direct_fields:
        if key in updates:
            dock_updates[key] = updates[key]

    if "temperature" in updates:
        dock_updates["nest_temperature"] = updates["temperature"]
    charge_state = updates.get("drone_charge_state")
    if isinstance(charge_state, dict) and "state" in charge_state:
        dock_updates["charging_state"] = charge_state["state"]

    return dock_updates


class EdgeGateway:
    """边缘网关主控"""

    def __init__(self):
        self.running = False
        self.config = None
        self.loop = None

        # 核心组件
        self.mqtt: MQTTClient = None
        self.router: TopicRouter = None

        # MAVLink新架构（core + modules）
        self.mavlink_conn = None
        self.dispatcher = None
        self.nest_mavlink_conn = None
        self.nest_dispatcher = None
        self.flight_control = None
        self.mode_control = None
        self.telemetry_module = None
        self.nest_module = None
        self.gimbal_control = None
        self.spectral_camera = None
        self.payload_config = None
        self.payload_topology = None
        self._gimbal_auto_connect_control = None
        self._gimbal_auto_connect_started = False
        self._gimbal_auto_connect_lock = threading.Lock()

        # 遥测处理
        self.telemetry: TelemetryProcessor = None
        self.device: DeviceManager = None
        self.mission: MissionManager = None
        self.camera: CameraController = None
        self.gimbal: GimbalController = None
        self.third_party: ThirdPartyController = None
        self.media: MediaManager = None
        self.drc: DrcController = None
        self.point_flight: PointFlightController = None
        self.dock: DockManager = None
        self.cloud_conn: CloudConnectionManager = None
        self.telemetry_reporter: TelemetryReporter = None
        self._dock_osd_interval = 2.0
        self._last_dock_osd_report_time = 0.0
        self.capability_reporter: CapabilityReporter = None
        self.log_mgr: LogManager = None
        self.flysafe: FlySafeManager = None
        self.flight_area: FlightAreaManager = None
        self.flight_area_applier: FlightAreaDeviceApplier = None
        self.custom_data: CustomDataManager = None
        self.waypoint_manager: WaypointManager = None
        self.rtk_module: RTKModule = None
        self.global_state: GatewayEventReducer = None
        self.command_admission: CommandAdmissionEngine = None
        self.history_logger: LocalHistoryLogger = None
        self._startup_connections_ready = False
        self._organization_binding_retry_timer = None
        self._organization_binding_retry_attempt = 0
        self._organization_binding_pending_tid = ""
        self._organization_binding_pending_stage = ""

    def initialize(self, config_path: str = "./config/gateway.yaml", runtime_overrides: dict = None) -> bool:
        """初始化网关"""
        try:
            # 1. 加载配置
            logger.info("Loading configuration...")
            self.config = load_config(config_path)
            if runtime_overrides:
                Config.merge(runtime_overrides)
                self.config = Config.all()

            # 数据库必须在业务模块初始化前完成后端选择；SessionLocal 是稳定代理，
            # 后续同步/异步调用都会落到这里选定的 MongoDB 或 SQLite。
            configure_database(Config.get_database_config())
            self.history_logger = LocalHistoryLogger()

            # 2. 初始化日志
            log_config = Config.get('logging', {})
            init_logger(log_config)
            logger.info("Logger initialized")
            logger.info("Configuration loaded: path=%s", config_path)

            production_errors = validate_production_config(Config.all())
            if production_errors:
                for error in production_errors:
                    logger.error("Production readiness failed: %s", error)
                return False

            # 3. 初始化MQTT客户端
            logger.info("Initializing MQTT client...")
            self.mqtt = MQTTClient()

            # 4. 初始化Topic路由器
            logger.info("Initializing topic router...")
            self.router = TopicRouter()

            # 5. 初始化遥测处理器
            logger.info("Initializing telemetry processor...")
            self.telemetry = TelemetryProcessor()
            self._dock_osd_interval = self.telemetry._osd_interval

            # 6. 初始化业务模块
            logger.info("Initializing business modules...")
            self.device = DeviceManager()
            self.mission = MissionManager()
            self.camera = CameraController()
            self.gimbal = GimbalController()
            self.third_party = ThirdPartyController()
            self.media = MediaManager()
            self.media.set_camera_controller(self.camera)
            self.drc = DrcController()
            self.point_flight = PointFlightController(self.drc)
            self.drc.set_point_flight_controller(self.point_flight)
            self.dock = DockManager()
            self.log_mgr = LogManager()
            self.flysafe = FlySafeManager()
            self.flight_area = FlightAreaManager()
            self.flight_area_applier = FlightAreaDeviceApplier()
            self.flight_area.set_device_applier(self.flight_area_applier)
            self.custom_data = CustomDataManager()

            # 负载链路独立于飞控/机巢 MAVLink，在业务模块创建后立即装配。
            self._configure_payload_connections()

            # 6.1 初始化数据保留管理器（边缘端专用）
            self.retention_manager = DataRetentionManager()

            # 6.2 初始化云端同步管理器（混合存储架构）
            self.cloud_sync_manager = CloudSyncManager()
            init_database()
            logger.info("Database backend initialized: %s", get_database_backend())

            # 7. 初始化RTK模块（延迟到start中，因为需要telemetry_module）

            # 8. 初始化云端连接管理
            self.cloud_conn = CloudConnectionManager(self.mqtt)
            self.telemetry_reporter = TelemetryReporter(self.mqtt)
            self.capability_reporter = CapabilityReporter(self.mqtt)
            self.device.set_cloud_connection(self.cloud_conn)

            # 8.1 初始化全局状态机/命令准入层。具体 MAVLink/机巢源会在 start()
            # 中创建后再次注入，初始化阶段先覆盖 MQTT、设备、任务、DRC、Dock。
            self.global_state = GatewayEventReducer(
                GatewayStateProvider(
                    mqtt=self.mqtt,
                    device=self.device,
                    mission=self.mission,
                    drc=self.drc,
                    dock=self.dock,
                    telemetry=self.telemetry,
                )
            )
            self.command_admission = CommandAdmissionEngine(self.global_state)
            self.mqtt.set_command_admission(self.command_admission)

            # 9. 设置回调
            self._setup_callbacks()

            # 8. 注册API处理器
            self._register_handlers()

            # 9. MAVLink连接和模块在start()中建立

            logger.info("Initialization complete")
            return True

        except Exception as e:
            logger.error(f"Initialization failed: {e}", exc_info=True)
            return False

    def _auto_bind_gateway_for_test_runtime(self) -> bool:
        """Bind the gateway automatically in test mode so local payload checks can run directly."""
        if str(Config.get('runtime.mode', 'test')).lower() == 'production':
            return False
        if not bool(Config.get('device.auto_bind_on_startup', True)):
            return False
        workspace_id = str(Config.get('device.auto_bind_workspace_id', 'local_test_workspace') or 'local_test_workspace')
        workspace_name = str(Config.get('device.auto_bind_workspace_name', '本地测试工作空间') or '本地测试工作空间')
        state = self.device.get_device_state() if self.device else {}
        if state.get('state') in {'bound', 'online'}:
            return False
        logger.info("Auto binding gateway for test runtime: workspace_id=%s", workspace_id)
        return self.device.bind_device(workspace_id, workspace_name)

    def _start_configured_organization_binding(self) -> bool:
        """启动配置驱动的 Dock3 上云绑定链路。

        上云绑定先向平台查询当前绑定状态；只有平台明确返回未绑定的设备，
        才继续执行组织查询和正式绑定，避免启动时重复绑定已绑定设备。
        """
        cfg = Config.get('device.organization_binding', {}) or {}
        if not bool(cfg.get('enabled', False)):
            return False

        def _retry_settings() -> tuple[int, float]:
            # 组织绑定重试属于网关内部容错策略，不暴露到客户 YAML；
            # 如确需调试，可通过未公开配置键覆盖，默认 5 次、失败回包间隔 30 秒。
            try:
                retry_limit = max(0, int(cfg.get('retry_limit', 5) or 0))
            except (TypeError, ValueError):
                retry_limit = 5
            try:
                retry_interval = max(1.0, float(cfg.get('retry_interval_seconds', 30.0) or 30.0))
            except (TypeError, ValueError):
                retry_interval = 30.0
            return retry_limit, retry_interval

        def _clear_retry_timer():
            timer = self._organization_binding_retry_timer
            self._organization_binding_retry_timer = None
            if timer:
                timer.cancel()

        def _schedule_retry(reason: str, *, delay: float = None):
            retry_limit, retry_interval = _retry_settings()
            if retry_limit <= 0:
                logger.warning("Configured organization binding retry disabled: reason=%s", reason)
                return
            if self._organization_binding_retry_attempt >= retry_limit:
                logger.error(
                    "Configured organization binding retry limit reached: attempts=%s, reason=%s",
                    self._organization_binding_retry_attempt,
                    reason,
                )
                return
            if self._organization_binding_retry_timer:
                return
            self._organization_binding_retry_attempt += 1
            attempt = self._organization_binding_retry_attempt
            retry_delay = retry_interval if delay is None else max(0.0, float(delay))
            logger.warning(
                "Configured organization binding will retry in %.1fs: attempt=%s/%s, reason=%s",
                retry_delay,
                attempt,
                retry_limit,
                reason,
            )
            timer = threading.Timer(retry_delay, self._retry_configured_organization_binding)
            timer.daemon = True
            self._organization_binding_retry_timer = timer
            timer.start()

        def _mark_pending(stage: str, tid: str):
            self._organization_binding_pending_stage = stage
            self._organization_binding_pending_tid = tid or ""

        def _clear_pending(tid: str = ""):
            if not tid or tid == self._organization_binding_pending_tid:
                self._organization_binding_pending_stage = ""
                self._organization_binding_pending_tid = ""

        def _watch_request_timeout(stage: str, tid: str):
            if not tid:
                return

            def _on_timeout():
                if (
                    self._organization_binding_pending_tid == tid
                    and self._organization_binding_pending_stage == stage
                ):
                    _clear_pending(tid)
                    # 平台无回复时，到达内部超时阈值后立即重发下一轮绑定链路。
                    _schedule_retry(f"{stage} request timeout", delay=0.0)

            timer = threading.Timer(request_timeout, _on_timeout)
            timer.daemon = True
            timer.start()

        if not self.device:
            logger.error("Organization binding enabled but DeviceManager is not available")
            return True
        if not self.cloud_conn or not self.cloud_conn.is_cloud_connected:
            logger.warning("Organization binding enabled but cloud connection is not ready")
            _schedule_retry("cloud connection is not ready")
            return True

        binding_code = str(cfg.get('device_binding_code', '') or '').strip()
        organization_id = str(cfg.get('organization_id', '') or '').strip()
        device_model_key = str(cfg.get('device_model_key', '') or '').strip()
        device_callsign = str(cfg.get('device_callsign', '') or Config.get_gateway_sn()).strip()
        bind_sub_devices = bool(cfg.get('bind_sub_devices', True))
        sub_devices = self.device.get_all_sub_devices() if bind_sub_devices else []
        try:
            request_timeout = max(30, int(cfg.get('request_timeout_seconds', 120) or 120))
        except (TypeError, ValueError):
            request_timeout = 120

        bind_candidates = [{
            'sn': Config.get_gateway_sn(),
            'device_binding_code': binding_code,
            'organization_id': organization_id,
            'device_callsign': device_callsign or Config.get_gateway_sn(),
            'device_model_key': device_model_key,
        }]
        for sub_device in sub_devices:
            bind_candidates.append({
                'sn': sub_device.sn,
                'device_binding_code': binding_code,
                'organization_id': organization_id,
                'device_callsign': sub_device.sn,
                'device_model_key': f"{sub_device.domain}-{sub_device.type}-{sub_device.sub_type}",
            })

        device_sns = [dev['sn'] for dev in bind_candidates]

        def _missing_config(unbound_devices: list) -> list:
            missing = []
            if not binding_code:
                missing.append('device.organization_binding.device_binding_code')
            if not organization_id:
                missing.append('device.organization_binding.organization_id')
            gateway_unbound = any(dev.get('sn') == Config.get_gateway_sn() for dev in unbound_devices)
            if gateway_unbound and not device_model_key:
                missing.append('device.organization_binding.device_model_key')
            for dev in unbound_devices:
                if not dev.get('device_model_key'):
                    missing.append(f"device_model_key for {dev.get('sn', '<unknown>')}")
            return missing

        def _unbound_devices_from_status(msg: dict) -> Optional[list]:
            data = msg.get('data', {}) if isinstance(msg.get('data', {}), dict) else {}
            result = data.get('result', -1)
            if result != 0:
                logger.error("Configured bind status query failed: result=%s", result)
                _schedule_retry(f"bind status result={result}")
                return None
            output = data.get('output', {}) if isinstance(data.get('output', {}), dict) else {}
            statuses = output.get('bind_status', [])
            status_by_sn = {
                str(item.get('sn', '')): bool(item.get('is_device_bind_organization', False))
                for item in statuses
                if isinstance(item, dict)
            }
            return [
                dev for dev in bind_candidates
                if not status_by_sn.get(dev['sn'], False)
            ]

        def _start_org_lookup_and_bind(unbound_devices: list):
            missing = _missing_config(unbound_devices)
            if missing:
                logger.error(
                    "Organization binding enabled but required config is missing: %s",
                    ", ".join(missing),
                )
                _schedule_retry(f"missing config: {', '.join(missing)}")
                return

            def _on_get_reply(method: str, msg: dict):
                _clear_pending(msg.get('tid', ''))
                result = msg.get('data', {}).get('result', -1)
                if result != 0:
                    logger.error("Configured organization query failed: result=%s", result)
                    _schedule_retry(f"organization query result={result}")
                    return
                logger.info(
                    "Configured organization query succeeded; binding %s unbound devices",
                    len(unbound_devices),
                )

                def _on_bind_reply(bind_method: str, bind_msg: dict):
                    _clear_pending(bind_msg.get('tid', ''))
                    bind_data = bind_msg.get('data', {}) if isinstance(bind_msg.get('data', {}), dict) else {}
                    bind_result = bind_data.get('result', -1)
                    bind_output = bind_data.get('output', {}) if isinstance(bind_data.get('output', {}), dict) else {}
                    err_infos = bind_output.get('err_infos', [])
                    failed_sns = {
                        str(item.get('sn', ''))
                        for item in err_infos
                        if isinstance(item, dict)
                    }
                    gateway_failed = Config.get_gateway_sn() in failed_sns
                    if bind_result != 0 or gateway_failed:
                        logger.error(
                            "Configured organization bind failed: result=%s, gateway_failed=%s, err_infos=%s",
                            bind_result,
                            gateway_failed,
                            err_infos,
                        )
                        _schedule_retry(f"organization bind result={bind_result}")
                        return
                    logger.info("Configured organization bind succeeded")
                    _clear_retry_timer()
                    self._organization_binding_retry_attempt = 0

                tid = self.device.bind_to_organization(
                    unbound_devices,
                    callback=_on_bind_reply,
                    timeout=request_timeout,
                )
                if not tid:
                    logger.error("Configured organization bind was not sent")
                    _schedule_retry("organization bind was not sent")
                else:
                    _mark_pending("airport_organization_bind", tid)
                    _watch_request_timeout("airport_organization_bind", tid)

            logger.info(
                "Configured organization binding: querying organization before binding %s devices",
                len(unbound_devices),
            )
            tid = self.device.query_organization_from_cloud(
                binding_code,
                organization_id,
                callback=_on_get_reply,
                timeout=request_timeout,
            )
            if not tid:
                logger.error("Configured organization query was not sent")
                _schedule_retry("organization query was not sent")
            else:
                _mark_pending("airport_organization_get", tid)
                _watch_request_timeout("airport_organization_get", tid)

        def _on_bind_status_reply(method: str, msg: dict):
            _clear_pending(msg.get('tid', ''))
            unbound_devices = _unbound_devices_from_status(msg)
            if unbound_devices is None:
                return
            if not unbound_devices:
                logger.info(
                    "Configured organization binding skipped: all queried devices are already bound"
                )
                _clear_retry_timer()
                self._organization_binding_retry_attempt = 0
                self.device.report_update_topo()
                return
            logger.info(
                "Configured organization binding: %s/%s devices are unbound",
                len(unbound_devices),
                len(bind_candidates),
            )
            _start_org_lookup_and_bind(unbound_devices)

        logger.info(
            "Configured organization binding enabled; querying bind status before organization bind"
        )
        tid = self.device.query_bind_status_from_cloud(
            device_sns,
            callback=_on_bind_status_reply,
            timeout=request_timeout,
        )
        if not tid:
            logger.error("Configured bind status query was not sent")
            _schedule_retry("bind status query was not sent")
        else:
            _mark_pending("airport_bind_status", tid)
            _watch_request_timeout("airport_bind_status", tid)
        return True

    def _retry_configured_organization_binding(self):
        self._organization_binding_retry_timer = None
        if not self.running:
            logger.info("Configured organization binding retry skipped: gateway stopping")
            return
        self._start_configured_organization_binding()

    def _configure_payload_connections(self):
        """Configure direct payload adapters without depending on flight control."""
        self.payload_config = Config.get_payload_config()
        self.payload_topology = PayloadTopology(self.payload_config.get('devices'))
        for error in self.payload_topology.validate():
            logger.error("Payload topology invalid: %s", error)

        if self.third_party:
            self.third_party.set_payload_topology(
                self.payload_topology,
                self._payload_connection_states,
            )
        if self.device:
            self.device.set_payload_state_provider(self._payload_connection_states)
        if self.media:
            self.media.set_payload_state_provider(self._payload_connection_states)

        gimbal_config = self.payload_topology.get_device('gimbal')
        control = (gimbal_config.get('channels') or {}).get('control') or {}
        direct_gimbal_enabled = (
            bool(gimbal_config.get('enabled', True))
            and bool(gimbal_config.get('direct_to_gateway'))
            and control.get('adapter') == 'viewlink'
        )
        if direct_gimbal_enabled:
            sdk_dir = control.get('sdk_dir')
            host = control.get('host')
            port = control.get('port')
            missing = [
                key for key, value in (
                    ('sdk_dir', sdk_dir),
                    ('host', host),
                    ('port', port),
                )
                if value in (None, '')
            ]
            if missing:
                logger.error(
                    "ViewLink gimbal config incomplete in YAML: missing %s",
                    ','.join(missing),
                )
                return
            self.gimbal_control = GimbalControl(
                sdk_dir=sdk_dir,
                gimbal_ip=host,
                gimbal_port=int(port),
                register_dev_status_callback=control.get('register_dev_status_callback', False),
            )
            self.camera.set_gimbal_control(self.gimbal_control)
            self.gimbal.set_gimbal_control(self.gimbal_control)
            if bool(gimbal_config.get('auto_connect', True)):
                self._gimbal_auto_connect_control = dict(control)
                logger.info("ViewLink gimbal auto connect deferred until MQTT is connected")

        spectral_config = self.payload_topology.get_device('spectral_camera')
        if bool(spectral_config.get('enabled', True)) and bool(spectral_config.get('direct_to_gateway')):
            self.spectral_camera = SpectralCameraAdapter(spectral_config)

        if self.drc:
            self.drc.set_gimbal_control(self.gimbal_control)
            self.drc.set_camera_controller(self.camera)

        fallback_enabled = bool(
            self.payload_config.get('flight_controller_fallback_enabled', False)
        )
        logger.info(
            "Payload topology configured: path=%s devices=%s flight_controller_fallback=%s",
            self.payload_config.get('control_path'),
            ','.join(device_id for device_id, _ in self.payload_topology.iter_devices()),
            fallback_enabled,
        )

    def _auto_connect_gimbal_payload(self, control: dict):
        """Start a non-blocking ViewLink gimbal connection during gateway startup."""
        if not self.gimbal_control:
            return None
        timeout = float(control.get("connect_timeout", 5.0) or 5.0)
        if hasattr(self.gimbal_control, "ensure_connected_async"):
            result = self.gimbal_control.ensure_connected_async(timeout=timeout, retry_interval=0)
        else:
            result = self.gimbal_control.ensure_connected(timeout=timeout, retry_interval=0)

        if result.get("success"):
            logger.info("ViewLink gimbal already connected: %s", result.get("message"))
        elif result.get("connecting"):
            logger.info("ViewLink gimbal auto connect started in background: %s", result.get("message"))
        else:
            logger.warning("ViewLink gimbal auto connect failed to start: %s", result)
        return result

    def _auto_connect_gimbal_payload_after_mqtt_connected(self):
        """Start ViewLink auto-connect only after the gateway has an MQTT session."""
        with self._gimbal_auto_connect_lock:
            if self._gimbal_auto_connect_started:
                return
            control = self._gimbal_auto_connect_control
            if not control:
                return
            if not self.mqtt or not self.mqtt.connected:
                logger.info("ViewLink gimbal auto connect waiting for MQTT connection")
                return
            if not self._startup_connections_ready:
                logger.info("ViewLink gimbal auto connect deferred until gateway startup is ready")
                return
            self._gimbal_auto_connect_started = True

        result = self._auto_connect_gimbal_payload(control)
        if not isinstance(result, dict) or (
            not result.get("success")
            and not result.get("connecting")
        ):
            with self._gimbal_auto_connect_lock:
                self._gimbal_auto_connect_started = False

    def _payload_connection_states(self) -> dict:
        """Return adapter state without treating configured endpoints as connected."""
        states = {}
        if self.gimbal:
            states['gimbal'] = self.gimbal.build_payload_state()
        elif self.gimbal_control:
            states['gimbal'] = (
                'connected' if self.gimbal_control.is_connected() else 'disconnected'
            )
        if self.camera:
            states['mapping_camera'] = self.camera.build_mapping_camera_payload_state()
        if self.spectral_camera:
            states['spectral_camera'] = self.spectral_camera.build_payload_state()
        return states

    def _enable_payload_flight_controller_fallback(self, connection) -> bool:
        """Opt-in legacy bridge for SITL only; never called with the nest link."""
        if not self.payload_config:
            self.payload_config = Config.get_payload_config()
        if not self.payload_config.get('flight_controller_fallback_enabled', False):
            logger.info("Payload-to-flight-controller fallback is disabled")
            return False
        self.camera.set_mavlink_connection(connection)
        self.gimbal.set_mavlink_connection(connection)
        self.third_party.set_mavlink_connection(connection)
        logger.warning("Legacy payload fallback over flight-controller MAVLink is enabled")
        return True

    def _close_payload_connections(self):
        if not self.gimbal_control:
            return
        try:
            self.gimbal_control.uninitialize()
        except Exception as exc:
            logger.warning("Failed to close direct gimbal adapter: %s", exc)

    def start(self) -> bool:
        """启动网关"""
        try:
            # 提前获取事件循环（供异步数据库操作使用）
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
            self.loop.set_exception_handler(self._handle_async_exception)

            # 注入事件循环到需要异步操作的模块
            if self.device:
                self.device.set_event_loop(self.loop)
            if self.mission:
                self.mission.set_event_loop(self.loop)
            if self.media:
                self.media.set_event_loop(self.loop)
            if self.camera:
                self.camera.set_event_loop(self.loop)

            # 连接NanoMQ
            logger.info("Connecting to NanoMQ...")
            if self.mqtt.connect():
                self.mqtt.set_allowed_service_methods(ALLOWED_SERVICE_METHODS)
                self.mqtt.set_ignored_service_methods(IGNORED_NON_SERVICE_METHODS)
                # 注册路由器到MQTT
                for method, handler in self.router._routes.items():
                    self.mqtt.register_handler(
                        method,
                        handler,
                        background=method in BACKGROUND_IO_METHODS,
                        reply_required=method in DOWNSTREAM_REPLY_REQUIRED_METHODS,
                    )
            else:
                logger.warning("MQTT not connected, running in offline mode")

            # 连接MAVLink（新架构：Connection + Dispatcher + Modules）
            logger.info("Connecting to MAVLink...")
            mavlink_config = Config.get_mavlink_config()
            nest_mavlink_config = Config.get_nest_mavlink_config()
            use_separate_nest_mavlink = bool(nest_mavlink_config.get('enabled'))
            use_main_mavlink = bool(mavlink_config.get('enabled', True))
            conn_type = mavlink_config['connection_type']

            if not use_main_mavlink:
                logger.info("Main MAVLink disabled; using nest MAVLink as the device-side link")
                self.flight_control = FlightControl()
                self.mode_control = ModeControl()
                self.telemetry_module = TelemetryModule(mode="real")
                self.waypoint_manager = WaypointManager()
                rtk_config = Config.get('rtk', {})
                self.rtk_module = RTKModule(ntrip_config=rtk_config, telemetry_module=self.telemetry_module)
                self.nest_module = DroneNestModule(protocol_version=nest_mavlink_config.get('protocol_version', 'v1.3'))
                if self.mission:
                    self.mission.set_flight_control(self.flight_control)
                    self.mission.set_mode_control(self.mode_control)
                    self.mission.set_waypoint_manager(self.waypoint_manager)
                    self.mission.set_nest_module(self.nest_module)
                    self.mission.set_gimbal_control(self.gimbal)
                    self.mission.set_camera_controller(self.camera)
                    self.mission.set_telemetry_module(self.telemetry_module)
                    self.mission.set_telemetry_reporter(self.telemetry_reporter)
                if self.camera:
                    self.camera.set_flight_control(self.flight_control)
                if self.telemetry_module and self.telemetry:
                    self.telemetry_module.add_callback(self.telemetry.on_telemetry_update)
                if self.drc:
                    self.drc.set_flight_control(self.flight_control)
                    self.drc.set_mode_control(self.mode_control)
                    self.drc.set_mission_manager(self.mission)
                    self.drc.set_telemetry_module(self.telemetry_module)
                if self.device:
                    self.device.set_telemetry_processor(self.telemetry)
                    self.device.set_dock_manager(self.dock)
                self._wire_nest_module_to_business()
                if use_separate_nest_mavlink:
                    if self._start_nest_mavlink(nest_mavlink_config):
                        if self.mission:
                            self.mission.set_mavlink_connection(self.nest_mavlink_conn)
                else:
                    logger.warning("Main MAVLink disabled but nest_mavlink.enabled is false")

            elif conn_type == 'serial':
                conn_str = mavlink_config['port']
            elif conn_type in ('udp', 'udpin'):
                conn_str = f"udpin:{mavlink_config['host']}:{mavlink_config['udp_port']}"
            elif conn_type == 'udpout':
                conn_str = f"udpout:{mavlink_config['host']}:{mavlink_config['udp_port']}"
            elif conn_type == 'tcp':
                conn_str = f"tcp:{mavlink_config['host']}:{mavlink_config['port']}"
            elif use_main_mavlink:
                conn_str = f"udpin:{mavlink_config['host']}:{mavlink_config['udp_port']}"

            if use_main_mavlink:
                self.mavlink_conn = MAVLinkConnection(
                    connection_string=conn_str,
                    source_system=mavlink_config['system_id'],
                    source_component=mavlink_config['component_id'],
                    baudrate=mavlink_config['baudrate']
                )

            if use_main_mavlink and self.mavlink_conn.connect():
                # 创建消息分发器
                self.dispatcher = MessageDispatcher(self.mavlink_conn)

                # 初始化功能模块
                self.flight_control = FlightControl()
                self.mode_control = ModeControl()
                self.telemetry_module = TelemetryModule(mode="real")
                self.nest_module = DroneNestModule(protocol_version=nest_mavlink_config.get('protocol_version', 'v1.3'))
                self.waypoint_manager = WaypointManager()

                # 初始化RTK模块（需要telemetry_module）
                rtk_config = Config.get('rtk', {})
                self.rtk_module = RTKModule(ntrip_config=rtk_config, telemetry_module=self.telemetry_module)

                # 注册模块到分发器（idle_task + on_message）
                self.dispatcher.add_module(self.flight_control)
                self.dispatcher.add_module(self.mode_control)
                self.dispatcher.add_module(self.telemetry_module)
                self.dispatcher.add_module(self.waypoint_manager)
                self.dispatcher.add_module(self.rtk_module)
                if not use_separate_nest_mavlink:
                    self.dispatcher.add_module(self.nest_module)
                    self.nest_module.dispatcher = self.dispatcher

                # 注册消息钩子：功能模块通过 on_message 接收消息
                self.dispatcher.register_hook('*', self.flight_control)
                self.dispatcher.register_hook('*', self.mode_control)
                self.dispatcher.register_hook('*', self.telemetry_module)
                self.dispatcher.register_hook('*', self.waypoint_manager)
                self.dispatcher.register_hook('*', self.rtk_module)
                if not use_separate_nest_mavlink:
                    self.dispatcher.register_hook('DRONE_NEST_RESPONSE', self.nest_module)
                    self.dispatcher.register_hook('DRONE_NEST_ORDER_RESPONSE', self.nest_module)

                # 连接TelemetryModule到TelemetryProcessor（统一数据流）
                # TelemetryModule已通过通配符'*'订阅所有消息，无需单独注册钩子
                self.telemetry_module.add_callback(self.telemetry.on_telemetry_update)

                # 设置TelemetryProcessor回调到TelemetryReporter（上报出口）
                # 注意：完整回调已在 _setup_callbacks() 中设置，此处仅确保 reporter 已初始化

                # 注入模块依赖到业务层
                self.mission.set_flight_control(self.flight_control)
                self.mission.set_mode_control(self.mode_control)
                self.mission.set_mavlink_connection(self.mavlink_conn)
                self.mission.set_waypoint_manager(self.waypoint_manager)
                self.mission.set_nest_module(self.nest_module)
                self.mission.set_gimbal_control(self.gimbal)
                self.mission.set_camera_controller(self.camera)
                self.mission.set_telemetry_module(self.telemetry_module)
                self.mission.set_telemetry_reporter(self.telemetry_reporter)

                # 恢复上次中断的任务
                self.mission.recover_missions()
                self.drc.set_flight_control(self.flight_control)
                self.drc.set_mode_control(self.mode_control)
                self._enable_payload_flight_controller_fallback(self.mavlink_conn)
                self.nest_module.set_flight_control(self.flight_control)

                self.camera.set_flight_control(self.flight_control)
                self.camera.set_mapping_camera_flight_controller_connection(self.mavlink_conn)
                self.camera.set_telemetry_module(self.telemetry_module)

                # DRC 模块依赖注入
                self.drc.set_mission_manager(self.mission)
                self.drc.set_telemetry_module(self.telemetry_module)
                self.drc.set_event_callback(self._handle_drc_event)

                # 注入 DeviceManager 依赖（数据源联动）
                self.device.set_nest_module(self.nest_module)
                self.device.set_telemetry_processor(self.telemetry)
                self.device.set_dock_manager(self.dock)

                self._wire_nest_module_to_business()
                self._wire_flight_area_applier(self.dispatcher)

                if use_separate_nest_mavlink:
                    self._start_nest_mavlink(nest_mavlink_config)

                # 启动分发器
                self.dispatcher.start()
                logger.info("MAVLink dispatcher started")
            elif use_main_mavlink:
                logger.warning("Failed to connect to MAVLink, retrying...")
                # 主飞控 MAVLink 连接失败不阻塞机巢链路测试。
                if use_separate_nest_mavlink:
                    logger.info("Starting standalone nest MAVLink without main MAVLink")
                    self.nest_module = DroneNestModule(protocol_version=nest_mavlink_config.get('protocol_version', 'v1.3'))
                    self._wire_nest_module_to_business()
                    self._start_nest_mavlink(nest_mavlink_config)

            self._refresh_state_machine_sources()

            # 云端连接管理由 MQTT 连接成功回调启动，避免自动绑定/拓扑上报早于
            # broker 可用。
            if self.cloud_conn:
                if self.device:
                    self.device.set_cloud_connection(self.cloud_conn)

            self._startup_connections_ready = True
            self._on_mqtt_connected_for_startup_sync()

            self.running = True
            logger.info("Edge Gateway started successfully")
            gimbal_auto_connect_timer = threading.Timer(
                1.0,
                self._auto_connect_gimbal_payload_after_mqtt_connected,
            )
            gimbal_auto_connect_timer.daemon = True
            gimbal_auto_connect_timer.start()
            return True

        except Exception as e:
            logger.error(f"Start failed: {e}", exc_info=True)
            return False

    def _refresh_state_machine_sources(self):
        """Refresh global-state data sources after runtime modules are created."""
        if not self.global_state:
            return
        self.global_state.set_sources(
            mqtt=self.mqtt,
            device=self.device,
            mission=self.mission,
            drc=self.drc,
            dock=self.dock,
            nest=self.nest_module,
            telemetry=self.telemetry,
            telemetry_module=self.telemetry_module,
        )
        logger.info(
            "Global state machine sources refreshed: nest=%s telemetry_module=%s",
            bool(self.nest_module),
            bool(self.telemetry_module),
        )

    def _wire_nest_module_to_business(self):
        """把机巢模块注入业务层；主飞控未连接时也允许单独测试机巢 MAVLink。"""
        if not self.nest_module:
            return

        if self.device:
            self.device.set_nest_module(self.nest_module)
        if self.mission:
            self.mission.set_nest_module(self.nest_module)

        def nest_env_cb(nest_data):
            if self.device:
                self.device.update_from_nest(nest_data)
                # 机巢状态进入缓存后按 OSD 定频上报平台；这里做兜底节流，
                # 避免收到高频机巢帧时突破客户要求的 2 秒上报节奏。
                self._report_dock_osd_if_due()
            if self.dock:
                dock_updates = filter_dock_status_fields(build_dock3_properties_from_nest(nest_data))
                if dock_updates:
                    self.dock.update_dock_status(dock_updates)
            if self.flight_control and hasattr(self.flight_control, "handle_nest_environment_from_callback"):
                self.flight_control.handle_nest_environment_from_callback(nest_data)

        if nest_env_cb not in self.nest_module.env_callbacks:
            self.nest_module.env_callbacks.append(nest_env_cb)

    def _wire_flight_area_applier(self, dispatcher):
        """把自定义飞行区应用器挂到真实设备侧 MAVLink dispatcher。"""
        if not dispatcher or not self.flight_area_applier:
            return
        self.flight_area_applier.set_dispatcher(dispatcher)
        dispatcher.add_module(self.flight_area_applier)
        dispatcher.register_hook('MISSION_REQUEST', self.flight_area_applier)
        dispatcher.register_hook('MISSION_REQUEST_INT', self.flight_area_applier)
        dispatcher.register_hook('MISSION_ACK', self.flight_area_applier)
        if self.flight_area:
            self.flight_area.set_device_applier(self.flight_area_applier)
        logger.info("Flight area device applier wired to MAVLink dispatcher")

    def _report_dock_osd_if_due(self, force: bool = False) -> bool:
        """按统一 OSD 周期发布机场/机巢状态到平台。"""
        if not self.telemetry_reporter or not self.device:
            return False
        current_time = time.time()
        if not force and current_time - self._last_dock_osd_report_time < self._dock_osd_interval:
            return False
        self._last_dock_osd_report_time = current_time
        return self.telemetry_reporter.report_osd(
            Config.get_gateway_sn(),
            self.device.build_dock3_dock_osd(include_payload=True),
        )

    def _build_aircraft_osd_for_report(self, osd_data: dict) -> dict:
        """Return aircraft OSD enriched with real payload state, without faking fields."""
        enriched = dict(osd_data or {})
        if not self.camera:
            return enriched
        try:
            cameras = self.camera.build_aircraft_osd_cameras()
        except Exception as exc:
            logger.warning("Build aircraft payload OSD failed: %s", exc)
            return enriched
        if cameras:
            enriched["cameras"] = cameras
        return enriched

    def handle_gateway_state_query(self, msg: dict) -> dict:
        """返回全局状态机快照，供平台侧验收脚本做只读准入检查。"""
        data = msg.get('data', {}) if isinstance(msg.get('data', {}), dict) else {}
        compact = bool(data.get('compact', False))
        if not self.global_state:
            return MessageBuilder.build_reply(
                msg,
                result=325100,
                data={'output': {'status': 'failed', 'reason': 'global_state_unavailable'}},
                msg_str='global state machine unavailable',
            )
        snapshot = self.global_state.snapshot()
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={
                'state': _json_safe(snapshot.to_dict(compact=compact)),
                'ready': (
                    snapshot.dock == 'ready'
                    and snapshot.aircraft == 'online'
                    and snapshot.mission == 'idle'
                    and snapshot.drc in {'idle', 'unknown'}
                ),
            },
        )

    def _apply_real_aircraft_mode_code(self, osd_data: dict) -> None:
        """Populate Dock3 aircraft mode_code from real telemetry and runtime state."""
        if not isinstance(osd_data, dict):
            return
        telemetry = self.telemetry.get_current_data() if self.telemetry else {}
        osd_data["mode_code"] = derive_aircraft_mode_code_from_gateway_modules(
            telemetry,
            mission=self.mission,
            flight_control=self.flight_control,
            drc=self.drc,
            nest_module=self.nest_module,
            logger=logger,
        )

    def _apply_aircraft_limit_fields(self, osd_data: dict) -> None:
        """Ensure aircraft OSD carries configured flight safety limits."""
        if not isinstance(osd_data, dict):
            return
        telemetry = self.telemetry.get_current_data() if self.telemetry else {}
        height_limit = _finite_non_negative_number(
            telemetry.get("height_limit"),
            telemetry.get("max_height"),
            telemetry.get("flight_height_limit"),
        )
        if height_limit is None:
            height_limit = _finite_non_negative_number(Config.get("device.limits.max_height"))
        if height_limit is not None:
            osd_data["height_limit"] = height_limit

        distance_limit = _finite_non_negative_number(
            telemetry.get("distance_limit"),
            telemetry.get("max_distance"),
            telemetry.get("flight_distance_limit"),
        )
        if distance_limit is None:
            distance_limit = _finite_non_negative_number(Config.get("device.limits.max_distance"))
        if distance_limit is not None:
            osd_data["distance_limit"] = distance_limit

        return_home_power = _bounded_percent(
            telemetry.get("return_home_power"),
            telemetry.get("remaining_power_for_return_home"),
            telemetry.get("return_home_battery"),
        )
        if return_home_power is None:
            return_home_power = _bounded_percent(Config.get("device.limits.return_home_power"))
        if return_home_power is not None:
            osd_data["return_home_power"] = return_home_power

    def _start_nest_mavlink(self, nest_config: dict) -> bool:
        """启动独立机巢 MAVLink 连接。"""
        try:
            if not self.nest_module:
                logger.warning("Nest module not initialized, skip nest MAVLink")
                return False
            self.nest_module.set_protocol_version(nest_config.get('protocol_version', 'v1.3'))

            conn_type = nest_config.get('connection_type', 'udp')
            if conn_type == 'serial':
                conn_str = nest_config['port']
            elif conn_type == 'udp':
                host = _normalize_udp_target_host(str(nest_config.get('host', '127.0.0.1')))
                if host != nest_config.get('host'):
                    logger.warning(
                        "nest_mavlink.connection.host=%s is a bind/wildcard address, using %s as UDP destination",
                        nest_config.get('host'),
                        host,
                    )
                conn_str = f"udp:{host}:{nest_config['udp_port']}"
            elif conn_type == 'tcp':
                conn_str = f"tcp:{nest_config['host']}:{nest_config['port']}"
            else:
                host = _normalize_udp_target_host(str(nest_config.get('host', '127.0.0.1')))
                if host != nest_config.get('host'):
                    logger.warning(
                        "nest_mavlink.connection.host=%s is a bind/wildcard address, using %s as UDP destination",
                        nest_config.get('host'),
                        host,
                    )
                conn_str = f"udp:{host}:{nest_config['udp_port']}"

            logger.info(f"Connecting to nest MAVLink: {conn_str}")
            self.nest_mavlink_conn = MAVLinkConnection(
                connection_string=conn_str,
                source_system=nest_config['system_id'],
                source_component=nest_config['component_id'],
                baudrate=nest_config['baudrate'],
                use_custom_dialect=True
            )

            if not self.nest_mavlink_conn.connect():
                logger.warning("Failed to connect to nest MAVLink")
                return False

            self.nest_dispatcher = MessageDispatcher(self.nest_mavlink_conn)
            self.nest_dispatcher.configure_reconnect(
                enabled=bool(nest_config.get('reconnect_enabled', True)),
                interval=float(nest_config.get('reconnect_interval', 2.0) or 2.0),
            )
            device_side_modules = [] if self.mavlink_conn else [
                self.flight_control,
                self.mode_control,
                self.telemetry_module,
                self.waypoint_manager,
                self.rtk_module,
            ]
            for module in device_side_modules:
                if module:
                    self.nest_dispatcher.add_module(module)
                    self.nest_dispatcher.register_hook('*', module)
            if not self.mavlink_conn:
                self._wire_flight_area_applier(self.nest_dispatcher)
            self.nest_dispatcher.add_module(self.nest_module)
            self.nest_dispatcher.register_hook('DRONE_NEST_RESPONSE', self.nest_module)
            self.nest_dispatcher.register_hook('DRONE_NEST_ORDER_RESPONSE', self.nest_module)
            self.nest_module.dispatcher = self.nest_dispatcher
            poll_enabled = nest_config.get('status_poll_enabled')
            if poll_enabled is None:
                poll_enabled = conn_type == 'udp'
            self.nest_module.status_poll_enabled = bool(poll_enabled)
            self.nest_module.status_poll_interval = float(nest_config.get(
                'status_poll_interval',
                self.nest_module.status_poll_interval,
            ) or self.nest_module.status_poll_interval)
            logger.info(
                "Nest status poll configured: enabled=%s interval=%ss connection_type=%s",
                self.nest_module.status_poll_enabled,
                self.nest_module.status_poll_interval,
                conn_type,
            )
            self.nest_dispatcher.start()
            logger.info("Nest MAVLink dispatcher started")
            return True

        except Exception as e:
            logger.error(f"Nest MAVLink start failed: {e}", exc_info=True)
            return False

    def stop(self):
        """停止网关"""
        logger.info("Stopping Edge Gateway...")
        self.running = False

        # 停止云端心跳
        if self.cloud_conn:
            self.cloud_conn.stop_heartbeat()
            self.cloud_conn.report_device_offline()
        if self._organization_binding_retry_timer:
            self._organization_binding_retry_timer.cancel()
            self._organization_binding_retry_timer = None

        if self.mission:
            self.mission.stop_mission_monitor()
        if self.telemetry:
            self.telemetry.stop()
        if self.dispatcher:
            self.dispatcher.stop()
        if self.nest_dispatcher:
            self.nest_dispatcher.stop()
        if self.mavlink_conn:
            self.mavlink_conn.disconnect()
        if self.nest_mavlink_conn:
            self.nest_mavlink_conn.disconnect()
        self._close_payload_connections()
        if self.mqtt:
            self.mqtt.disconnect()
        if self.device:
            self.device.stop()
        if self.rtk_module:
            self.rtk_module.stop_ntrip()
        if self.retention_manager:
            self.retention_manager.stop_sync()
        if self.cloud_sync_manager:
            self.cloud_sync_manager.stop_sync()

        close_database()

        logger.info("Edge Gateway stopped")

    def request_stop(self, reason: str = ""):
        """请求主循环退出，由 async finally 统一执行异步清理。"""
        logger.info("Stop requested%s", f": {reason}" if reason else "")
        self.running = False

    def _handle_async_exception(self, loop, context):
        """记录 asyncio 后台任务未处理异常。"""
        message = context.get("message", "Async task exception")
        exception = context.get("exception")
        if exception:
            logger.error(
                "Async exception: %s",
                message,
                exc_info=(type(exception), exception, exception.__traceback__)
            )
        else:
            logger.error("Async exception: %s, context=%s", message, context)

    async def stop_async(self):
        """异步停止网关"""
        logger.info("Stopping Edge Gateway (async)...")
        self.running = False

        if self.cloud_conn:
            self.cloud_conn.stop_heartbeat()
            self.cloud_conn.report_device_offline()
        if self._organization_binding_retry_timer:
            self._organization_binding_retry_timer.cancel()
            self._organization_binding_retry_timer = None

        if self.mission:
            self.mission.stop_mission_monitor()
        if self.telemetry:
            self.telemetry.stop()
        if self.dispatcher:
            self.dispatcher.stop()
        if self.nest_dispatcher:
            self.nest_dispatcher.stop()
        if self.mavlink_conn:
            self.mavlink_conn.disconnect()
        if self.nest_mavlink_conn:
            self.nest_mavlink_conn.disconnect()
        self._close_payload_connections()
        if self.mqtt:
            self.mqtt.disconnect()
        if self.device:
            self.device.stop()
        if self.rtk_module:
            self.rtk_module.stop_ntrip()

        if self.retention_manager:
            await self.retention_manager.stop()
        if self.cloud_sync_manager:
            await self.cloud_sync_manager.stop()

        close_database()

        logger.info("Edge Gateway stopped (async)")

    async def run_async(self):
        """异步主循环"""
        logger.info("Entering async main loop...")

        if self.retention_manager:
            await self.retention_manager.start()

        if self.cloud_sync_manager:
            await self.cloud_sync_manager.start()

        try:
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
        finally:
            await self.stop_async()

    def run(self):
        """主循环（同步入口）"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.run_async())
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        finally:
            loop.close()

    def _on_mqtt_connected_for_startup_sync(self):
        """Run startup cloud/device sync only after MQTT is connected."""
        if not self._startup_connections_ready:
            logger.info("MQTT connected before MAVLink startup completed; deferring device sync")
            return
        if not self.mqtt or not self.mqtt.connected:
            logger.info("MQTT is not connected; skip startup device sync")
            return

        flush_latest_mission_platform_snapshot(
            self.mqtt,
            self.mission,
            gateway_sn=Config.get_gateway_sn(),
        )

        if self.cloud_conn:
            logger.info("Starting cloud connection manager after all startup connections...")
            self.cloud_conn.report_device_online()
            if self.device:
                self.device.publish_live_capacity_state("gateway_online")
            self.cloud_conn.start_heartbeat()

        if not self.device:
            return

        if self.cloud_conn:
            self.device.set_cloud_connection(self.cloud_conn)

        if self._start_configured_organization_binding():
            return

        auto_bound = self._auto_bind_gateway_for_test_runtime()
        if auto_bound:
            return

        # Edge side startup binding check uses requests/requests_reply. If the
        # gateway is already bound, this path reports update_topo immediately.
        self.device.auto_query_bind_status_on_startup()

    def _setup_callbacks(self):
        """设置各模块的回调函数"""
        gateway_sn = Config.get_gateway_sn()

        # 设备管理回调
        def device_event_cb(event):
            # update_topo 必须通过 sys/product/{sn}/status 通道上报
            if event.get('method') == 'update_topo':
                self.mqtt.publish_status(event)
            else:
                self.mqtt.publish_event(event)
        def device_state_cb(state):
            if self.history_logger:
                self.history_logger.record_device_state_change(gateway_sn, state, source='device_manager')
            self.telemetry_reporter.report_state(gateway_sn, state)

        self.device.set_callbacks(device_event_cb, device_state_cb)

        # 注册 status_reply 处理器
        self.mqtt.set_status_reply_handler(self.device.handle_update_topo_reply)

        # 注册 requests_reply 处理器
        self.mqtt.set_requests_reply_handler(self.cloud_conn.handle_requests_reply)
        self.mqtt.register_connect_callback(self._auto_connect_gimbal_payload_after_mqtt_connected)
        self.mqtt.register_connect_callback(self._on_mqtt_connected_for_startup_sync)

        # 启动请求清理线程
        self.cloud_conn._start_request_cleaner()

        # 遥测回调
        def persist_telemetry(device_sn: str, osd_data: dict):
            try:
                with SessionLocal() as session:
                    TelemetryDB.save_telemetry_sync(session, device_sn, osd_data)
                    session.commit()
            except Exception as e:
                logger.warning(
                    "Failed to persist telemetry: device_sn=%s, error=%s",
                    device_sn,
                    e,
                    exc_info=True,
                )

        def osd_cb(osd_data):
            self._apply_real_aircraft_mode_code(osd_data)
            self._apply_aircraft_limit_fields(osd_data)
            aircraft_osd = self._build_aircraft_osd_for_report(osd_data)
            if self.flight_area:
                self.flight_area.update_aircraft_osd(aircraft_osd)
            # 同步遥测数据到 DeviceManager（更新物模型OSD属性）
            self.device.update_from_telemetry(osd_data)
            self._report_dock_osd_if_due(force=True)

            published_drone_osd = False
            for sub_device in self.device.get_all_sub_devices():
                if sub_device.online:
                    persist_telemetry(sub_device.sn, aircraft_osd)
                    if self.history_logger:
                        self.history_logger.record_flight_telemetry(
                            sub_device.sn, aircraft_osd, source='aircraft_osd'
                        )
                    self.telemetry_reporter.report_osd(sub_device.sn, aircraft_osd)
                    published_drone_osd = True
            if not published_drone_osd:
                persist_telemetry(gateway_sn, aircraft_osd)
                if self.history_logger:
                    self.history_logger.record_flight_telemetry(
                        gateway_sn, aircraft_osd, source='aircraft_osd'
                    )

        def state_cb(state_data):
            if self.history_logger:
                self.history_logger.record_device_state_change(gateway_sn, state_data, source='telemetry_state')
            self.telemetry_reporter.report_state(gateway_sn, state_data)

        self.telemetry.set_callbacks(osd_cb, state_cb)
        self.telemetry.start()  # 启动OSD定时推送线程 (0.5Hz)

        # 任务管理回调
        def mission_event_cb(event):
            publish_platform_event_with_latest_snapshot(
                self.mqtt,
                event,
                gateway_sn=gateway_sn,
                source='mission_manager',
            )
            # 同步 mission 状态到 DeviceManager
            self.device.update_from_mission(event)
        self.mission.set_event_callback(mission_event_cb)
        self.point_flight.set_event_callback(mission_event_cb)

        # 媒体管理回调
        def media_event_cb(event):
            publish_platform_event_with_latest_snapshot(
                self.mqtt,
                event,
                gateway_sn=gateway_sn,
                source='media_manager',
            )
        self.media.set_event_callback(media_event_cb)
        def media_state_cb(state):
            if self.history_logger:
                self.history_logger.record_device_state_change(gateway_sn, state, source='media_manager')
            self.telemetry_reporter.report_state(gateway_sn, state)
        self.media.set_state_callback(media_state_cb)
        self.media.set_request_sender(self.cloud_conn.send_request)

        # DRC事件回调
        self.drc.set_event_callback(self._handle_drc_event)

        # 机场管理回调
        def dock_event_cb(event):
            self.mqtt.publish_event(event)
        def dock_state_cb(state):
            if self.history_logger:
                self.history_logger.record_device_state_change(gateway_sn, state, source='dock_manager')
            self.telemetry_reporter.report_state(gateway_sn, state)
        self.dock.set_callbacks(dock_event_cb, dock_state_cb)

        # 云端连接状态回调
        def cloud_status_cb(connected: bool):
            if connected:
                self.capability_reporter.report_all_capabilities()
        self.cloud_conn.set_status_callback(cloud_status_cb)

        # 日志管理回调
        def log_event_cb(event):
            self.mqtt.publish_event(event)
        self.log_mgr.set_event_callback(log_event_cb)

        # FlySafe回调
        def flysafe_event_cb(event):
            self.mqtt.publish_event(event)
        self.flysafe.set_event_callback(flysafe_event_cb)

        # 飞行区域回调
        def flight_area_event_cb(event):
            self.mqtt.publish_event(event)
        self.flight_area.set_event_callback(flight_area_event_cb)
        self.flight_area.set_request_sender(self.cloud_conn.send_request)

        # 自定义数据回调
        def custom_data_event_cb(event):
            self.mqtt.publish_event(event)
        self.custom_data.set_event_callback(custom_data_event_cb)

        # 第三方负载回调
        def third_party_event_cb(event):
            self.mqtt.publish_event(event)
        self.third_party.set_event_callback(third_party_event_cb)

        def camera_event_cb(event):
            self.mqtt.publish_event(event)
        self.camera.set_event_callback(camera_event_cb)

        def gimbal_event_cb(event):
            self.mqtt.publish_event(event)
        self.gimbal.set_event_callback(gimbal_event_cb)

    def _handle_drc_event(self, event: dict):
        """同步 DRC 状态到物模型后再上报事件。"""
        if event.get('method') == 'drc_status_notify' and self.device:
            data = event.get('data', {})
            if 'drc_state' in data:
                self.device.update_properties({'drc_state': data['drc_state']})
        publish_platform_event_with_latest_snapshot(
            self.mqtt,
            event,
            gateway_sn=Config.get_gateway_sn(),
            source='drc_controller',
        )

    def _register_handlers(self):
        """注册所有API处理器到路由器"""

        # ---- 设备管理API (Device Management) ----
        self.router.register_batch({
            'ota_create': self.device.handle_ota_create,
            'ota_cancel': self.device.handle_ota_cancel,
            'firmware_upgrade': self.device.handle_ota_create,
            'firmware_upgrade_cancel': self.device.handle_ota_cancel,
            'config_update': self.device.handle_config_update,
            'config_get': self.device.handle_config_get,
            'config_rollback': self.device.handle_config_rollback,
            'property_set': self.device.handle_property_set,
            'property_get': self.device.handle_property_get,
            'gateway_state_query': self.handle_gateway_state_query,
            'device_reboot': self.device.handle_device_reboot,
            'format_sd_card': self.device.handle_format_sd_card,
            'cover_open': self.device.handle_cover_open,
            'cover_close': self.device.handle_cover_close,
            'charge_open': self.device.handle_charge_open,
            'charge_close': self.device.handle_charge_close,
            'putter_open': self.device.handle_putter_open,
            'putter_close': self.device.handle_putter_close,
            'device_lock': self.device.handle_device_lock,
            'device_unlock': self.device.handle_device_unlock,
            'supplement_light_open': self.device.handle_supplement_light_open,
            'supplement_light_close': self.device.handle_supplement_light_close,
            'sdr_workmode_switch': self.device.handle_sdr_workmode_switch,
            'sim_slot_switch': self.device.handle_sim_slot_switch,
            'esim_activate': self.device.handle_esim_activate,
            'esim_operator_switch': self.device.handle_esim_operator_switch,
            'alternate_land_point_set': self.device.handle_alternate_land_point_set,
            'rtk_ref_points_set': self.device.handle_rtk_ref_points_set,
            'rtk_calibration': self.device.handle_rtk_calibration,
            'rtk_ref_points_query': self.device.handle_rtk_ref_points_query,
            'device_exit_rtk_mode': self.device.handle_device_exit_rtk_mode,
            'device_enter_simulation_mode': self.device.handle_device_enter_simulation_mode,
            'device_exit_simulation_mode': self.device.handle_device_exit_simulation_mode,
            'device_calibration': self.device.handle_device_calibration,
            'device_format': self.device.handle_device_format,
            'device_reset': self.device.handle_device_reset,
            'hms_get': self.device.handle_hms_get,
            'hms_clear': self.device.handle_hms_clear,
            'device_log_list_get': self.device.handle_device_log_list_get,
            'device_log_upload': self.device.handle_device_log_upload,
            'cover_force_close': self.device.handle_cover_force_close,
            'drone_open': self.device.handle_drone_open,
            'drone_close': self.device.handle_drone_close,
            'drone_format': self.device.handle_drone_format,
            'debug_mode_open': self.device.handle_debug_mode_open,
            'debug_mode_close': self.device.handle_debug_mode_close,
            'battery_maintenance_switch': self.device.handle_battery_maintenance_switch,
            'air_conditioner_mode_switch': self.device.handle_air_conditioner_mode_switch,
            'alarm_state_switch': self.device.handle_alarm_state_switch,
            'battery_store_mode_switch': self.device.handle_battery_store_mode_switch,
            'config': self.device.handle_config,
            'update_topo': self.device.handle_update_topo,
            'airsense_warning': self.device.handle_airsense_warning,
        }, category='device')

        # property/set 单独处理(在MQTT client中通过特殊topic处理)
        # 但也可以注册为通用handler
        # self.router.register('properties_set', self.device.handle_property_set, 'device')

        # ---- 飞行任务API (Flight Task) ----
        self.router.register_batch({
            'load_wayline': self.mission.handle_load_wayline,
            'upload_wayline': self.mission.handle_upload_wayline,
            'execute_task': self.mission.handle_execute_task,
            'cancel_task': self.mission.handle_cancel_task,
            'pause_wayline': self.mission.handle_pause_wayline,
            'resume_wayline': self.mission.handle_resume_wayline,
            'flyto': self.mission.handle_flyto,
            'take_off': self.mission.handle_takeoff,
            'one_key_takeoff_flow': self.mission.handle_one_key_takeoff_flow,
            'one_key_return_home': self.mission.handle_one_key_return_home,
            'cancel_return_home': self.mission.handle_cancel_return_home,
            'set_flight_mode': self.mission.handle_set_flight_mode,
            'go_home': self.mission.handle_go_home,
            'landing': self.mission.handle_landing,
            'emergency_stop': self.mission.handle_emergency_stop,
            'start_takeoff_to_point': self.mission.handle_start_takeoff_to_point,
            # Dock3 DRC page documents these, but their Topic is services.
            # Keep them categorized with mission/point-flight, not DRC realtime.
            'takeoff_to_point': self.point_flight.handle_takeoff_to_point,
            'fly_to_point': self.point_flight.handle_fly_to_point,
            'fly_to_point_stop': self.point_flight.handle_fly_to_point_stop,
            'fly_to_point_update': self.point_flight.handle_fly_to_point_update,
            'return_home': self.mission.handle_return_home,
            'go_to_altitude': self.mission.handle_go_to_altitude,
        }, category='mission')

        # ---- 航线管理API (Wayline Management) ----
        # DJI Dock3 Cloud API Wayline 接口参考:
        # https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/wayline.html
        #
        # Services (down): 云端下发 -> 设备响应 services_reply
        # Events (up): 设备上报 -> 云端响应 events_reply
        # Requests (up): 设备请求 -> 云端响应 requests_reply
        self.router.register_batch({
            # 航线任务管理 (Services)
            'wayline_task_create': self.mission.handle_wayline_task_create,
            'wayline_task_execute': self.mission.handle_wayline_task_execute,
            'wayline_task_pause': self.mission.handle_wayline_task_pause,
            'wayline_task_resume': self.mission.handle_wayline_task_resume,
            'wayline_task_cancel': self.mission.handle_wayline_task_cancel,
            'wayline_task_query': self.mission.handle_wayline_task_query,
            'wayline_mission_upload': self.mission.handle_wayline_mission_upload,
            'wayline_mission_download': self.mission.handle_wayline_mission_download,
            'wayline_mission_delete': self.mission.handle_wayline_mission_delete,
            'wayline_mission_query': self.mission.handle_wayline_mission_query,
            # DJI标准FlightTask (Services)
            'flighttask_prepare': self.mission.handle_flighttask_prepare,
            'flighttask_execute': self.mission.handle_flighttask_execute,
            'flighttask_undo': self.mission.handle_flighttask_undo,
            'flighttask_stop': self.mission.handle_flighttask_stop,
            'flighttask_progress': self.mission.handle_flighttask_progress,  # 兼做查询
            'flighttask_progress_get': self.mission.handle_flighttask_progress_get,
            'flighttask_pause': self.mission.handle_flighttask_pause,
            'flighttask_resume': self.mission.handle_flighttask_resume,
            'flighttask_return': self.mission.handle_flighttask_return,
            'resume_wayline_task': self.mission.handle_resume_wayline_task,
            'flight_setup_abort': self.mission.handle_flight_setup_abort,
            'flighttask_create': self.mission.handle_flighttask_create,
            'flighttask_recovery': self.mission.handle_flighttask_recovery,
            'return_home_cancel': self.mission.handle_return_home_cancel,
            'return_specific_home': self.mission.handle_return_specific_home,
            'flighttask_resource_get': self.mission.handle_flighttask_resource_get,
            'in_flight_wayline_deliver': self.mission.handle_in_flight_wayline_deliver,
            'in_flight_wayline_stop': self.mission.handle_in_flight_wayline_stop,
            'in_flight_wayline_recover': self.mission.handle_in_flight_wayline_recover,
            'in_flight_wayline_cancel': self.mission.handle_in_flight_wayline_cancel,
        }, category='mission')

        # ---- 作业管理API (Job Management) ----
        self.router.register_batch({
            'job_create': self.mission.handle_job_create,
            'job_delete': self.mission.handle_job_delete,
            'job_query': self.mission.handle_job_query,
        }, category='mission')

        # ---- 云台控制API (Gimbal Control) ----
        self.router.register_batch({
            'gimbalRotate': self.gimbal.handle_gimbal_rotate,
            'camera_screen_drag': self.gimbal.handle_camera_screen_drag,
        }, category='payload')

        # ---- 第三方负载API (Third Party Payload) ----
        self.router.register_batch({
            'drc_light_calibration': self.third_party.handle_drc_light_calibration,
            'drc_light_fine_tuning_set': self.third_party.handle_drc_light_fine_tuning_set,
            'drc_speaker_tts_set': self.third_party.handle_drc_speaker_tts_set,
            'drc_speaker_play_volume_set': self.third_party.handle_drc_speaker_play_volume_set,
            'payload_authority_release': self.third_party.handle_payload_authority_release,
            'psdk_ui_resource_upload_result': self.third_party.handle_psdk_ui_resource_upload_result,
            'psdk_floating_window_text': self.third_party.handle_psdk_floating_window_text,
            'psdk_widget_value_set': self.third_party.handle_psdk_widget_value_set,
            'psdk_input_box_text_set': self.third_party.handle_psdk_input_box_text_set,
            'speaker_replay': self.third_party.handle_speaker_replay,
            'speaker_play_mode_set': self.third_party.handle_speaker_play_mode_set,
            'speaker_play_stop': self.third_party.handle_speaker_play_stop,
            'speaker_audio_play_start': self.third_party.handle_speaker_audio_play_start,
            'speaker_tts_play_start': self.third_party.handle_speaker_tts_play_start,
            'speaker_play_volume_set': self.third_party.handle_speaker_play_volume_set,
        }, category='payload')

        # ---- 媒体管理API (Media Management) ----
        self.router.register_batch({
            'file_list_get': self.media.handle_file_list_get,
            'file_download': self.media.handle_file_download,
            'download_media': self.media.handle_download_media,
            'file_delete': self.media.handle_file_delete,
            'adjust_upload_priority': self.media.handle_adjust_upload_priority,
            'live_start': self.media.handle_live_start,
            'live_start_push': self.media.handle_live_start_push,
            'live_stop': self.media.handle_live_stop,
            'live_stop_push': self.media.handle_live_stop_push,
            'live_camera_change': self.media.handle_live_camera_change,
            'set_livestream_lens': self.media.handle_set_livestream_lens,
            'set_livestream_quality': self.media.handle_set_livestream_quality,
            'live_capability_query': self.media.handle_live_capability_query,
            'highest_priority_upload_flighttask_media': self.media.handle_highest_priority_upload_flighttask_media,
            'upload_flighttask_media_prioritize': self.media.handle_upload_flighttask_media_prioritize,
            'live_lens_change': self.media.handle_live_lens_change,
            'live_set_quality': self.media.handle_live_set_quality,
        }, category='media')

        # ---- Services-topic DRC session methods ----
        # These methods manage DRC state, but the Cloud API topic is services,
        # so they are intentionally kept out of the DRC realtime category.
        self.router.register_batch({
            'drc_mode_enter': self.drc.handle_drc_mode_enter,
            'drc_mode_exit': self.drc.handle_drc_mode_exit,
            'flight_authority_grab': self.drc.handle_flight_authority_grab,
        }, category='services')

        # ---- DRC实时控制API (Direct Remote Control) ----
        # Only drc/down realtime methods belong in this category. Methods from
        # the Dock3 DRC page that use services/services_reply are registered in
        # their service-domain category above.
        self.router.register_batch({
            'heart_beat': self.drc.handle_drc_heart_beat,
            'stick_control': self.drc.handle_stick_control,
            'drone_control': self.drc.handle_drone_control,
            'drone_emergency_stop': self.drc.handle_drone_emergency_stop,
        }, category='drc')

        # Dock3 文档的 DRC 页面同时列出了一批 services 负载控制接口。
        # 这些 method 的 Topic 是 thing/product/{gateway_sn}/services，
        # 不能落到 DRCController 的占位实现里；统一在 payload 层注册。
        self.router.register_batch({
            'camera_frame_zoom': self.camera.handle_camera_frame_zoom,
            'camera_mode_switch': self.camera.handle_camera_mode_switch,
            'camera_photo_take': self.camera.handle_camera_photo_take,
            'camera_photo_stop': self.camera.handle_camera_photo_stop,
            'camera_recording_start': self.camera.handle_camera_recording_start,
            'camera_recording_stop': self.camera.handle_camera_recording_stop,
            'camera_focal_length_set': self.camera.handle_camera_focal_length_set,
            'camera_exposure_set': self.camera.handle_camera_exposure_set,
            'camera_focus_mode_set': self.camera.handle_camera_focus_mode_set,
            'camera_focus_value_set': self.camera.handle_camera_focus_value_set,
            'camera_point_focus_action': self.camera.handle_camera_point_focus_action,
            'camera_exposure_mode_set': self.camera.handle_camera_exposure_mode_set,
            'camera_aim': self.gimbal.handle_camera_aim,
            'camera_screen_split': self.gimbal.handle_camera_screen_split,
            'gimbal_reset': self.gimbal.handle_gimbal_reset,
            'camera_look_at': self.gimbal.handle_camera_look_at,
            'photo_storage_set': self.camera.handle_photo_storage_set,
            'video_storage_set': self.camera.handle_video_storage_set,
            'payload_authority_grab': self.third_party.handle_payload_authority_grab,
            'ir_metering_mode_set': self.gimbal.handle_ir_metering_mode_set,
            'ir_metering_point_set': self.gimbal.handle_ir_metering_point_set,
            'ir_metering_area_set': self.gimbal.handle_ir_metering_area_set,
        }, category='payload')

        # ---- 日志管理API ----
        self.router.register_batch({
            'fileupload_list': self.log_mgr.handle_fileupload_list,
            'fileupload_start': self.log_mgr.handle_fileupload_start,
            'fileupload_update': self.log_mgr.handle_fileupload_update,
        }, category='log')

        # ---- FlySafe API ----
        self.router.register_batch({
            'unlock_license_switch': self.flysafe.handle_unlock_license_switch,
            'unlock_license_update': self.flysafe.handle_unlock_license_update,
            'unlock_license_list': self.flysafe.handle_unlock_license_list,
        }, category='flysafe')

        # ---- 飞行区域API ----
        self.router.register_batch({
            'flight_areas_update': self.flight_area.handle_flight_areas_update,
        }, category='flight_area')

        # ---- 自定义数据传输API ----
        self.router.register_batch({
            'custom_data_transmission_from_esdk': self.custom_data.handle_custom_data_transmission_from_esdk,
            'custom_data_transmission_to_esdk': self.custom_data.handle_custom_data_transmission_to_esdk,
            'custom_data_transmission_from_psdk': self.custom_data.handle_custom_data_transmission_from_psdk,
            'custom_data_transmission_to_psdk': self.custom_data.handle_custom_data_transmission_to_psdk,
        }, category='custom_data')

        logger.info(f"Registered {len(self.router._routes)} API handlers")


async def main_async():
    """异步主入口"""
    import argparse
    parser = argparse.ArgumentParser(description='Edge Gateway')
    parser.add_argument('-c', '--config', default='./config/gateway.yaml',
                       help='Configuration file path')
    parser.add_argument('-d', '--daemon', action='store_true',
                       help='Run as daemon')
    parser.add_argument('--runtime-mode', choices=['test', 'production'],
                       help='Runtime mode. production rejects test identities and missing real-chain topology metadata.')
    parser.add_argument('--gateway-sn',
                       help='Override gateway.sn for this run')
    parser.add_argument('--gateway-device-secret',
                       help='Override device.topology.device_secret for update_topo')
    parser.add_argument('--gateway-nonce',
                       help='Override device.topology.nonce for update_topo')
    parser.add_argument('--sub-device-json', action='append', default=[],
                       help='Add a real-chain sub-device JSON object with sn/domain/type/sub_type/index/device_secret/nonce/thing_version/online.')
    parser.add_argument('--mission-completion-enabled', choices=['true', 'false'],
                       help='Enable or disable post-wayline return/landing/dock recovery flow.')
    parser.add_argument('--mission-completion-retry-count', type=int,
                       help='Retry count for flight-control and dock commands in one-key takeoff/completion flow.')
    parser.add_argument('--mission-completion-retry-interval', type=float,
                       help='Retry interval seconds for one-key takeoff/completion flow.')
    args = parser.parse_args()

    runtime_overrides = build_runtime_overrides(args)

    gateway = EdgeGateway()

    if sys.gettrace() is None:
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}")
            gateway.request_stop(f"signal={signum}")

        signal.signal(signal.SIGINT, signal_handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, signal_handler)
    else:
        logger.info("Running in debug mode, signal handlers disabled")

    if not gateway.initialize(args.config, runtime_overrides=runtime_overrides):
        sys.exit(1)

    if not gateway.start():
        sys.exit(1)

    await gateway.run_async()


def main():
    """主入口"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main_async())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        loop.close()


if __name__ == '__main__':
    main()
