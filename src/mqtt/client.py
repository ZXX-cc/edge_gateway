"""
MQTT客户端模块
连接NanoMQ本地Broker，处理消息发布/订阅
"""
import json
import time
import threading
from typing import Callable, Dict, Optional, List
import paho.mqtt.client as mqtt

from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import TopicBuilder, parse_message
from src.utils.field_labels import format_with_labels

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
        self._connect_callbacks: List[Callable] = []
        self._lock = threading.Lock()
        self._running = False
        self._connect_count = 0
        self._last_osd_log_time = 0

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
            self.client.reconnect_delay_set(min_delay=1, max_delay=30)

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
            logger.error(f"MQTT connection failed: {e}", exc_info=True)
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
            logger.error(f"Publish error: {e}", exc_info=True)
            return False

    # ---- 便捷发布方法 ----

    def publish_osd(self, device_sn: str, data: dict):
        """发布OSD定频数据"""
        topic = TopicBuilder.osd(device_sn)
        now = time.time()
        if now - self._last_osd_log_time >= 30:
            self._last_osd_log_time = now
            logger.info(
                "MQTT OSD sample: topic=%s, data=%s",
                topic,
                format_with_labels(data, max_length=2000)
            )
        self.publish(topic, data, qos=0)

    def publish_state(self, device_sn: str, data: dict):
        """发布状态变更"""
        topic = TopicBuilder.state(device_sn)
        logger.info(
            "MQTT state publish: topic=%s, data=%s",
            topic,
            format_with_labels(data, max_length=3000)
        )
        self.publish(topic, data, qos=1)

    def publish_event(self, data: dict):
        """发布事件上报"""
        topic = TopicBuilder.events(self.gateway_sn)
        if data.get('method') == 'heartbeat':
            logger.debug(
                "MQTT heartbeat event publish: topic=%s, data=%s",
                topic,
                format_with_labels(data, max_length=1000)
            )
        else:
            logger.info(
                "MQTT event publish: topic=%s, method=%s, data=%s",
                topic,
                data.get('method'),
                format_with_labels(data, max_length=3000)
            )
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

    @staticmethod
    def _is_service_reply_method(method: str) -> bool:
        """Dock3 中这些 DRC 方法仍走 services/services_reply。"""
        return method in {
            "drc_mode_enter",
            "drc_mode_exit",
            "takeoff_to_point",
        }

    @staticmethod
    def _build_error_reply(payload: dict, code: int, message: str, gateway_sn: str) -> dict:
        return {
            "bid": payload.get("bid", ""),
            "data": {"result": code},
            "gateway": payload.get("gateway", gateway_sn),
            "tid": payload.get("tid", ""),
            "timestamp": int(time.time() * 1000),
            "method": payload.get("method", ""),
            "msg": message
        }

    @staticmethod
    def _reply_result(data: dict):
        if not isinstance(data, dict):
            return None
        return data.get('data', {}).get('result')

    def _publish_reply_for_downstream_topic(self, source_topic: str, data: dict) -> bool:
        """
        按下行Topic选择对应响应Topic。

        services        -> services_reply
        property/set    -> property/set_reply
        drc/down        -> drc/up
        """
        method = data.get('method', '')
        if self._is_service_reply_method(method):
            reply_topic = TopicBuilder.services_reply(self.gateway_sn)
        elif source_topic.endswith('/property/set'):
            reply_topic = TopicBuilder.property_set_reply(self.gateway_sn)
        elif source_topic.endswith('/drc/down'):
            reply_topic = TopicBuilder.drc_up(self.gateway_sn)
        else:
            reply_topic = TopicBuilder.services_reply(self.gateway_sn)

        ok = self.publish(reply_topic, data, qos=1)
        logger.info(
            "MQTT reply sent: source_topic=%s, reply_topic=%s, method=%s, tid=%s, result=%s, ok=%s, data=%s",
            source_topic,
            reply_topic,
            method,
            data.get('tid'),
            self._reply_result(data),
            ok,
            format_with_labels(data, max_length=3000)
        )
        return ok

    def _publish_reply_for_source_topic(self, source_topic: str, data: dict) -> bool:
        """按来源topic原样选择响应topic，用于错误提示避免测试端超时。"""
        if source_topic.endswith('/property/set'):
            reply_topic = TopicBuilder.property_set_reply(self.gateway_sn)
        elif source_topic.endswith('/drc/down'):
            reply_topic = TopicBuilder.drc_up(self.gateway_sn)
        else:
            reply_topic = TopicBuilder.services_reply(self.gateway_sn)

        ok = self.publish(reply_topic, data, qos=1)
        logger.info(
            "MQTT error reply sent: source_topic=%s, reply_topic=%s, method=%s, tid=%s, result=%s, msg=%s, ok=%s, data=%s",
            source_topic,
            reply_topic,
            data.get('method'),
            data.get('tid'),
            self._reply_result(data),
            data.get('msg'),
            ok,
            format_with_labels(data, max_length=3000)
        )
        return ok

    def _validate_downstream_topic(self, source_topic: str, method: str) -> Optional[str]:
        """校验当前已确认的 Dock3 method 与 topic 对应关系。"""
        if method == "property_set" and not source_topic.endswith('/property/set'):
            return "property_set must be sent to thing/product/{sn}/property/set"
        if self._is_service_reply_method(method) and not source_topic.endswith('/services'):
            return f"{method} must be sent to thing/product/{{sn}}/services"
        return None

    def publish_status(self, data: dict):
        """发布设备状态/拓扑上报 (sys/product/{sn}/status)"""
        topic = TopicBuilder.status(self.gateway_sn)
        logger.info(
            "MQTT status publish: topic=%s, data=%s",
            topic,
            format_with_labels(data, max_length=3000)
        )
        self.publish(topic, data, qos=1)

    def publish_requests(self, data: dict):
        """发布设备请求 (thing/product/{sn}/requests)"""
        topic = TopicBuilder.requests(self.gateway_sn)
        logger.info(
            "MQTT request publish: topic=%s, method=%s, data=%s",
            topic,
            data.get('method'),
            format_with_labels(data, max_length=3000)
        )
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

    def register_connect_callback(self, callback: Callable):
        """注册连接成功回调"""
        if callback not in self._connect_callbacks:
            self._connect_callbacks.append(callback)

    def _notify_connect_callbacks(self):
        """通知所有连接回调"""
        for callback in self._connect_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"Connect callback error: {e}", exc_info=True)

    # ---- 回调 ----

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            self._connect_count += 1
            event = "connected" if self._connect_count == 1 else "reconnected"
            logger.info(
                "MQTT %s to broker: host=%s, port=%s, client_id=edge_gateway_%s",
                event,
                self.config.get('host'),
                self.config.get('port'),
                self.gateway_sn
            )
            self.subscribe_downstream()
            self._notify_connect_callbacks()
        else:
            logger.error(f"MQTT connection failed, rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            logger.warning(
                "MQTT unexpected disconnect: rc=%s, running=%s, reconnect=auto",
                rc,
                self._running
            )
        else:
            logger.info("MQTT disconnected cleanly")

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

            logger.info(
                "MQTT command received: topic=%s, method=%s, tid=%s, bid=%s, need_reply=%s, payload_bytes=%s, payload=%s",
                msg.topic,
                method,
                payload.get('tid'),
                payload.get('bid'),
                payload.get('need_reply'),
                len(msg.payload or b''),
                format_with_labels(payload, max_length=3000)
            )

            # 特殊处理 sys/product/{sn}/status_reply
            if msg.topic.endswith('/status_reply'):
                if self._status_reply_handler:
                    try:
                        self._status_reply_handler(method, payload)
                    except Exception as e:
                        logger.error(f"Status reply handler error for {method}: {e}", exc_info=True)
                else:
                    result = payload.get('data', {}).get('result', -1)
                    logger.info(
                        "Status reply received: method=%s, result=%s, payload=%s",
                        method,
                        result,
                        format_with_labels(payload, max_length=3000)
                    )
                return

            # 特殊处理 thing/product/{sn}/requests_reply
            if msg.topic.endswith('/requests_reply'):
                if self._requests_reply_handler:
                    try:
                        self._requests_reply_handler(method, payload)
                    except Exception as e:
                        logger.error(f"Requests reply handler error for {method}: {e}", exc_info=True)
                else:
                    result = payload.get('data', {}).get('result', -1)
                    logger.info(
                        "Requests reply received: method=%s, result=%s, payload=%s",
                        method,
                        result,
                        format_with_labels(payload, max_length=3000)
                    )
                return

            # services_reply/events_reply 是网关上行后的云端确认或本客户端自发回包，
            # 不能再次路由到业务handler，否则会造成重复执行。
            if msg.topic.endswith('/services_reply') or msg.topic.endswith('/events_reply'):
                result = payload.get('data', {}).get('result', -1)
                logger.info(
                    "Upstream reply received: topic=%s, method=%s, result=%s, payload=%s",
                    msg.topic,
                    method,
                    result,
                    format_with_labels(payload, max_length=3000)
                )
                return

            handler = self._topic_handlers.get(method)
            if handler:
                try:
                    topic_error = self._validate_downstream_topic(msg.topic, method)
                    if topic_error:
                        logger.warning(f"Invalid topic for method={method}: {topic_error}")
                        if payload.get('need_reply') == 1:
                            error_reply = self._build_error_reply(payload, -3, topic_error, self.gateway_sn)
                            self._publish_reply_for_source_topic(msg.topic, error_reply)
                        return

                    result = handler(payload)
                    logger.info(
                        "MQTT handler completed: method=%s, tid=%s, category_topic=%s, result=%s, reply=%s",
                        method,
                        payload.get('tid'),
                        msg.topic,
                        self._reply_result(result),
                        format_with_labels(result, max_length=3000)
                    )
                    # 如果需要响应，自动发送
                    if payload.get('need_reply') == 1 and result:
                        self._publish_reply_for_downstream_topic(msg.topic, result)
                except Exception as e:
                    logger.error(f"Handler error for {method}: {e}", exc_info=True)
                    if payload.get('need_reply') == 1:
                        error_reply = self._build_error_reply(
                            payload,
                            -1,
                            f"Handler error for {method}: {e}",
                            self.gateway_sn,
                        )
                        self._publish_reply_for_downstream_topic(msg.topic, error_reply)
            else:
                logger.warning(f"No handler registered for method: {method}")
                if payload.get('need_reply') == 1:
                    error_reply = self._build_error_reply(
                        payload,
                        -2,
                        f"No handler registered for method: {method}",
                        self.gateway_sn,
                    )
                    self._publish_reply_for_downstream_topic(msg.topic, error_reply)

        except Exception as e:
            logger.error(f"Message processing error: {e}", exc_info=True)
