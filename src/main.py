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
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.config import Config, load_config
from src.utils.logger import init_logger, get_logger
from src.utils.message import MessageBuilder
from src.mqtt.client import MQTTClient
from src.mqtt.topic_router import TopicRouter
from src.core.connection import MAVLinkConnection
from src.core.message_dispatcher import MessageDispatcher
from src.modules.flight_control import FlightControl
from src.modules.mode_control import ModeControl
from src.modules.telemetry_module import TelemetryModule
from src.modules.drone_nest_module import DroneNestModule
from src.mavlink.telemetry import TelemetryProcessor
from src.device.manager import DeviceManager
from src.mission.manager import MissionManager
from src.payload.camera import CameraController
from src.payload.gimbal import GimbalController
from src.payload.third_party import ThirdPartyController
from src.media.manager import MediaManager
from src.drc.controller import DrcController
from src.cloud_api.dock_manager import DockManager
from src.cloud_api.connection_manager import CloudConnectionManager
from src.cloud_api.telemetry_reporter import TelemetryReporter
from src.cloud_api.capability_reporter import CapabilityReporter
from src.modules.log_manager import LogManager
from src.modules.flysafe_manager import FlySafeManager
from src.modules.flight_area_manager import FlightAreaManager
from src.modules.custom_data_manager import CustomDataManager

logger = get_logger("main")


