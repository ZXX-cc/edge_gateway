"""
DJI Cloud API 标准遥测上报模块

按照大疆上云API规范格式化并上报OSD数据。
支持：
- OSD定频上报 (thing/product/{sn}/osd)
- 状态变更上报 (thing/product/{sn}/state)
- 事件上报 (thing/product/{sn}/events)
- 飞行任务进度上报
- HMS告警上报
- 断线缓存与恢复机制

参考：DJI Cloud API - OSD & State Specification
"""
import time
import threading
from typing import Dict, Any, Optional, List
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder, TopicBuilder

logger = get_logger("telemetry")
alarm_logger = get_logger("alarm")

MAX_CACHE_SIZE = 100
MAX_CACHE_TIME = 300  # 缓存最大时间（秒）


class TelemetryReporter:
    """
    标准遥测上报器

    将内部遥测数据转换为大疆Cloud API标准格式并上报。
    支持断线缓存与恢复机制。
    """

    def __init__(self, mqtt_client):
        self.mqtt = mqtt_client
        self.gateway_sn = Config.get_gateway_sn()

        # 断线缓存
        self._cache: List[Dict[str, Any]] = []
        self._cache_lock = threading.Lock()

        # 注册MQTT连接状态回调
        self.mqtt.register_connect_callback(self._on_mqtt_connect)

    # ---- OSD 定频上报 ----

    def report_osd(self, device_sn: str, osd_data: Dict[str, Any]) -> bool:
        """
        上报OSD定频数据

        标准格式：
        {
            "latitude": 39.9,
            "longitude": 116.4,
            "height": 50.0,
            "altitude": 100.0,
            "speed": 5.0,
            "vertical_speed": 0.5,
            "heading": 90.0,
            "battery": {
                "capacity_percent": 80,
                "voltage": 22.2,
                "current": 5.0,
                "temperature": 35.0
            },
            "gimbal": {
                "pitch": -30.0,
                "yaw": 45.0,
                "roll": 0.0
            },
            "satellite_number": 12,
            "gps_signal_level": 5,
            "rc_signal_quality": 100,
            "transmission_signal_quality": 100,
            "flight_time": 300,
            "flight_mode": "AUTO"
        }
        """
        topic = TopicBuilder.osd(device_sn)
        payload = MessageBuilder.build_osd(self.gateway_sn, osd_data)
        return self._publish_with_cache(topic, payload, qos=0)

    # ---- 状态变更上报 ----

    def report_state(self, device_sn: str, state_data: Dict[str, Any]) -> bool:
        """
        上报状态变更

        标准格式（物模型属性）：
        {
            "property": {
                "cover_state": 1,
                "charging_state": 1,
                "drone_in_dock": 0
            }
        }
        """
        topic = TopicBuilder.state(device_sn)
        payload = MessageBuilder.build_state(self.gateway_sn, state_data)
        return self._publish_with_cache(topic, payload, qos=1)

    # ---- 事件上报 ----

    def report_event(self, method: str, data: Dict[str, Any]) -> bool:
        """
        上报通用事件

        Args:
            method: 事件方法名
            data: 事件数据
        """
        topic = TopicBuilder.events(self.gateway_sn)
        payload = MessageBuilder.build_event(method, data, self.gateway_sn)
        return self._publish_with_cache(topic, payload, qos=1)

    # ---- 断线缓存与恢复机制 ----

    def _publish_with_cache(self, topic: str, payload: dict, qos: int = 1) -> bool:
        """
        带缓存的发布方法

        如果MQTT未连接，将消息缓存到本地，待连接恢复后重发
        """
        if self.mqtt.connected:
            return self.mqtt.publish(topic, payload, qos=qos)
        else:
            self._add_to_cache(topic, payload, qos)
            return False

    def _add_to_cache(self, topic: str, payload: dict, qos: int):
        """
        添加消息到缓存

        Args:
            topic: 消息主题
            payload: 消息内容
            qos: QoS级别
        """
        with self._cache_lock:
            # 清理过期缓存
            self._clean_old_cache_locked()

            # 添加新缓存
            cache_item = {
                'topic': topic,
                'payload': payload,
                'qos': qos,
                'timestamp': int(time.time())
            }

            # 限制缓存大小
            if len(self._cache) >= MAX_CACHE_SIZE:
                self._cache.pop(0)

            self._cache.append(cache_item)
            logger.debug(f"Message cached: topic={topic}, cache_size={len(self._cache)}")

    def _clean_old_cache_locked(self):
        """清理过期缓存。调用方必须持有 _cache_lock。"""
        now = int(time.time())
        self._cache = [item for item in self._cache
                       if now - item['timestamp'] <= MAX_CACHE_TIME]

    def _recover_cache(self):
        """恢复缓存的消息"""
        if not self.mqtt.connected:
            return

        with self._cache_lock:
            cache_copy = self._cache.copy()
            self._cache.clear()

        logger.info(f"Recovering {len(cache_copy)} cached messages...")

        for item in cache_copy:
            success = self.mqtt.publish(item['topic'], item['payload'], item['qos'])
            if not success:
                self._add_to_cache(item['topic'], item['payload'], item['qos'])

        logger.info(f"Cache recovery completed, {len(self._cache)} messages remain")

    def _on_mqtt_connect(self):
        """MQTT连接成功回调"""
        logger.info("MQTT reconnected, recovering cached messages")
        self._recover_cache()

    # ---- 飞行任务事件 ----

    def report_task_ready(self, task_id: str, waypoint_count: int):
        """上报任务准备就绪"""
        self.report_event('task_ready_notify', {
            'task_id': task_id,
            'status': 'ready',
            'waypoint_count': waypoint_count
        })

    def report_task_progress(self, task_id: str, current: int, total: int,
                             status: str = "executing"):
        """上报任务进度"""
        self.report_event('flight_task_progress_notify', {
            'task_id': task_id,
            'current_waypoint': current,
            'total_waypoints': total,
            'percentage': int(current / total * 100) if total > 0 else 0,
            'status': status
        })

    def report_task_complete(self, task_id: str):
        """上报任务完成"""
        self.report_event('flight_task_progress_notify', {
            'task_id': task_id,
            'status': 'completed',
            'percentage': 100
        })

    def report_flyto_progress(self, fly_to_id: str, status: str, result: int = 0):
        """上报flyto进度"""
        self.report_event('fly_to_point_progress', {
            'fly_to_id': fly_to_id,
            'status': status,
            'result': result
        })

    def report_takeoff_progress(self, status: str, **kwargs):
        """上报起飞进度"""
        self.report_event('take_off_progress', {
            'status': status,
            **kwargs
        })

    def report_setup_exception(self, exception_type: str, error_code: int, **kwargs):
        """上报准备阶段异常"""
        self.report_event('flight_setup_exception_notify', {
            'exception_type': exception_type,
            'error_code': error_code,
            **kwargs
        })

    # ---- OTA事件 ----

    def report_ota_progress(self, status: str, progress: int, step: str = ""):
        """上报OTA进度"""
        self.report_event('ota_progress', {
            'status': status,
            'progress': progress,
            'step': step
        })

    def report_ota_result(self, success: bool, error_code: int = 0, error_msg: str = ""):
        """上报OTA结果"""
        self.report_event('ota_result', {
            'result': 0 if success else -1,
            'error_code': error_code,
            'error_msg': error_msg
        })

    # ---- 媒体事件 ----

    def report_file_upload_complete(self, file_info: Dict[str, Any]):
        """上报文件上传完成"""
        self.report_event('file_upload_callback', {
            'file': {
                'object_key': file_info.get('object_key', ''),
                'path': file_info.get('path', ''),
                'name': file_info.get('name', ''),
                'ext': {
                    'flight_id': file_info.get('flight_id', ''),
                    'drone_model_key': file_info.get('drone_model_key', ''),
                    'payload_model_key': file_info.get('payload_model_key', ''),
                    'is_original': file_info.get('is_original', True)
                }
            }
        })

    def report_priority_media(self, flighttask_id: str):
        """上报最高优先级任务媒体"""
        self.report_event('highest_priority_upload_flighttask_media', {
            'flighttask_id': flighttask_id
        })

    # ---- HMS告警 ----

    def report_hms_alarm(self, alarm_id: str, alarm_level: int,
                         alarm_content: str, **kwargs):
        """
        上报HMS告警

        Args:
            alarm_id: 告警ID
            alarm_level: 0=提示 1=一般 2=严重 3=紧急
            alarm_content: 告警内容
        """
        alarm_logger.info(
            "HMS alarm publish: alarm_id=%s level=%s content=%s",
            alarm_id,
            alarm_level,
            alarm_content,
        )
        self.report_event('hms_push', {
            'alarm_id': alarm_id,
            'alarm_level': alarm_level,
            'alarm_content': alarm_content,
            'create_time': int(time.time() * 1000),
            **kwargs
        })

    # ---- DRC事件 ----

    def report_drc_status(self, enabled: bool, session_id: str = ""):
        """上报DRC状态变更"""
        self.report_event('drc_status_notify', {
            'drc_enabled': enabled,
            'drc_session_id': session_id
        })

    # ---- 直播事件 ----

    def report_live_status(self, session_id: str, status: str, **kwargs):
        """上报直播状态"""
        self.report_event('live_status_notify', {
            'session_id': session_id,
            'status': status,
            **kwargs
        })
