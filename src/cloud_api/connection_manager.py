"""
DJI Cloud API 设备端连接管理器

处理设备上云完整生命周期：
1. 设备绑定/解绑
2. 设备上线/离线
3. 心跳保活
4. 连接状态监控
5. 重连策略

参考大疆 Cloud API 设备接入流程。
"""
import time
import threading
import uuid
from typing import Optional, Callable, Dict, Any
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder, TopicBuilder
from src.utils.request_trace import get_request_trace_recorder

logger = get_logger("cloud_conn")


class CloudConnectionManager:
    """
    云端连接管理器

    职责：
    1. 管理设备与云端的MQTT连接生命周期
    2. 自动上报设备在线状态
    3. 维护心跳机制
    4. 连接断开时自动重连并恢复状态
    """

    def __init__(self, mqtt_client):
        self.mqtt = mqtt_client
        self.gateway_sn = Config.get_gateway_sn()

        # 连接状态
        self._cloud_connected = False
        self._device_bound = False
        self._workspace_id = ""

        # 心跳
        self._heartbeat_interval = 10  # 秒
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_running = False

        # 重连
        self._reconnect_interval = 5
        self._max_reconnect_attempts = 10

        # 回调
        self._status_callback: Optional[Callable] = None

        # 请求-响应管理 (requests / requests_reply)
        self._pending_requests: Dict[str, dict] = {}
        self._request_lock = threading.Lock()
        self._request_cleaner: Optional[threading.Thread] = None
        self._request_cleaner_running = False
        self._trace = get_request_trace_recorder()

    def set_status_callback(self, callback: Callable):
        """设置连接状态变更回调"""
        self._status_callback = callback

    # ---- 请求-响应管理 (requests / requests_reply) ----

    def send_request(self, method: str, data: Optional[dict],
                     callback: Callable = None, timeout: int = 30) -> str:
        """
        发送请求到云端 (requests 通道)

        Args:
            method: API方法名
            data: 请求数据
            callback: 响应回调，签名为 (method: str, msg: dict) -> None
            timeout: 超时时间（秒）

        Returns:
            tid: 事务ID，用于匹配响应
        """
        tid = str(uuid.uuid4())
        msg = MessageBuilder.build(method, data, self.gateway_sn,
                                   need_reply=True, tid=tid)
        if data is None:
            msg['data'] = None

        with self._request_lock:
            self._pending_requests[tid] = {
                'callback': callback,
                'timestamp': time.time(),
                'timeout': timeout,
                'method': method
            }

        self.mqtt.publish_requests(msg)
        self._trace.record(
            "cloud_request_pending",
            direction="out",
            topic=TopicBuilder.requests(self.gateway_sn),
            method=method,
            tid=tid,
            bid=msg.get('bid', ''),
            gateway=self.gateway_sn,
            need_reply=1,
            payload=msg,
            extra={"timeout_s": timeout},
        )
        logger.info(f"Request sent: method={method}, tid={tid}")
        return tid

    def handle_requests_reply(self, method: str, msg: dict):
        """
        处理 requests_reply 响应

        Args:
            method: 响应的方法名
            msg: 响应消息体
        """
        tid = msg.get('tid', '')
        with self._request_lock:
            pending = self._pending_requests.pop(tid, None)

        if pending:
            elapsed = time.time() - pending['timestamp']
            result = msg.get('data', {}).get('result', -1)
            self._trace.record(
                "cloud_request_matched_reply",
                direction="in",
                topic=TopicBuilder.requests_reply(self.gateway_sn),
                method=method,
                tid=tid,
                bid=msg.get('bid', ''),
                gateway=self.gateway_sn,
                payload=msg,
                result=result,
                elapsed_ms=int(elapsed * 1000),
                extra={"expected_method": pending.get('method')},
            )
            logger.info(f"Request reply: method={method}, tid={tid}, "
                       f"result={result}, elapsed={elapsed:.2f}s")
            if pending['callback']:
                try:
                    pending['callback'](method, msg)
                except Exception as e:
                    logger.error(f"Request callback error: {e}")
        else:
            # 可能是 unsolicited reply 或超时后已清理
            result = msg.get('data', {}).get('result', -1)
            self._trace.record(
                "cloud_request_unexpected_reply",
                direction="in",
                topic=TopicBuilder.requests_reply(self.gateway_sn),
                method=method,
                tid=tid,
                bid=msg.get('bid', ''),
                gateway=self.gateway_sn,
                payload=msg,
                result=result,
                error="unexpected_or_late_requests_reply",
            )
            logger.warning(f"Unexpected/late requests_reply: method={method}, "
                          f"tid={tid}, result={result}")

    def _start_request_cleaner(self):
        """启动请求清理线程，定期移除超时的 pending request"""
        if self._request_cleaner_running:
            return
        self._request_cleaner_running = True
        self._request_cleaner = threading.Thread(
            target=self._request_cleaner_loop,
            daemon=True,
            name="RequestCleaner"
        )
        self._request_cleaner.start()
        logger.info("Request cleaner started")

    def _stop_request_cleaner(self):
        """停止请求清理线程"""
        self._request_cleaner_running = False
        if self._request_cleaner:
            self._request_cleaner.join(timeout=2.0)

    def _request_cleaner_loop(self):
        """清理超时的 pending requests"""
        while self._request_cleaner_running:
            try:
                now = time.time()
                expired = []
                with self._request_lock:
                    for tid, info in list(self._pending_requests.items()):
                        if now - info['timestamp'] > info['timeout']:
                            expired.append(tid)
                    for tid in expired:
                        info = self._pending_requests.pop(tid)
                        self._trace.record(
                            "cloud_request_timeout",
                            direction="internal",
                            topic=TopicBuilder.requests(self.gateway_sn),
                            method=info.get('method', ''),
                            tid=tid,
                            gateway=self.gateway_sn,
                            elapsed_ms=int((now - info.get('timestamp', now)) * 1000),
                            error="request timeout",
                            extra={"timeout_s": info.get('timeout')},
                        )
                if expired:
                    logger.warning(f"Expired requests cleaned: {len(expired)}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Request cleaner error: {e}")
                time.sleep(1)

    # ---- 设备绑定流程 (组织管理 Organization) ----

    def request_airport_bind_status(self, device_sns: list,
                                    callback: Callable = None) -> str:
        """
        主动查询设备绑定状态 (airport_bind_status)
        Topic: requests / requests_reply
        """
        data = {'devices': [{'sn': sn} for sn in device_sns]}
        return self.send_request('airport_bind_status', data, callback)

    def request_airport_organization_get(self, binding_code: str,
                                         org_id: str,
                                         callback: Callable = None) -> str:
        """
        主动查询组织信息 (airport_organization_get)
        Topic: requests / requests_reply
        """
        data = {'device_binding_code': binding_code, 'organization_id': org_id}
        return self.send_request('airport_organization_get', data, callback)

    def request_airport_organization_bind(self, bind_devices: list,
                                          callback: Callable = None) -> str:
        """
        主动发起设备绑定到组织 (airport_organization_bind)
        Topic: requests / requests_reply

        Args:
            bind_devices: 绑定参数列表
                [{sn, device_binding_code, organization_id,
                  device_callsign, device_model_key}, ...]
        """
        data = {'bind_devices': bind_devices}
        return self.send_request('airport_organization_bind', data, callback)

    def request_config(self, config_type: str = 'json',
                       config_scope: str = 'product',
                       callback: Callable = None) -> str:
        """
        主动获取云端配置 (config)
        Topic: requests / requests_reply

        Args:
            config_type: 配置类型，默认 'json'
            config_scope: 配置范围，默认 'product'
            callback: 响应回调
        """
        data = {'config_type': config_type, 'config_scope': config_scope}
        return self.send_request('config', data, callback)

    def report_device_binding(self, workspace_id: str, bind_code: str = "") -> bool:
        """
        上报设备绑定状态

        设备完成绑定后，通过events上报绑定结果。
        """
        self._device_bound = True
        self._workspace_id = workspace_id

        event = MessageBuilder.build_event(
            'device_binding',
            {
                'device_sn': self.gateway_sn,
                'workspace_id': workspace_id,
                'bind_code': bind_code,
                'bind_status': 'success',
                'timestamp': int(time.time() * 1000)
            },
            self.gateway_sn
        )

        topic = TopicBuilder.events(self.gateway_sn)
        result = self.mqtt.publish(topic, event)
        logger.info(f"Device binding reported: workspace={workspace_id}")
        return result

    def report_device_unbinding(self) -> bool:
        """上报设备解绑"""
        self._device_bound = False
        self._workspace_id = ""

        event = MessageBuilder.build_event(
            'device_unbinding',
            {
                'device_sn': self.gateway_sn,
                'timestamp': int(time.time() * 1000)
            },
            self.gateway_sn
        )

        topic = TopicBuilder.events(self.gateway_sn)
        result = self.mqtt.publish(topic, event)
        logger.info("Device unbinding reported")
        return result

    # ---- 设备上下线 ----

    def report_device_online(self) -> bool:
        """
        上报设备上线

        设备连接到云端后，立即上报在线状态。
        """
        self._cloud_connected = True

        event = MessageBuilder.build_event(
            'device_online',
            {
                'device_sn': self.gateway_sn,
                'online_status': 1,
                'timestamp': int(time.time() * 1000)
            },
            self.gateway_sn
        )

        topic = TopicBuilder.events(self.gateway_sn)
        result = self.mqtt.publish(topic, event)
        logger.info("Device online reported")

        # 上线后自动上报能力集
        self._report_device_info()

        return result

    def report_device_offline(self) -> bool:
        """上报设备离线（ wills 或主动断开前）"""
        self._cloud_connected = False

        event = MessageBuilder.build_event(
            'device_offline',
            {
                'device_sn': self.gateway_sn,
                'online_status': 0,
                'timestamp': int(time.time() * 1000)
            },
            self.gateway_sn
        )

        topic = TopicBuilder.events(self.gateway_sn)
        result = self.mqtt.publish(topic, event)
        logger.info("Device offline reported")
        return result

    # ---- 设备信息上报 ----

    def _report_device_info(self):
        """上报设备基本信息"""
        event = MessageBuilder.build_event(
            'device_info_put',
            {
                'device_sn': self.gateway_sn,
                'product_type': Config.get('gateway.product_type', 'edge-gateway'),
                'product_name': Config.get('gateway.name', 'EdgeGateway'),
                'firmware_version': '1.0.0',
                'hardware_version': '1.0.0',
                'timestamp': int(time.time() * 1000)
            },
            self.gateway_sn
        )
        self.mqtt.publish(TopicBuilder.events(self.gateway_sn), event)
        logger.info("Device info reported")

    def report_device_topology(self, sub_devices: list):
        """
        上报设备拓扑

        Args:
            sub_devices: 子设备列表 [{sn, type, online}, ...]
        """
        event = MessageBuilder.build_event(
            'device_topology',
            {
                'gateway_sn': self.gateway_sn,
                'sub_devices': sub_devices,
                'timestamp': int(time.time() * 1000)
            },
            self.gateway_sn
        )
        self.mqtt.publish(TopicBuilder.events(self.gateway_sn), event)
        logger.info(f"Device topology reported: {len(sub_devices)} sub-devices")

    # ---- 心跳保活 ----

    def start_heartbeat(self):
        """启动心跳线程"""
        if self._heartbeat_running:
            return

        self._heartbeat_running = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="CloudHeartbeat"
        )
        self._heartbeat_thread.start()
        logger.info("Cloud heartbeat started")

    def stop_heartbeat(self):
        """停止心跳"""
        self._heartbeat_running = False
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2.0)
        logger.info("Cloud heartbeat stopped")

    def _heartbeat_loop(self):
        """心跳循环"""
        while self._heartbeat_running:
            try:
                if self.mqtt.connected:
                    self._send_heartbeat()
                time.sleep(self._heartbeat_interval)
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                time.sleep(1)

    def _send_heartbeat(self):
        """发送心跳"""
        event = MessageBuilder.build_event(
            'heartbeat',
            {
                'device_sn': self.gateway_sn,
                'timestamp': int(time.time() * 1000)
            },
            self.gateway_sn
        )
        self.mqtt.publish(TopicBuilder.events(self.gateway_sn), event)

    # ---- 连接状态 ----

    @property
    def is_cloud_connected(self) -> bool:
        return self._cloud_connected and self.mqtt.connected

    @property
    def is_device_bound(self) -> bool:
        return self._device_bound
