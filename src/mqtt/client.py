"""
MQTT客户端模块
连接NanoMQ本地Broker，处理消息发布/订阅
"""
import json
import time
import threading
from typing import Callable, Dict, Optional
import paho.mqtt.client as mqtt

from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import TopicBuilder, parse_message

logger = get_logger("mqtt")


class MQTTClient:
    """
    MQTT客户端 - 连接NanoMQ本地Broker

    职责:
    1. 维护与NanoMQ的连接(含自动重连)
    2. 订阅下行Topic(services, property/set, drc/down)
    3. 发布上行数据(osd, state, events, replies)
    4. 消息路由到对应处理器
    """

    def __init__(self):
        self.config = Config.get_mqtt_config()
        self.gateway_sn = Config.get_gateway_sn()
        self.client: Optional[mqtt.Client] = None
        self.connected = False
        self._handlers: Dict[str, Callable] = {}  # topic -> handler
        self._topic_handlers: Dict[str, Callable] = {}  # method -> handler
        self._status_reply_handler: Optional[Callable] = None
        self._requests_reply_handler: Optional[Callable] = None
        self._lock = threading.Lock()
        self._running = False

    def connect(self) -> bool:
        """连接到NanoMQ Broker"""
        try:
            self.client = mqtt.Client(
                client_id=f"edge_gateway_{self.gateway_sn}",
                clean_session=False
            )
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

            # 设置用户名密码(如配置)
            username = self.config.get('username')
            password = self.config.get('password')
            if username:
                self.client.username_pw_set(username, password)

            host = self.config['host']
            port = self.config['port']
            logger.info(f"Connecting to NanoMQ at {host}:{port}...")
            self.client.connect(host, port, keepalive=60)

            self.client.loop_start()
            self._running = True

            # 等待连接确认
            for _ in range(10):
                if self.connected:
                    break
                time.sleep(0.5)

            return self.connected

        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")
            return False

    def disconnect(self):
        """断开连接"""
        self._running = False
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        self.connected = False
        logger.info("MQTT disconnected")

    def subscribe_downstream(self):
        """
        订阅所有下行Topic
        - services: 云端服务调用
        - property/set: 属性设置
        - drc/down: DRC控制指令
        """
        topics = [
            TopicBuilder.services(self.gateway_sn),
            TopicBuilder.property_set(self.gateway_sn),
            TopicBuilder.drc_down(self.gateway_sn),
            TopicBuilder.events_reply(self.gateway_sn),
            TopicBuilder.requests_reply(self.gateway_sn),
            TopicBuilder.services_reply(self.gateway_sn),
            TopicBuilder.status_reply(self.gateway_sn),
            TopicBuilder.requests(self.gateway_sn),
        ]
        for topic in topics:
            self.client.subscribe(topic, qos=1)
            logger.info(f"Subscribed: {topic}")

    def publish(self, topic: str, payload: dict, qos: int = 1) -> bool:
        """发布消息到指定Topic"""
        if not self.connected or not self.client:
            logger.warning(f"Cannot publish, not connected. Topic: {topic}")
            return False
        try:
            message = json.dumps(payload, ensure_ascii=False)
            result = self.client.publish(topic, message, qos=qos)
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            logger.error(f"Publish error: {e}")
            return False

    # ---- 便捷发布方法 ----

    def publish_osd(self, device_sn: str, data: dict):
        """发布OSD定频数据"""
        topic = TopicBuilder.osd(device_sn)
        self.publish(topic, data, qos=0)

    def publish_state(self, device_sn: str, data: dict):
        """发布状态变更"""
        topic = TopicBuilder.state(device_sn)
        self.publish(topic, data, qos=1)

    def publish_event(self, data: dict):
        """发布事件上报"""
        topic = TopicBuilder.events(self.gateway_sn)
        self.publish(topic, data, qos=1)

    def publish_service_reply(self, data: dict):
        """发布服务响应"""
        topic = TopicBuilder.services_reply(self.gateway_sn)
        self.publish(topic, data, qos=1)

    def publish_property_reply(self, data: dict):
        """发布属性设置响应"""
        topic = TopicBuilder.property_set_reply(self.gateway_sn)
        self.publish(topic, data, qos=1)

    def publish_drc_up(self, data: dict):
        """发布DRC上行数据"""
        topic = TopicBuilder.drc_up(self.gateway_sn)
        self.publish(topic, data, qos=1)

    def publish_status(self, data: dict):
        """发布设备状态/拓扑上报 (sys/product/{sn}/status)"""
        topic = TopicBuilder.status(self.gateway_sn)
        self.publish(topic, data, qos=1)

    def publish_requests(self, data: dict):
        """发布设备请求 (thing/product/{sn}/requests)"""
        topic = TopicBuilder.requests(self.gateway_sn)
        self.publish(topic, data, qos=1)

    # ---- 处理器注册 ----

    def register_handler(self, method: str, handler: Callable):
        """
        注册API方法处理器

        Args:
            method: API方法名(如 'flyto', 'camera_photo_take')
            handler: 处理函数(msg: dict) -> dict
        """
        self._topic_handlers[method] = handler
        logger.debug(f"Registered handler for method: {method}")

    def unregister_handler(self, method: str):
        """注销处理器"""
        self._topic_handlers.pop(method, None)

    def set_status_reply_handler(self, handler: Callable):
        """设置 status_reply 处理器"""
        self._status_reply_handler = handler

    def set_requests_reply_handler(self, handler: Callable):
        """设置 requests_reply 处理器"""
        self._requests_reply_handler = handler

    # ---- 回调 ----

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            logger.info("MQTT connected to NanoMQ")
            self.subscribe_downstream()
        else:
            logger.error(f"MQTT connection failed, rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            logger.warning(f"MQTT unexpected disconnect, rc={rc}")

    def _on_message(self, client, userdata, msg):
        """消息回调 - 路由到对应处理器"""
        try:
            payload = parse_message(msg.payload)
            if not payload:
                logger.warning(f"Failed to parse message on {msg.topic}")
                return

            method = payload.get('method', '')
            if not method:
                logger.warning(f"No method in message on {msg.topic}")
                return

            # 特殊处理 sys/product/{sn}/status_reply
            if msg.topic.endswith('/status_reply'):
                if self._status_reply_handler:
                    try:
                        self._status_reply_handler(method, payload)
                    except Exception as e:
                        logger.error(f"Status reply handler error for {method}: {e}")
                else:
                    result = payload.get('data', {}).get('result', -1)
                    logger.info(f"Status reply received: method={method}, result={result}")
                return

            # 特殊处理 thing/product/{sn}/requests_reply
            if msg.topic.endswith('/requests_reply'):
                if self._requests_reply_handler:
                    try:
                        self._requests_reply_handler(method, payload)
                    except Exception as e:
                        logger.error(f"Requests reply handler error for {method}: {e}")
                else:
                    result = payload.get('data', {}).get('result', -1)
                    logger.info(f"Requests reply received: method={method}, result={result}")
                return

            handler = self._topic_handlers.get(method)
            if handler:
                try:
                    result = handler(payload)
                    # 如果需要响应，自动发送
                    if payload.get('need_reply') == 1 and result:
                        self.publish_service_reply(result)
                except Exception as e:
                    logger.error(f"Handler error for {method}: {e}")
            else:
                logger.warning(f"No handler registered for method: {method}")

        except Exception as e:
            logger.error(f"Message processing error: {e}")
