"""
MQTT客户端模块
连接NanoMQ本地Broker，处理消息发布/订阅
"""
import json
import os
import time
import threading
import uuid
import copy
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Dict, Optional, List
import paho.mqtt.client as mqtt

from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.command_trace import trace_command
from src.utils.message import MessageBuilder, TopicBuilder, parse_message
from src.utils.field_labels import format_with_labels
from src.utils.dji_error_codes import (
    describe_error,
    normalize_error_code,
    normalize_reply_error_codes,
)
from src.utils.request_trace import get_request_trace_recorder

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
        # 仅写入本地 trace/command trace，不放入 Dock3 MQTT payload；用于在共享
        # broker 中识别“当前网关发布”与外部旧进程的同 tid 回包。
        self._trace_instance = f"{self.gateway_sn}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        # 文件扫描、媒体删除等本地 IO 不能占用 Paho 的网络回调线程。
        # 控制类 method 仍保持原有串行调用，以免改变设备状态机的执行顺序。
        worker_count = self.config.get('background_handler_workers', 2)
        try:
            self._background_worker_count = max(1, int(worker_count))
        except (TypeError, ValueError):
            self._background_worker_count = 2
        self._background_executor: Optional[ThreadPoolExecutor] = None
        self._background_methods = set()
        self._background_futures: set[Future] = set()
        self._background_lock = threading.Lock()
        self._trace = get_request_trace_recorder()
        self._trace.start()
        self._dedup_lock = threading.Lock()
        self._dedup_cache = OrderedDict()
        self._dedup_inflight = set()
        self._dedup_ttl_seconds = max(10, int(self.config.get('dedup_ttl_seconds', 300)))
        self._dedup_max_entries = max(32, int(self.config.get('dedup_max_entries', 1024)))

    @staticmethod
    def _dedup_key(source_topic: str, payload: dict):
        tid = str(payload.get("tid") or "").strip()
        method = str(payload.get("method") or "").strip()
        if not tid or not method:
            return None
        return source_topic, method, tid

    def _cached_downstream_reply(self, source_topic: str, payload: dict):
        key = self._dedup_key(source_topic, payload)
        if key is None:
            return None
        now = time.monotonic()
        with self._dedup_lock:
            while self._dedup_cache:
                oldest_key, oldest = next(iter(self._dedup_cache.items()))
                if now - oldest["created_at"] <= self._dedup_ttl_seconds:
                    break
                self._dedup_cache.pop(oldest_key, None)
            cached = self._dedup_cache.get(key)
            if not cached:
                return None
            self._dedup_cache.move_to_end(key)
            return copy.deepcopy(cached["reply"])

    def _remember_downstream_reply(self, source_topic: str, payload: dict, reply: dict):
        key = self._dedup_key(source_topic, payload)
        if key is None or not isinstance(reply, dict):
            return
        with self._dedup_lock:
            self._dedup_cache[key] = {
                "created_at": time.monotonic(),
                "reply": copy.deepcopy(reply),
            }
            self._dedup_inflight.discard(key)
            self._dedup_cache.move_to_end(key)
            while len(self._dedup_cache) > self._dedup_max_entries:
                self._dedup_cache.popitem(last=False)

    def _claim_downstream_request(self, source_topic: str, payload: dict) -> bool:
        key = self._dedup_key(source_topic, payload)
        if key is None:
            return True
        with self._dedup_lock:
            if key in self._dedup_inflight:
                return False
            self._dedup_inflight.add(key)
            return True

    def _release_downstream_request(self, source_topic: str, payload: dict):
        key = self._dedup_key(source_topic, payload)
        if key is None:
            return
        with self._dedup_lock:
            self._dedup_inflight.discard(key)

    def connect(self) -> bool:
        """连接到NanoMQ Broker"""
        try:
            self._ensure_background_executor()
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
            self._shutdown_background_executor()
            logger.error(f"MQTT connection failed: {e}", exc_info=True)
            return False

    def disconnect(self):
        """断开连接"""
        self._running = False
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        self.connected = False
        self._shutdown_background_executor()
        self._trace.record("mqtt_disconnected", direction="system", gateway=self.gateway_sn)
        logger.info("MQTT disconnected")

    def _ensure_background_executor(self):
        """按需创建后台 IO 处理器，允许同一客户端在断开后重新连接。"""
        with self._background_lock:
            if self._background_executor is None:
                self._background_executor = ThreadPoolExecutor(
                    max_workers=self._background_worker_count,
                    thread_name_prefix="mqtt-io",
                )

    def _shutdown_background_executor(self):
        """停止接收新的后台 IO 任务，不等待文件扫描或删除完成。"""
        with self._background_lock:
            executor = self._background_executor
            self._background_executor = None
            self._background_futures.clear()
        if executor is None:
            return
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # Python 3.8 兼容
            executor.shutdown(wait=False)

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

    def _trace_publish(
        self,
        event: str,
        topic: str,
        payload: dict,
        *,
        direction: str = "out",
        ok: Optional[bool] = None,
        source_topic: str = "",
        elapsed_ms: Optional[int] = None,
    ):
        extra = {}
        if ok is not None:
            extra["publish_ok"] = ok
        if source_topic:
            extra["source_topic"] = source_topic
        extra["publisher_pid"] = os.getpid()
        extra["publisher_instance"] = self._trace_instance
        self._trace.record(
            event,
            direction=direction,
            topic=topic,
            payload=payload,
            result=self._reply_result(payload),
            elapsed_ms=elapsed_ms,
            extra=extra,
        )

    def publish_osd(self, device_sn: str, data: dict):
        """发布OSD定频数据"""
        topic = TopicBuilder.osd(device_sn)
        payload = MessageBuilder.build_osd(self.gateway_sn, data)
        now = time.time()
        if now - self._last_osd_log_time >= 30:
            self._last_osd_log_time = now
            logger.info(self._format_log_block("MQTT OSD SAMPLE", [
                ("topic", topic),
                ("data", format_with_labels(payload, max_length=2000)),
            ]))
        self.publish(topic, payload, qos=0)

    def publish_state(self, device_sn: str, data: dict):
        """发布状态变更"""
        topic = TopicBuilder.state(device_sn)
        payload = MessageBuilder.build_state(self.gateway_sn, data)
        logger.info(self._format_log_block("MQTT STATE PUBLISH", [
            ("topic", topic),
            ("data", format_with_labels(payload, max_length=3000)),
        ]))
        self.publish(topic, payload, qos=1)

    def publish_event(self, data: dict):
        """发布事件上报"""
        topic = TopicBuilder.events(self.gateway_sn)
        data = self._normalize_reply_for_publish(topic, data)
        if data.get('method') == 'heartbeat':
            logger.debug(
                "MQTT heartbeat event publish: topic=%s, data=%s",
                topic,
                format_with_labels(data, max_length=1000)
            )
        else:
            logger.info(self._format_log_block("MQTT EVENT PUBLISH", [
                ("topic", topic),
                ("method", data.get('method')),
                ("data", format_with_labels(data, max_length=3000)),
            ]))
        ok = self.publish(topic, data, qos=1)
        if data.get('method') != 'heartbeat':
            self._trace_publish("mqtt_publish_event", topic, data, ok=ok)

    def publish_service_reply(self, data: dict):
        """发布服务响应"""
        topic = TopicBuilder.services_reply(self.gateway_sn)
        data = self._normalize_reply_for_publish(TopicBuilder.services(self.gateway_sn), data)
        ok = self.publish(topic, data, qos=1)
        self._trace_publish("mqtt_publish_services_reply", topic, data, ok=ok, source_topic=TopicBuilder.services(self.gateway_sn))

    def publish_property_reply(self, data: dict):
        """发布属性设置响应"""
        topic = TopicBuilder.property_set_reply(self.gateway_sn)
        data = self._normalize_reply_for_publish(TopicBuilder.property_set(self.gateway_sn), data)
        ok = self.publish(topic, data, qos=1)
        self._trace_publish("mqtt_publish_property_reply", topic, data, ok=ok, source_topic=TopicBuilder.property_set(self.gateway_sn))

    def publish_drc_up(self, data: dict):
        """发布DRC上行数据"""
        topic = TopicBuilder.drc_up(self.gateway_sn)
        data = self._normalize_reply_for_publish(TopicBuilder.drc_down(self.gateway_sn), data)
        ok = self.publish(topic, data, qos=1)
        self._trace_publish("mqtt_publish_drc_up", topic, data, ok=ok, source_topic=TopicBuilder.drc_down(self.gateway_sn))

    @staticmethod
    def _is_service_reply_method(method: str) -> bool:
        """Dock3 中这些 DRC 方法仍走 services/services_reply。"""
        return method in {
            "drc_mode_enter",
            "drc_mode_exit",
            "takeoff_to_point",
        }

    @staticmethod
    def _build_error_reply(
        payload: dict,
        code: int,
        message: str,
        gateway_sn: str,
        context: str = "generic",
    ) -> dict:
        dji_code = normalize_error_code(code, context=context)
        dji_message = message or describe_error(dji_code)
        return {
            "bid": payload.get("bid", ""),
            "data": {"result": dji_code},
            "gateway": payload.get("gateway", gateway_sn),
            "tid": payload.get("tid", ""),
            "timestamp": int(time.time() * 1000),
            "method": payload.get("method", ""),
            "msg": dji_message
        }

    @staticmethod
    def _reply_result(data: dict):
        if not isinstance(data, dict):
            return None
        return data.get('data', {}).get('result')

    def _reply_context(self, source_topic: str, method: str) -> str:
        if source_topic.endswith('/property/set'):
            return "property"
        if source_topic.endswith('/drc/down') and self._is_service_reply_method(method):
            return "invalid_request"
        return "generic"

    def _normalize_reply_for_publish(self, source_topic: str, data: dict) -> dict:
        method = data.get('method', '') if isinstance(data, dict) else ''
        context = self._reply_context(source_topic, method)
        normalized, conversions = normalize_reply_error_codes(data, context=context)
        for conversion in conversions:
            logger.warning(
                "DJI error code normalized: source_topic=%s, method=%s, tid=%s, path=%s, original_result=%s, dji_result=%s, description=%s",
                source_topic,
                method,
                normalized.get('tid'),
                conversion.get('path'),
                conversion.get('original_result'),
                conversion.get('dji_result'),
                conversion.get('description'),
            )
        return normalized

    def _publish_reply_for_downstream_topic(self, source_topic: str, data: dict) -> bool:
        """
        按下行Topic选择对应响应Topic。

        services        -> services_reply
        property/set    -> property/set_reply
        drc/down        -> drc/up
        """
        data = self._normalize_reply_for_publish(source_topic, data)
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
        trace_command(
            "mqtt_reply_sent",
            source_topic=source_topic,
            reply_topic=reply_topic,
            method=method,
            tid=data.get("tid"),
            result=self._reply_result(data),
            ok=ok,
        )
        self._trace_publish(
            "mqtt_publish_reply",
            reply_topic,
            data,
            ok=ok,
            source_topic=source_topic,
        )
        logger.info(self._format_log_block("MQTT REPLY SENT", [
            ("source_topic", source_topic),
            ("reply_topic", reply_topic),
            ("method", method),
            ("tid", data.get('tid')),
            ("result", self._reply_result(data)),
            ("ok", ok),
            ("data", format_with_labels(data, max_length=3000)),
        ]))
        return ok

    def _publish_reply_for_source_topic(self, source_topic: str, data: dict) -> bool:
        """按来源topic原样选择响应topic，用于错误提示避免测试端超时。"""
        data = self._normalize_reply_for_publish(source_topic, data)
        if source_topic.endswith('/property/set'):
            reply_topic = TopicBuilder.property_set_reply(self.gateway_sn)
        elif source_topic.endswith('/drc/down'):
            reply_topic = TopicBuilder.drc_up(self.gateway_sn)
        else:
            reply_topic = TopicBuilder.services_reply(self.gateway_sn)

        ok = self.publish(reply_topic, data, qos=1)
        trace_command(
            "mqtt_error_reply_sent",
            source_topic=source_topic,
            reply_topic=reply_topic,
            method=data.get("method"),
            tid=data.get("tid"),
            result=self._reply_result(data),
            msg=data.get("msg"),
            ok=ok,
        )
        self._trace_publish(
            "mqtt_publish_error_reply",
            reply_topic,
            data,
            ok=ok,
            source_topic=source_topic,
        )
        logger.info(self._format_log_block("MQTT ERROR REPLY SENT", [
            ("source_topic", source_topic),
            ("reply_topic", reply_topic),
            ("method", data.get('method')),
            ("tid", data.get('tid')),
            ("result", self._reply_result(data)),
            ("msg", data.get('msg')),
            ("ok", ok),
            ("data", format_with_labels(data, max_length=3000)),
        ]))
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
        logger.info(self._format_log_block("MQTT STATUS PUBLISH", [
            ("topic", topic),
            ("data", format_with_labels(data, max_length=3000)),
        ]))
        ok = self.publish(topic, data, qos=1)
        self._trace_publish("mqtt_publish_status", topic, data, ok=ok)

    def publish_requests(self, data: dict):
        """发布设备请求 (thing/product/{sn}/requests)"""
        topic = TopicBuilder.requests(self.gateway_sn)
        logger.info(self._format_log_block("MQTT REQUEST PUBLISH", [
            ("topic", topic),
            ("method", data.get('method')),
            ("data", format_with_labels(data, max_length=3000)),
        ]))
        ok = self.publish(topic, data, qos=1)
        self._trace_publish("mqtt_publish_request", topic, data, ok=ok)

    # ---- 处理器注册 ----

    def register_handler(self, method: str, handler: Callable, *, background: bool = False):
        """
        注册API方法处理器

        Args:
            method: API方法名(如 'flyto', 'camera_photo_take')
            handler: 处理函数(msg: dict) -> dict
            background: 是否在后台 IO 工作线程执行。仅用于文件扫描、删除等
                不访问实时状态机的耗时本地操作。
        """
        self._topic_handlers[method] = handler
        if background:
            self._background_methods.add(method)
        else:
            self._background_methods.discard(method)
        logger.debug(f"Registered handler for method: {method}")

    def unregister_handler(self, method: str):
        """注销处理器"""
        self._topic_handlers.pop(method, None)
        self._background_methods.discard(method)

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

    @staticmethod
    def _format_log_block(title: str, rows: list) -> str:
        width = max([len(str(key)) for key, _ in rows] + [0])
        lines = ["", f"========== {title} =========="]
        for key, value in rows:
            lines.append(f"{str(key):<{width}} : {value}")
        lines.append("=" * (22 + len(title)))
        return "\n".join(lines)

    # ---- 回调 ----

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            self._connect_count += 1
            event = "connected" if self._connect_count == 1 else "reconnected"
            self._trace.record(
                "mqtt_connected",
                direction="system",
                gateway=self.gateway_sn,
                extra={
                    "host": self.config.get('host'),
                    "port": self.config.get('port'),
                    "connect_count": self._connect_count,
                    "event": event,
                },
            )
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
        self._trace.record(
            "mqtt_disconnected",
            direction="system",
            gateway=self.gateway_sn,
            result=rc,
            extra={"running": self._running},
        )
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
        started = time.perf_counter()
        payload_bytes = len(msg.payload or b'')
        try:
            payload = parse_message(msg.payload)
            if not payload:
                self._trace.record(
                    "mqtt_parse_failed",
                    direction="in",
                    topic=msg.topic,
                    payload_bytes=payload_bytes,
                    error="parse_message returned empty",
                )
                logger.warning(f"Failed to parse message on {msg.topic}")
                return

            method = payload.get('method', '')
            self._trace.record(
                "mqtt_received",
                direction="in",
                topic=msg.topic,
                payload=payload,
                payload_bytes=payload_bytes,
            )
            if not method:
                self._trace.record(
                    "mqtt_no_method",
                    direction="in",
                    topic=msg.topic,
                    payload=payload,
                    payload_bytes=payload_bytes,
                    error="missing method",
                )
                logger.warning(f"No method in message on {msg.topic}")
                return

            is_downstream_command = (
                msg.topic.endswith('/services')
                or msg.topic.endswith('/property/set')
                or msg.topic.endswith('/drc/down')
            )
            if is_downstream_command:
                trace_command(
                    "mqtt_command_received",
                    topic=msg.topic,
                    method=method,
                    tid=payload.get("tid"),
                    bid=payload.get("bid"),
                    need_reply=payload.get("need_reply"),
                    payload_bytes=len(msg.payload or b""),
                    data=payload.get("data", {}),
                )

            logger.info(self._format_log_block("MQTT COMMAND RECEIVED", [
                ("topic", msg.topic),
                ("method", method),
                ("tid", payload.get('tid')),
                ("bid", payload.get('bid')),
                ("need_reply", payload.get('need_reply')),
                ("payload_bytes", len(msg.payload or b'')),
                ("payload", format_with_labels(payload, max_length=3000)),
            ]))

            # 特殊处理 sys/product/{sn}/status_reply
            if msg.topic.endswith('/status_reply'):
                result = payload.get('data', {}).get('result', -1)
                if self._status_reply_handler:
                    try:
                        self._status_reply_handler(method, payload)
                    except Exception as e:
                        self._trace.record(
                            "mqtt_status_reply_handler_error",
                            direction="in",
                            topic=msg.topic,
                            payload=payload,
                            result=result,
                            elapsed_ms=int((time.perf_counter() - started) * 1000),
                            error=str(e),
                        )
                        logger.error(f"Status reply handler error for {method}: {e}", exc_info=True)
                else:
                    logger.info(self._format_log_block("MQTT STATUS REPLY", [
                        ("method", method),
                        ("result", result),
                        ("payload", format_with_labels(payload, max_length=3000)),
                    ]))
                self._trace.record(
                    "mqtt_status_reply_received",
                    direction="in",
                    topic=msg.topic,
                    payload=payload,
                    result=result,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                )
                return

            # 特殊处理 thing/product/{sn}/requests_reply
            if msg.topic.endswith('/requests_reply'):
                result = payload.get('data', {}).get('result', -1)
                if self._requests_reply_handler:
                    try:
                        self._requests_reply_handler(method, payload)
                    except Exception as e:
                        self._trace.record(
                            "mqtt_requests_reply_handler_error",
                            direction="in",
                            topic=msg.topic,
                            payload=payload,
                            result=result,
                            elapsed_ms=int((time.perf_counter() - started) * 1000),
                            error=str(e),
                        )
                        logger.error(f"Requests reply handler error for {method}: {e}", exc_info=True)
                else:
                    logger.info(self._format_log_block("MQTT REQUESTS REPLY", [
                        ("method", method),
                        ("result", result),
                        ("payload", format_with_labels(payload, max_length=3000)),
                    ]))
                self._trace.record(
                    "mqtt_requests_reply_received",
                    direction="in",
                    topic=msg.topic,
                    payload=payload,
                    result=result,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                )
                return

            # requests 是设备主动发给云端的上行 topic。网关和云端模拟器共用本地
            # broker 时，客户端可能收到自己发布的 requests，不能把它再路由给
            # 下行业务 handler，否则会产生错误的 services_reply。
            if msg.topic.endswith('/requests'):
                self._trace.record(
                    "mqtt_observed_device_request",
                    direction="in",
                    topic=msg.topic,
                    payload=payload,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    extra={"routing": "skipped_self_originated_request"},
                )
                logger.info(
                    "Device-originated request observed, skip downstream routing: "
                    "topic=%s, method=%s, tid=%s, payload=%s",
                    msg.topic,
                    method,
                    payload.get('tid'),
                    format_with_labels(payload, max_length=3000)
                )
                return

            # services_reply/events_reply 是网关上行后的云端确认或本客户端自发回包，
            # 不能再次路由到业务handler，否则会造成重复执行。
            if msg.topic.endswith('/services_reply') or msg.topic.endswith('/events_reply'):
                result = payload.get('data', {}).get('result', -1)
                self._trace.record(
                    "mqtt_upstream_reply_received",
                    direction="in",
                    topic=msg.topic,
                    payload=payload,
                    result=result,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                )
                logger.info(self._format_log_block("MQTT UPSTREAM REPLY", [
                    ("topic", msg.topic),
                    ("method", method),
                    ("result", result),
                    ("payload", format_with_labels(payload, max_length=3000)),
                ]))
                return

            handler = self._topic_handlers.get(method)
            if handler:
                try:
                    topic_error = self._validate_downstream_topic(msg.topic, method)
                    if topic_error:
                        if is_downstream_command:
                            trace_command(
                                "mqtt_invalid_topic",
                                topic=msg.topic,
                                method=method,
                                tid=payload.get("tid"),
                                reason=topic_error,
                            )
                        self._trace.record(
                            "mqtt_invalid_topic",
                            direction="in",
                            topic=msg.topic,
                            payload=payload,
                            elapsed_ms=int((time.perf_counter() - started) * 1000),
                            error=topic_error,
                        )
                        logger.warning(f"Invalid topic for method={method}: {topic_error}")
                        if payload.get('need_reply') == 1:
                            error_reply = self._build_error_reply(
                                payload,
                                -3,
                                topic_error,
                                self.gateway_sn,
                                context="invalid_request",
                            )
                            self._publish_reply_for_source_topic(msg.topic, error_reply)
                        return

                    if is_downstream_command:
                        cached_reply = self._cached_downstream_reply(msg.topic, payload)
                        if cached_reply is not None:
                            trace_command(
                                "mqtt_duplicate_request_replayed",
                                source_topic=msg.topic,
                                method=method,
                                tid=payload.get("tid"),
                            )
                            self._trace.record(
                                "mqtt_duplicate_request_replayed",
                                direction="in",
                                topic=msg.topic,
                                payload=payload,
                                extra={"deduplicated": True},
                            )
                            logger.warning(
                                "Duplicate MQTT request suppressed and cached reply replayed: "
                                "topic=%s, method=%s, tid=%s",
                                msg.topic,
                                method,
                                payload.get("tid"),
                            )
                            if payload.get('need_reply') == 1:
                                self._publish_reply_for_downstream_topic(msg.topic, cached_reply)
                            return
                        if not self._claim_downstream_request(msg.topic, payload):
                            trace_command(
                                "mqtt_duplicate_request_inflight_suppressed",
                                source_topic=msg.topic,
                                method=method,
                                tid=payload.get("tid"),
                            )
                            self._trace.record(
                                "mqtt_duplicate_request_inflight_suppressed",
                                direction="in",
                                topic=msg.topic,
                                payload=payload,
                                extra={"deduplicated": True, "inflight": True},
                            )
                            logger.warning(
                                "Duplicate in-flight MQTT request suppressed: "
                                "topic=%s, method=%s, tid=%s",
                                msg.topic,
                                method,
                                payload.get("tid"),
                            )
                            return

                    if method in self._background_methods:
                        self._submit_background_handler(
                            handler,
                            payload,
                            msg.topic,
                            started,
                            is_downstream_command,
                        )
                    else:
                        self._execute_handler(
                            handler,
                            payload,
                            msg.topic,
                            started,
                            is_downstream_command,
                        )
                except Exception as e:
                    self._handle_handler_exception(payload, msg.topic, started, e)
            else:
                if is_downstream_command:
                    trace_command(
                        "mqtt_no_handler",
                        topic=msg.topic,
                        method=method,
                        tid=payload.get("tid"),
                        error=f"No handler registered for method: {method}",
                    )
                self._trace.record(
                    "mqtt_no_handler",
                    direction="in",
                    topic=msg.topic,
                    payload=payload,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    error=f"No handler registered for method: {method}",
                )
                logger.warning(f"No handler registered for method: {method}")
                if payload.get('need_reply') == 1:
                    error_reply = self._build_error_reply(
                        payload,
                        -2,
                        f"No handler registered for method: {method}",
                        self.gateway_sn,
                        context="unknown_method",
                    )
                    self._publish_reply_for_downstream_topic(msg.topic, error_reply)

        except Exception as e:
            self._trace.record(
                "mqtt_processing_error",
                direction="in",
                topic=getattr(msg, "topic", ""),
                payload_bytes=payload_bytes,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                error=str(e),
            )
            logger.error(f"Message processing error: {e}", exc_info=True)

    def _submit_background_handler(
        self,
        handler: Callable,
        payload: dict,
        source_topic: str,
        started: float,
        is_downstream_command: bool,
    ):
        """把明确标记的文件 IO handler 移出 MQTT 网络回调线程。"""
        self._ensure_background_executor()
        method = payload.get("method", "")
        self._trace.record(
            "mqtt_handler_queued",
            direction="in",
            topic=source_topic,
            payload=payload,
            extra={
                "execution": "background_io",
                "gateway_pid": os.getpid(),
                "gateway_instance": self._trace_instance,
            },
        )
        if is_downstream_command:
            trace_command(
                "mqtt_handler_queued",
                source_topic=source_topic,
                method=method,
                tid=payload.get("tid"),
                execution="background_io",
                gateway_pid=os.getpid(),
                gateway_instance=self._trace_instance,
            )

        with self._background_lock:
            executor = self._background_executor
            if executor is None:
                raise RuntimeError("Background MQTT handler executor is unavailable")
            future = executor.submit(
                self._execute_handler,
                handler,
                payload,
                source_topic,
                started,
                is_downstream_command,
            )
            self._background_futures.add(future)
        future.add_done_callback(self._discard_background_future)

    def _discard_background_future(self, future: Future):
        with self._background_lock:
            self._background_futures.discard(future)

    def _execute_handler(
        self,
        handler: Callable,
        payload: dict,
        source_topic: str,
        started: float,
        is_downstream_command: bool,
    ):
        """执行 handler、记录链路耗时并按原 topic 返回 reply。"""
        method = payload.get("method", "")
        handler_started = time.perf_counter()
        try:
            result = handler(payload)
            handler_elapsed_ms = int((time.perf_counter() - handler_started) * 1000)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if is_downstream_command:
                trace_command(
                    "mqtt_handler_completed",
                    source_topic=source_topic,
                    method=method,
                    tid=payload.get("tid"),
                    result=self._reply_result(result),
                    handler_elapsed_ms=handler_elapsed_ms,
                    gateway_pid=os.getpid(),
                    gateway_instance=self._trace_instance,
                )
            self._trace.record(
                "mqtt_handler_completed",
                direction="in",
                topic=source_topic,
                payload=payload,
                result=self._reply_result(result),
                elapsed_ms=elapsed_ms,
                extra={
                    "handler_elapsed_ms": handler_elapsed_ms,
                    "execution": "background_io" if method in self._background_methods else "inline",
                    "gateway_pid": os.getpid(),
                    "gateway_instance": self._trace_instance,
                    "reply_method": result.get('method') if isinstance(result, dict) else "",
                    "reply_result": self._reply_result(result),
                    "will_reply": bool(payload.get('need_reply') == 1 and result),
                },
            )
            logger.info(self._format_log_block("MQTT HANDLER COMPLETED", [
                ("method", method),
                ("tid", payload.get('tid')),
                ("source_topic", source_topic),
                ("handler_elapsed_ms", handler_elapsed_ms),
                ("result", self._reply_result(result)),
                ("reply", format_with_labels(result, max_length=3000)),
            ]))
            if is_downstream_command and isinstance(result, dict):
                self._remember_downstream_reply(source_topic, payload, result)
            elif is_downstream_command:
                self._release_downstream_request(source_topic, payload)
            if payload.get('need_reply') == 1 and result:
                self._publish_reply_for_downstream_topic(source_topic, result)
        except Exception as error:
            self._handle_handler_exception(payload, source_topic, started, error)

    def _handle_handler_exception(self, payload: dict, source_topic: str, started: float, error: Exception):
        method = payload.get("method", "")
        self._release_downstream_request(source_topic, payload)
        self._trace.record(
            "mqtt_handler_exception",
            direction="in",
            topic=source_topic,
            payload=payload,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=str(error),
        )
        logger.error(f"Handler error for {method}: {error}", exc_info=True)
        if payload.get('need_reply') == 1:
            error_reply = self._build_error_reply(
                payload,
                -1,
                f"Handler error for {method}: {error}",
                self.gateway_sn,
                context="handler_exception",
            )
            self._publish_reply_for_downstream_topic(source_topic, error_reply)
