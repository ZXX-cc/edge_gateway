"""
DJI Cloud API 设备能力集上报模块

设备上线后需要向云端上报自身能力集，包括：
- 设备基本信息
- 机场能力（Dock）
- 无人机能力
- 负载能力
- 媒体能力
- DRC能力

参考：DJI Cloud API - Device Capability Specification
"""
import time
from typing import Dict, Any, List
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder, TopicBuilder

logger = get_logger("capability")


class CapabilityReporter:
    """
    设备能力集上报器

    在设备上线、固件升级、负载变更时上报能力集。
    """

    def __init__(self, mqtt_client):
        self.mqtt = mqtt_client
        self.gateway_sn = Config.get_gateway_sn()

    def report_all_capabilities(self):
        """上报完整能力集"""
        self.report_dock_capability()
        self.report_drone_capability()
        self.report_payload_capability()
        self.report_media_capability()
        self.report_drc_capability()
        logger.info("All capabilities reported")

    def report_dock_capability(self):
        """
        上报机场能力集

        标准格式：
        {
            "cover_control": true,
            "charge_control": true,
            "putter_control": true,
            "supplement_light": true,
            "air_conditioner": true,
            "rtk_support": true,
            "alternate_landing_point": true,
            "emergency_stop": true,
            "weather_monitor": true
        }
        """
        capability = {
            'cover_control': True,
            'charge_control': True,
            'putter_control': True,
            'supplement_light': True,
            'air_conditioner': True,
            'rtk_support': True,
            'alternate_landing_point': True,
            'emergency_stop': True,
            'weather_monitor': True,
            'task_schedule': True,
        }

        self._publish_capability('dock_capability_put', capability)

    def report_drone_capability(self):
        """
        上报无人机能力集

        标准格式：
        {
            "max_flight_time": 1800,
            "max_speed": 15.0,
            "max_height": 120.0,
            "max_distance": 1000.0,
            "drc_support": true,
            "waypoint_support": true,
            "flight_modes": ["AUTO", "RTL", "LOITER", "GUIDED"],
            "obstacle_avoidance": true,
            "precision_landing": true
        }
        """
        capability = {
            'max_flight_time': 1800,
            'max_speed': 15.0,
            'max_height': 120.0,
            'max_distance': 1000.0,
            'drc_support': True,
            'waypoint_support': True,
            'flight_modes': ['MANUAL', 'STABILIZE', 'AUTO', 'RTL', 'LOITER',
                            'GUIDED', 'TAKEOFF', 'LAND', 'QSTABILIZE', 'QRTL'],
            'obstacle_avoidance': True,
            'precision_landing': True,
            'return_home_support': True,
            'one_key_takeoff_support': True,
        }

        self._publish_capability('drone_capability_put', capability)

    def report_payload_capability(self):
        """
        上报负载能力集

        标准格式：
        {
            "camera_types": ["wide", "zoom", "ir"],
            "gimbal_types": ["3-axis"],
            "speaker_support": true,
            "searchlight_support": true,
            "night_vision_support": true,
            "parachute_support": false,
            "max_payload_count": 3
        }
        """
        capability = {
            'camera_types': ['wide', 'zoom', 'ir'],
            'gimbal_types': ['3-axis'],
            'speaker_support': True,
            'searchlight_support': True,
            'night_vision_support': True,
            'parachute_support': False,
            'gas_detector_support': False,
            'radar_support': False,
            'max_payload_count': 3,
            'cameras': [
                {
                    'camera_index': '0',
                    'camera_type': 'wide',
                    'supports_photo': True,
                    'supports_record': True,
                    'supports_zoom': True,
                    'max_zoom_factor': 20.0,
                    'supports_ir': False
                },
                {
                    'camera_index': '1',
                    'camera_type': 'zoom',
                    'supports_photo': True,
                    'supports_record': True,
                    'supports_zoom': True,
                    'max_zoom_factor': 200.0,
                    'supports_ir': False
                },
                {
                    'camera_index': '2',
                    'camera_type': 'ir',
                    'supports_photo': True,
                    'supports_record': True,
                    'supports_zoom': True,
                    'max_zoom_factor': 20.0,
                    'supports_ir': True
                }
            ]
        }

        self._publish_capability('payload_capability_put', capability)

    def report_media_capability(self):
        """
        上报媒体能力集

        标准格式：
        {
            "storage_support": true,
            "upload_support": true,
            "livestream_support": true,
            "supported_protocols": ["webrtc", "rtmp", "rtsp"],
            "supported_resolutions": ["720p", "1080p", "4k"],
            "supported_video_sources": ["wide", "zoom", "ir", "fpv"],
            "max_bitrate": 8000,
            "max_framerate": 60
        }
        """
        capability = {
            'storage_support': True,
            'upload_support': True,
            'livestream_support': True,
            'supported_protocols': ['webrtc', 'rtmp', 'rtsp', 'gb28181'],
            'supported_resolutions': ['720p', '1080p', '4k'],
            'supported_video_sources': ['wide', 'zoom', 'ir', 'fpv', 'split'],
            'max_bitrate': 8000,
            'max_framerate': 60,
            'photo_formats': ['jpg', 'dng'],
            'video_formats': ['mp4', 'mov']
        }

        self._publish_capability('media_capability_put', capability)

    def report_drc_capability(self):
        """
        上报DRC能力集

        标准格式：
        {
            "drc_support": true,
            "supported_commands": ["speed", "heading", "altitude", "flyto"],
            "max_drc_speed": 15.0,
            "drc_gimbal_support": true,
            "drc_payload_support": true
        }
        """
        capability = {
            'drc_support': True,
            'supported_commands': ['speed', 'heading', 'altitude', 'flyto'],
            'max_drc_speed': 15.0,
            'drc_gimbal_support': True,
            'drc_payload_support': True,
            'osd_push_frequency': 10
        }

        self._publish_capability('drc_capability_put', capability)

    def _publish_capability(self, method: str, data: Dict[str, Any]):
        """发布能力集事件"""
        topic = TopicBuilder.events(self.gateway_sn)
        payload = MessageBuilder.build_event(method, data, self.gateway_sn)
        self.mqtt.publish(topic, payload)
        logger.info(f"Capability reported: {method}")