class EdgeGateway:
    """边缘网关主控"""

    def __init__(self):
        self.running = False
        self.config = None

        # 核心组件
        self.mqtt: MQTTClient = None
        self.router: TopicRouter = None

        # MAVLink新架构（core + modules）
        self.mavlink_conn = None
        self.dispatcher = None
        self.flight_control = None
        self.mode_control = None
        self.telemetry_module = None

        # 遥测处理
        self.telemetry: TelemetryProcessor = None
        self.device: DeviceManager = None
        self.mission: MissionManager = None
        self.camera: CameraController = None
        self.gimbal: GimbalController = None
        self.third_party: ThirdPartyController = None
        self.media: MediaManager = None
        self.drc: DrcController = None
        self.dock: DockManager = None
        self.cloud_conn: CloudConnectionManager = None
        self.telemetry_reporter: TelemetryReporter = None
        self.capability_reporter: CapabilityReporter = None
        self.log_mgr: LogManager = None
        self.flysafe: FlySafeManager = None
        self.flight_area: FlightAreaManager = None
        self.custom_data: CustomDataManager = None

    def initialize(self, config_path: str = "./config/gateway.yaml") -> bool:
        """初始化网关"""
        try:
            # 1. 加载配置
            logger.info("Loading configuration...")
            self.config = load_config(config_path)

            # 2. 初始化日志
            log_config = Config.get('logging', {})
            init_logger(log_config)
            logger.info("Logger initialized")

            # 3. 初始化MQTT客户端
            logger.info("Initializing MQTT client...")
            self.mqtt = MQTTClient()

            # 4. 初始化Topic路由器
            logger.info("Initializing topic router...")
            self.router = TopicRouter()

            # 5. 初始化遥测处理器
            logger.info("Initializing telemetry processor...")
            self.telemetry = TelemetryProcessor()

            # 6. 初始化业务模块
            logger.info("Initializing business modules...")
            self.device = DeviceManager()
            self.mission = MissionManager()
            self.camera = CameraController()
            self.gimbal = GimbalController()
            self.third_party = ThirdPartyController()
            self.media = MediaManager()
            self.drc = DrcController()
            self.dock = DockManager()
            self.log_mgr = LogManager()
            self.flysafe = FlySafeManager()
            self.flight_area = FlightAreaManager()
            self.custom_data = CustomDataManager()

            # 8. 初始化云端连接管理
            self.cloud_conn = CloudConnectionManager(self.mqtt)
            self.telemetry_reporter = TelemetryReporter(self.mqtt)
            self.capability_reporter = CapabilityReporter(self.mqtt)

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

    def start(self) -> bool:
        """启动网关"""
        try:
            # 连接NanoMQ
            logger.info("Connecting to NanoMQ...")
            if self.mqtt.connect():
                # 注册路由器到MQTT
                for method, handler in self.router._routes.items():
                    self.mqtt.register_handler(method, handler)
            else:
                logger.warning("MQTT not connected, running in offline mode")

            # 连接MAVLink（新架构：Connection + Dispatcher + Modules）
            logger.info("Connecting to MAVLink...")
            mavlink_config = Config.get_mavlink_config()
            conn_type = mavlink_config['connection_type']

            if conn_type == 'serial':
                conn_str = mavlink_config['port']
            elif conn_type == 'udp':
                conn_str = f"udp:{mavlink_config['host']}:{mavlink_config['udp_port']}"
            elif conn_type == 'tcp':
                conn_str = f"tcp:{mavlink_config['host']}:{mavlink_config['udp_port']}"
            else:
                conn_str = f"udp:{mavlink_config['host']}:{mavlink_config['udp_port']}"

            self.mavlink_conn = MAVLinkConnection(
                connection_string=conn_str,
                source_system=mavlink_config['system_id'],
                source_component=mavlink_config['component_id'],
                baudrate=mavlink_config['baudrate']
            )

            if self.mavlink_conn.connect():
                # 创建消息分发器
                self.dispatcher = MessageDispatcher(self.mavlink_conn)

                # 初始化功能模块
                self.flight_control = FlightControl()
                self.mode_control = ModeControl()
                self.telemetry_module = TelemetryModule(mode="real")
                self.nest_module = DroneNestModule()

                # 注册模块到分发器（idle_task + on_message）
                self.dispatcher.add_module(self.flight_control)
                self.dispatcher.add_module(self.mode_control)
                self.dispatcher.add_module(self.telemetry_module)
                self.dispatcher.add_module(self.nest_module)

                # 注册消息钩子：功能模块通过 on_message 接收消息
                self.dispatcher.register_hook('*', self.flight_control)
                self.dispatcher.register_hook('*', self.mode_control)
                self.dispatcher.register_hook('*', self.telemetry_module)
                self.dispatcher.register_hook('*', self.nest_module)
                self.dispatcher.register_hook('DRONE_NEST_RESPONSE', self.nest_module)
                self.dispatcher.register_hook('DRONE_NEST_ORDER_RESPONSE', self.nest_module)

                # 注册遥测钩子（替代adapter的register_telemetry_callback）
                self.dispatcher.register_hook('GLOBAL_POSITION_INT',
                    lambda msg: self.telemetry.on_position(msg.msg))
                self.dispatcher.register_hook('ATTITUDE',
                    lambda msg: self.telemetry.on_attitude(msg.msg))
                self.dispatcher.register_hook('SYS_STATUS',
                    lambda msg: self.telemetry.on_battery(msg.msg))
                self.dispatcher.register_hook('BATTERY_STATUS',
                    lambda msg: self.telemetry.on_battery(msg.msg))
                self.dispatcher.register_hook('GPS_RAW_INT',
                    lambda msg: self.telemetry.on_gps(msg.msg))
                self.dispatcher.register_hook('HEARTBEAT',
                    lambda msg: self.telemetry.on_heartbeat(msg.msg))
                self.dispatcher.register_hook('VFR_HUD',
                    lambda msg: self.telemetry.on_vfr_hud(msg.msg))
                self.dispatcher.register_hook('WIND',
                    lambda msg: self.telemetry.on_wind(msg.msg))

                # 注入模块依赖到业务层
                self.mission.set_flight_control(self.flight_control)
                self.mission.set_mode_control(self.mode_control)
                self.mission.set_mavlink_connection(self.mavlink_conn)
                self.drc.set_flight_control(self.flight_control)
                self.drc.set_mode_control(self.mode_control)
                self.camera.set_mavlink_connection(self.mavlink_conn)
                self.gimbal.set_mavlink_connection(self.mavlink_conn)
                self.third_party.set_mavlink_connection(self.mavlink_conn)
                self.dock.set_nest_module(self.nest_module)
                self.nest_module.set_flight_control(self.flight_control)

                # 注入 DeviceManager 依赖（数据源联动）
                self.device.set_nest_module(self.nest_module)
                self.device.set_telemetry_processor(self.telemetry)
                self.device.set_dock_manager(self.dock)

                # 机巢数据同步到 DeviceManager
                def nest_env_cb(nest_data):
                    self.device.update_from_nest(nest_data)
                self.nest_module.env_callbacks.append(nest_env_cb)

                # 启动分发器
                self.dispatcher.start()
                logger.info("MAVLink dispatcher started")
            else:
                logger.warning("Failed to connect to MAVLink, retrying...")
                # MAVLink连接失败不阻塞，后续会重连

            # 启动云端连接管理（上报在线、启动心跳）
            if self.cloud_conn:
                logger.info("Starting cloud connection manager...")
                self.cloud_conn.report_device_online()
                self.cloud_conn.start_heartbeat()
                self.cloud_conn._start_request_cleaner()

            self.running = True
            logger.info("Edge Gateway started successfully")
            return True

        except Exception as e:
            logger.error(f"Start failed: {e}", exc_info=True)
            return False

    def stop(self):
        """停止网关"""
        logger.info("Stopping Edge Gateway...")
        self.running = False

        # 停止云端心跳
        if self.cloud_conn:
            self.cloud_conn.stop_heartbeat()
            self.cloud_conn.report_device_offline()

        if self.telemetry:
            self.telemetry.stop()
        if self.dispatcher:
            self.dispatcher.stop()
        if self.mavlink_conn:
            self.mavlink_conn.disconnect()
        if self.mqtt:
            self.mqtt.disconnect()
        if self.device:
            self.device.stop()

        logger.info("Edge Gateway stopped")

    def run(self):
        """主循环"""
        logger.info("Entering main loop...")
        try:
            while self.running:
                # 主循环可扩展心跳、监控等逻辑
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        finally:
            self.stop()

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
            self.mqtt.publish_state(gateway_sn, state)

        self.device.set_callbacks(device_event_cb, device_state_cb)

        # 注册 status_reply 处理器
        self.mqtt.set_status_reply_handler(self.device.handle_update_topo_reply)

        # 注册 requests_reply 处理器
        self.mqtt.set_requests_reply_handler(self.cloud_conn.handle_requests_reply)

        # 启动请求清理线程
        self.cloud_conn._start_request_cleaner()

        # 遥测回调
        def osd_cb(osd_data):
            # 同步遥测数据到 DeviceManager（更新物模型OSD属性）
            self.device.update_from_telemetry(osd_data)
            self.mqtt.publish_osd(gateway_sn, osd_data)
        def state_cb(state_data):
            self.mqtt.publish_state(gateway_sn, state_data)

        self.telemetry.set_callbacks(osd_cb, state_cb)
        self.telemetry.start()  # 启动OSD定时推送线程 (0.5Hz)

        # 任务管理回调
        def mission_event_cb(event):
            self.mqtt.publish_event(event)
        self.mission.set_event_callback(mission_event_cb)

        # 媒体管理回调
        def media_event_cb(event):
            self.mqtt.publish_event(event)
        self.media.set_event_callback(media_event_cb)
        self.media.set_request_sender(self.cloud_conn.send_request)

        # DRC事件回调
        def drc_event_cb(event):
            self.mqtt.publish_event(event)
        self.drc.set_event_callback(drc_event_cb)

        # 机场管理回调
        def dock_event_cb(event):
            self.mqtt.publish_event(event)
        def dock_state_cb(state):
            self.mqtt.publish_state(gateway_sn, state)
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

    def _register_handlers(self):
        """注册所有API处理器到路由器"""

        # ---- 设备管理API (Device Management) ----
        self.router.register_batch({
            'ota_create': self.device.handle_ota_create,
            'ota_cancel': self.device.handle_ota_cancel,
            'config_update': self.device.handle_config_update,
            'property_set': self.device.handle_property_set,
            'property_get': self.device.handle_property_get,
            'device_binding': self.device.handle_device_binding,
            'device_unbinding': self.device.handle_device_unbinding,
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
            'camera_mode_switch': self.device.handle_camera_mode_switch,
            'supplement_light_open': self.device.handle_supplement_light_open,
            'supplement_light_close': self.device.handle_supplement_light_close,
            'sdr_workmode_switch': self.device.handle_sdr_workmode_switch,
            'alternate_land_point_set': self.device.handle_alternate_land_point_set,
            'rtk_ref_points_set': self.device.handle_rtk_ref_points_set,
            'rtk_ref_points_query': self.device.handle_rtk_ref_points_query,
            'device_exit_rtk_mode': self.device.handle_device_exit_rtk_mode,
            'device_enter_simulation_mode': self.device.handle_device_enter_simulation_mode,
            'device_exit_simulation_mode': self.device.handle_device_exit_simulation_mode,
            'device_calibration': self.device.handle_device_calibration,
            'device_format': self.device.handle_device_format,
            'device_reset': self.device.handle_device_reset,
            'workspace_bind_status': self.device.handle_workspace_bind_status,
            'workspace_get': self.device.handle_workspace_get,
            'hms_get': self.device.handle_hms_get,
            'hms_clear': self.device.handle_hms_clear,
            'device_log_list_get': self.device.handle_device_log_list_get,
            'device_log_upload': self.device.handle_device_log_upload,
            'device_capability_query': self.device.handle_device_capability_query,
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
            'airport_bind_status': self.device.handle_airport_bind_status,
            'airport_organization_get': self.device.handle_airport_organization_get,
            'airport_organization_bind': self.device.handle_airport_organization_bind,
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
            'fly_to_point': self.mission.handle_fly_to_point,
            'take_off': self.mission.handle_takeoff,
            'one_key_return_home': self.mission.handle_one_key_return_home,
            'cancel_return_home': self.mission.handle_cancel_return_home,
            'set_flight_mode': self.mission.handle_set_flight_mode,
            'go_home': self.mission.handle_go_home,
            'landing': self.mission.handle_landing,
            'emergency_stop': self.mission.handle_emergency_stop,
            'start_takeoff_to_point': self.mission.handle_start_takeoff_to_point,
            'return_home': self.mission.handle_return_home,
            'go_to_altitude': self.mission.handle_go_to_altitude,
        }, category='mission')

        # ---- 航线管理API (Wayline Management) ----
        self.router.register_batch({
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
            'flighttask_prepare': self.mission.handle_flighttask_prepare,
            'flighttask_execute': self.mission.handle_flighttask_execute,
            'flighttask_undo': self.mission.handle_flighttask_undo,
            'flighttask_return': self.mission.handle_flighttask_return,
            'flighttask_progress': self.mission.handle_flighttask_progress,
            'flighttask_pause': self.mission.handle_flighttask_pause,
            'flighttask_resume': self.mission.handle_flighttask_resume,
            'flighttask_ready': self.mission.handle_flighttask_ready,
            'return_home_info': self.mission.handle_return_home_info,
            'flight_setup_abort': self.mission.handle_flight_setup_abort,
            'flighttask_create': self.mission.handle_flighttask_create,
            'flighttask_recovery': self.mission.handle_flighttask_recovery,
            'return_home_cancel': self.mission.handle_return_home_cancel,
            'flighttask_resource_get': self.mission.handle_flighttask_resource_get,
        }, category='mission')

        # ---- 作业管理API (Job Management) ----
        self.router.register_batch({
            'job_create': self.mission.handle_job_create,
            'job_delete': self.mission.handle_job_delete,
            'job_query': self.mission.handle_job_query,
        }, category='mission')

        # ---- 相机控制API (Camera Control) ----
        self.router.register_batch({
            'camera_photo_take': self.camera.handle_camera_photo_take,
            'start_photo': self.camera.handle_start_photo,
            'stop_photo': self.camera.handle_stop_photo,
            'start_record': self.camera.handle_start_record,
            'stop_record': self.camera.handle_stop_record,
            'set_camera_mode': self.camera.handle_set_camera_mode,
            'set_exposure_mode': self.camera.handle_set_exposure_mode,
            'zoom': self.camera.handle_zoom,
            'set_focus_mode': self.camera.handle_set_focus_mode,
            'set_focus_value': self.camera.handle_set_focus_value,
            'spot_focus': self.camera.handle_spot_focus,
            'photo_storage_set': self.camera.handle_photo_storage_set,
            'camera_recover': self.camera.handle_camera_recover,
            'camera_reset': self.camera.handle_camera_reset,
            'set_shutter_speed': self.camera.handle_set_shutter_speed,
            'set_iso': self.camera.handle_set_iso,
            'set_white_balance': self.camera.handle_set_white_balance,
            'set_aperture': self.camera.handle_set_aperture,
            'set_metering_mode': self.camera.handle_set_metering_mode,
            'set_resolution': self.camera.handle_set_resolution,
            'set_frame_rate': self.camera.handle_set_frame_rate,
            'set_video_encoding': self.camera.handle_set_video_encoding,
            'camera_capability_query': self.camera.handle_camera_capability_query,
            'camera_photo_stop': self.camera.handle_camera_photo_stop,
            'camera_recording_start': self.camera.handle_camera_recording_start,
            'camera_recording_stop': self.camera.handle_camera_recording_stop,
            'camera_focal_length_set': self.camera.handle_camera_focal_length_set,
            'camera_point_focus_action': self.camera.handle_camera_point_focus_action,
            'camera_exposure_mode_set': self.camera.handle_camera_exposure_mode_set,
            'camera_focus_mode_set': self.camera.handle_camera_focus_mode_set,
            'camera_focus_value_set': self.camera.handle_camera_focus_value_set,
        }, category='payload')

        # ---- 云台控制API (Gimbal Control) ----
        self.router.register_batch({
            'gimbalRotate': self.gimbal.handle_gimbal_rotate,
            'camera_look_at': self.gimbal.handle_camera_look_at,
            'set_gimbal_mode': self.gimbal.handle_set_gimbal_mode,
            'gimbal_reset': self.gimbal.handle_gimbal_reset,
            'gimbal_capability_query': self.gimbal.handle_gimbal_capability_query,
            'gimbal_status_query': self.gimbal.handle_gimbal_status_query,
        }, category='payload')

        # ---- 红外相机API (IR Thermal) ----
        self.router.register_batch({
            'ir_metering_mode_set': self.gimbal.handle_ir_metering_mode_set,
            'ir_color_palette_set': self.gimbal.handle_ir_color_palette_set,
            'ir_display_mode_set': self.gimbal.handle_ir_display_mode_set,
            'ir_isotherm_set': self.gimbal.handle_ir_isotherm_set,
            'ir_zoom_set': self.gimbal.handle_ir_zoom_set,
        }, category='payload')

        # ---- 第三方负载API (Third Party Payload) ----
        self.router.register_batch({
            'drc_light_control_set': self.third_party.handle_drc_light_control_set,
            'drc_light_calibration': self.third_party.handle_drc_light_calibration,
            'drc_light_fine_tuning_set': self.third_party.handle_drc_light_fine_tuning_set,
            'drc_speaker_tts_set': self.third_party.handle_drc_speaker_tts_set,
            'drc_speaker_play_volume_set': self.third_party.handle_drc_speaker_play_volume_set,
            'drc_speaker_audio_play': self.third_party.handle_drc_speaker_audio_play,
            'drc_speaker_audio_pause': self.third_party.handle_drc_speaker_audio_pause,
            'drc_speaker_audio_stop': self.third_party.handle_drc_speaker_audio_stop,
            'psdk_function_set': self.third_party.handle_psdk_function_set,
            'set_thermal_mode': self.third_party.handle_set_thermal_mode,
            'set_palette': self.third_party.handle_set_palette,
            'payload_authority_grab': self.third_party.handle_payload_authority_grab,
            'payload_authority_release': self.third_party.handle_payload_authority_release,
            'payload_status_query': self.third_party.handle_payload_status_query,
            'parachute_open': self.third_party.handle_parachute_open,
            'parachute_status_query': self.third_party.handle_parachute_status_query,
            'night_vision_mode_set': self.third_party.handle_night_vision_mode_set,
            'gas_detector_data_get': self.third_party.handle_gas_detector_data_get,
            'radar_status_query': self.third_party.handle_radar_status_query,
            'searchlight_status_query': self.third_party.handle_searchlight_status_query,
            'speaker_status_query': self.third_party.handle_speaker_status_query,
            'payload_capability_query': self.third_party.handle_payload_capability_query,
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
            'file_delete': self.media.handle_file_delete,
            'adjust_upload_priority': self.media.handle_adjust_upload_priority,
            'storage_config_get': self.media.handle_storage_config_get,
            'live_start': self.media.handle_live_start,
            'live_stop': self.media.handle_live_stop,
            'set_livestream_lens': self.media.handle_set_livestream_lens,
            'set_livestream_quality': self.media.handle_set_livestream_quality,
            'live_capability_query': self.media.handle_live_capability_query,
            'highest_priority_upload_flighttask_media': self.media.handle_highest_priority_upload_flighttask_media,
            'upload_flighttask_media_prioritize': self.media.handle_upload_flighttask_media_prioritize,
            'live_lens_change': self.media.handle_live_lens_change,
            'live_set_quality': self.media.handle_live_set_quality,
        }, category='media')

        # ---- DRC实时控制API (Direct Remote Control) ----
        self.router.register_batch({
            'drc_mode_enter': self.drc.handle_drc_mode_enter,
            'drc_mode_exit': self.drc.handle_drc_mode_exit,
            'drc_speed_push': self.drc.handle_drc_speed_push,
            'drc_heading_push': self.drc.handle_drc_heading_push,
            'drc_altitude_push': self.drc.handle_drc_altitude_push,
            'drc_flyto_point_push': self.drc.handle_drc_flyto_point_push,
            'drone_control': self.drc.handle_drone_control,
            'payload_control': self.drc.handle_payload_control,
            'drc_gimbal_speed_push': self.drc.handle_drc_gimbal_speed_push,
            'fly_to_point_stop': self.drc.handle_fly_to_point_stop,
            'fly_to_point_update': self.drc.handle_fly_to_point_update,
            'camera_screen_drag': self.drc.handle_camera_screen_drag,
            'camera_aim': self.drc.handle_camera_aim,
            'camera_screen_split': self.drc.handle_camera_screen_split,
            'video_storage_set': self.drc.handle_video_storage_set,
            'camera_exposure_set': self.drc.handle_camera_exposure_set,
            'ir_metering_point_set': self.drc.handle_ir_metering_point_set,
            'ir_metering_area_set': self.drc.handle_ir_metering_area_set,
            'poi_mode_enter': self.drc.handle_poi_mode_enter,
            'poi_mode_exit': self.drc.handle_poi_mode_exit,
            'poi_circle_speed_set': self.drc.handle_poi_circle_speed_set,
            'drone_emergency_stop': self.drc.handle_drone_emergency_stop,
            'takeoff_to_point': self.drc.handle_takeoff_to_point,
        }, category='drc')

        # ---- 机场管理API (DJI Dock) ----
        self.router.register_batch({
            'dock_status_query': self.dock.handle_dock_status_query,
            'dock_control': self.dock.handle_dock_control,
            'dock_alarm_query': self.dock.handle_dock_alarm_query,
            'dock_task_schedule_query': self.dock.handle_dock_task_schedule_query,
            'dock_task_schedule_set': self.dock.handle_dock_task_schedule_set,
            'dock_emergency_stop': self.dock.handle_dock_emergency_stop,
            'dock_reset': self.dock.handle_dock_reset,
            'dock_weather_query': self.dock.handle_dock_weather_query,
        }, category='dock')

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
            'flight_areas_get': self.flight_area.handle_flight_areas_get,
        }, category='flight_area')

        # ---- 自定义数据传输API ----
        self.router.register_batch({
            'custom_data_transmission_from_esdk': self.custom_data.handle_custom_data_transmission_from_esdk,
            'custom_data_transmission_to_esdk': self.custom_data.handle_custom_data_transmission_to_esdk,
            'custom_data_transmission_from_psdk': self.custom_data.handle_custom_data_transmission_from_psdk,
            'custom_data_transmission_to_psdk': self.custom_data.handle_custom_data_transmission_to_psdk,
        }, category='custom_data')

        logger.info(f"Registered {len(self.router._routes)} API handlers")


def main():
    """主入口"""
    import argparse
    parser = argparse.ArgumentParser(description='Edge Gateway')
    parser.add_argument('-c', '--config', default='./config/gateway.yaml',
                       help='Configuration file path')
    parser.add_argument('-d', '--daemon', action='store_true',
                       help='Run as daemon')
    args = parser.parse_args()

    # 创建网关实例
    gateway = EdgeGateway()

    # 信号处理（调试模式下不注册，避免与 debugpy 冲突）
    if sys.gettrace() is None:
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}")
            gateway.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        # Windows 不完全支持 SIGTERM，跳过注册
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, signal_handler)
    else:
        logger.info("Running in debug mode, signal handlers disabled")

    # 初始化并启动
    if not gateway.initialize(args.config):
        sys.exit(1)

    if not gateway.start():
        sys.exit(1)

    # 运行主循环
    gateway.run()


if __name__ == '__main__':
    main()
