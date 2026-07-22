"""
消息分发器 - 框架核心
core/message_dispatcher.py
"""
import time
from threading import Thread, Lock
from typing import Dict, List, Optional, Set
from collections import defaultdict

from .connection import MAVLinkConnection
from .message_cache import MessageCache, MAVLinkMessage
from .observer import MessageObserver
from src.utils.logger import get_logger


logger = get_logger("mavlink.dispatcher")


class MessageDispatcher:
    """消息分发器 - 负责消息接收、缓存和分发"""

    def __init__(self, connection: MAVLinkConnection, cache_history: int = 1):
        """
        初始化分发器

        Args:
            connection: MAVLink连接对象
            cache_history: 缓存历史消息数量
        """
        self.connection = connection
        self.cache = MessageCache(max_history=cache_history)

        # 消息钩子：{msg_type: {observers}}
        self._hooks: Dict[str, Set[MessageObserver]] = defaultdict(set)

        # 通配符钩子：接收所有消息
        self._wildcard_hooks: Set[MessageObserver] = set()

        # 注册的模块列表（用于idle_task）
        self._modules: List[MessageObserver] = []

        # 线程控制
        self._running = False
        self._lock = Lock()

        self._receive_thread: Optional[Thread] = None
        self._idle_thread: Optional[Thread] = None

        # 性能统计
        self._message_count = 0
        self._start_time = 0
        self._heartbeat_timeout_count = 0
        self._heartbeat_timeout_log_interval = 10
        self._reconnect_enabled = True
        self._reconnect_interval = 2.0
        self._last_reconnect_attempt = 0.0

        # ⭐⭐⭐ 新增：根据连接类型设置心跳检查策略 ⭐⭐⭐
        self._check_heartbeat = not connection.use_custom_dialect
        if not self._check_heartbeat:
            logger.info(
                "Dispatcher heartbeat timeout check disabled for custom dialect: connection=%s",
                connection.connection_string,
            )

    def register_hook(self, msg_type: str, observer):
        """
        注册消息钩子（推送机制）

        Args:
            msg_type: 消息类型，支持通配符"*"
            observer: 观察者对象
        """
        with self._lock:
            if msg_type == "*":
                self._wildcard_hooks.add(observer)
                name = getattr(observer, 'name', repr(observer))
                logger.debug("Observer subscribed: observer=%s msg_type=*", name)
            else:
                self._hooks[msg_type].add(observer)
                name = getattr(observer, 'name', repr(observer))
                logger.debug("Observer subscribed: observer=%s msg_type=%s", name, msg_type)

    def unregister_hook(self, msg_type: str, observer):
        """
        取消注册消息钩子

        Args:
            msg_type: 消息类型
            observer: 观察者对象
        """
        with self._lock:
            if msg_type == "*":
                self._wildcard_hooks.discard(observer)
            elif msg_type in self._hooks:
                self._hooks[msg_type].discard(observer)

    def add_module(self, module: MessageObserver):
        """
        添加模块（用于idle_task拉取机制）

        Args:
            module: 模块对象
        """
        with self._lock:
            if module not in self._modules:
                self._modules.append(module)
                logger.info("Dispatcher module added: module=%s", module.name)

    def remove_module(self, module: MessageObserver):
        """
        移除模块

        Args:
            module: 模块对象
        """
        with self._lock:
            if module in self._modules:
                self._modules.remove(module)
                logger.info("Dispatcher module removed: module=%s", module.name)

    def start(self):
        """启动消息分发器"""
        if self._running:
            logger.info("Dispatcher already running: connection=%s", self.connection.connection_string)
            return

        if not self.connection.is_connected():
            logger.warning("Dispatcher start skipped: connection not established: %s", self.connection.connection_string)
            return

        self._running = True
        self._start_time = time.time()

        # 启动接收线程
        self._receive_thread = Thread(target=self._receive_loop, daemon=True)
        self._receive_thread.start()

        # 启动空闲任务线程
        self._idle_thread = Thread(target=self._idle_loop, daemon=True)
        self._idle_thread.start()

        # 调用所有模块的on_start
        for module in self._modules:
            try:
                module.on_start()
            except Exception as e:
                logger.error("Dispatcher module on_start failed: module=%s error=%s", module.name, e, exc_info=True)

        logger.info(
            "Dispatcher started: connection=%s modules=%s heartbeat_check=%s",
            self.connection.connection_string,
            [getattr(module, "name", repr(module)) for module in self._modules],
            self._check_heartbeat,
        )

    def stop(self):
        """停止消息分发器"""
        logger.info("Dispatcher stopping: connection=%s", self.connection.connection_string)
        self._running = False

        # 等待线程结束
        if self._receive_thread:
            self._receive_thread.join(timeout=2)
        if self._idle_thread:
            self._idle_thread.join(timeout=2)

        # 调用所有模块的on_stop
        for module in self._modules:
            try:
                module.on_stop()
            except Exception as e:
                logger.error("Dispatcher module on_stop failed: module=%s error=%s", module.name, e, exc_info=True)

        logger.info("Dispatcher stopped: connection=%s", self.connection.connection_string)

    def configure_reconnect(self, enabled: bool = True, interval: float = 2.0):
        """配置后台重连；机巢 TCP 被对端断开后由 idle loop 拉起恢复。"""
        self._reconnect_enabled = bool(enabled)
        self._reconnect_interval = max(float(interval or 0), 0.1)

    def _receive_loop(self):
        """消息接收循环（推送机制实现）"""
        logger.info("Dispatcher receive loop started: connection=%s", self.connection.connection_string)

        while self._running:
            try:
                # 接收消息
                raw_msg = self.connection.recv_message(timeout=0.1)

                if raw_msg is None:
                    continue
                # ⭐⭐⭐ 核心修复：统一消息类型获取逻辑 ⭐⭐⭐
                msg_type = self._extract_message_type(raw_msg)
                msg_id = self._extract_message_id(raw_msg)
                # ❌ 当前代码（会崩溃）
                # f"[RAW] ID={msg_id} len={len(raw_msg.get_payload()) if hasattr(raw_msg, 'get_payload') else 'N/A'}"
                # 封装消息
                message = MAVLinkMessage(
                    msg=raw_msg,
                    timestamp=time.time(),
                    sysid=raw_msg.get_srcSystem(),
                    compid=raw_msg.get_srcComponent()
                )

                # ⭐ 同时保存消息类型名和ID，方便模块使用
                message.msg_type = msg_type
                message.msg_id = msg_id

                # 调试日志
                # if msg_id in (12000, 12002):
                    # print(f"[Dispatcher] 收到自定义消息: ID={msg_id}, Type={msg_type}")

                # 更新心跳时间
                if msg_type == 'HEARTBEAT':
                    # print(f"🔍 DEBUG: 收到心跳 - {message}")
                    self.connection.update_heartbeat()

                # 添加到缓存
                self.cache.add_message(message)
                # 在 self.cache.add_message(message) 后面添加
                # print(
                #     f"[DEBUG] 收到机巢消息 → ID={msg_id:5d}  type={msg_type:30s}  src={message.sysid}/{message.compid}")
                # # ⭐ 双重分发：同时按类型名和ID分发
                self._notify_observers(message)
                if msg_id and msg_type != str(msg_id):
                    # 为ID订阅者额外分发一次
                    self._notify_observers_by_id(message, msg_id)

                # 统计
                self._message_count += 1

            except TypeError as e:
                logger.error("Dispatcher receive type error: %s", e, exc_info=True)
                if 'raw_msg' in locals():
                    logger.error("Dispatcher problematic message: %s", raw_msg)
                time.sleep(0.1)

            except Exception as e:
                if self._running:
                    logger.error("Dispatcher receive loop error: %s", e, exc_info=True)
                time.sleep(0.1)

    def _extract_message_type(self, raw_msg) -> str:
        """
        提取消息类型名（优先级：名称 > ID）

        Returns:
            消息类型字符串，如 "HEARTBEAT" 或 "DRONE_NEST_ENVIRONMENT"
        """
        try:
            # 尝试1：获取消息名称
            if hasattr(raw_msg, 'get_type'):
                msg_type_raw = raw_msg.get_type()
                if isinstance(msg_type_raw, str) and msg_type_raw.strip():
                    return msg_type_raw.upper()

            # 尝试2：从类名提取
            if hasattr(raw_msg, '__class__'):
                class_name = raw_msg.__class__.__name__
                if class_name.startswith('MAVLink_'):
                    # MAVLink_drone_nest_environment_message -> DRONE_NEST_ENVIRONMENT
                    name = class_name.replace('MAVLink_', '').replace('_message', '')
                    return name.upper()

            # 尝试3：查找dialect映射
            msg_id = self._extract_message_id(raw_msg)
            if msg_id:
                # 尝试从dialect获取名称
                mav = getattr(self.connection, 'mav', None)
                if mav and hasattr(mav, 'mavlink_map'):
                    if msg_id in mav.mavlink_map:
                        msg_class = mav.mavlink_map[msg_id]
                        if hasattr(msg_class, 'msgname'):
                            return msg_class.msgname.upper()

                # 兜底：使用ID作为类型名
                return f"MSG_ID_{msg_id}"

            # 完全失败
            return "UNKNOWN"

        except Exception as e:
            logger.warning("Dispatcher extract message type failed: %s", e)
            return "ERROR"

    def _extract_message_id(self, raw_msg) -> Optional[int]:
        """
        提取消息ID

        Returns:
            消息ID整数，失败返回None
        """
        try:
            if hasattr(raw_msg, 'get_msgId'):
                return raw_msg.get_msgId()
            elif hasattr(raw_msg, 'msgid'):
                return raw_msg.msgid
            elif hasattr(raw_msg, 'id'):
                return raw_msg.id
            return None
        except:
            return None

    def _notify_observers_by_id(self, message: MAVLinkMessage, msg_id: int):
        """
        按消息ID通知观察者（支持ID订阅）

        Args:
            message: MAVLink消息对象
            msg_id: 消息ID
        """
        id_key = f"MSG_ID_{msg_id}"
        observers = self._hooks.get(id_key, set()).copy()

        for observer in observers:
            if observer.enabled:
                try:
                    observer.on_message(message)
                except Exception as e:
                    logger.error(
                        "Observer on_message failed: observer=%s msg_id=%s error=%s",
                        getattr(observer, "name", repr(observer)),
                        msg_id,
                        e,
                        exc_info=True,
                    )

    def _notify_observers(self, message: MAVLinkMessage):
        """
        通知观察者（推送机制核心）

        Args:
            message: MAVLink消息对象
        """
        msg_type = message.msg_type

        # 通知特定消息类型的观察者
        observers = self._hooks.get(msg_type, set()).copy()
        for observer in observers:
            try:
                if hasattr(observer, 'enabled') and not observer.enabled:
                    continue
                if hasattr(observer, 'on_message'):
                    observer.on_message(message)
                else:
                    observer(message)
            except Exception as e:
                name = getattr(observer, 'name', repr(observer))
                logger.error("Observer on_message failed: observer=%s msg_type=%s error=%s", name, msg_type, e, exc_info=True)

        # 通知通配符观察者
        for observer in self._wildcard_hooks.copy():
            try:
                if hasattr(observer, 'enabled') and not observer.enabled:
                    continue
                if hasattr(observer, 'on_message'):
                    observer.on_message(message)
                else:
                    observer(message)
            except Exception as e:
                name = getattr(observer, 'name', repr(observer))
                logger.error("Observer on_message failed: observer=%s msg_type=* error=%s", name, e, exc_info=True)

    def _idle_loop(self):
        """空闲任务循环（拉取机制实现）"""
        logger.info("Dispatcher idle loop started: connection=%s", self.connection.connection_string)

        while self._running:
            try:
                self._maybe_reconnect()

                # ⭐⭐⭐ 修改：只对需要心跳检查的连接进行检查 ⭐⭐⭐
                if self._check_heartbeat:
                    if self._should_log_heartbeat_timeout(self.connection.check_heartbeat_timeout()):
                        logger.warning(
                            "Heartbeat timeout: connection=%s consecutive=%s",
                            self.connection.connection_string,
                            self._heartbeat_timeout_count,
                        )

                # 调用所有模块的idle_task
                modules_copy = self._modules.copy()
                for module in modules_copy:
                    if module.enabled:
                        try:
                            module.idle_task(self)
                        except Exception as e:
                            logger.error("Dispatcher module idle_task failed: module=%s error=%s", module.name, e, exc_info=True)

                # 控制循环频率（10Hz）
                time.sleep(0.1)

            except Exception as e:
                if self._running:
                    logger.error("Dispatcher idle loop error: %s", e, exc_info=True)

    def _maybe_reconnect(self) -> bool:
        """连接断开时按固定间隔重连，避免业务线程一直向已关闭 socket 写入。"""
        if not self._reconnect_enabled or self.connection.is_connected():
            return False

        current_time = time.time()
        if current_time - self._last_reconnect_attempt < self._reconnect_interval:
            return False
        self._last_reconnect_attempt = current_time

        info = (
            self.connection.get_connection_info()
            if hasattr(self.connection, "get_connection_info")
            else {}
        )
        logger.warning(
            "Dispatcher reconnecting: connection=%s reason=%s",
            self.connection.connection_string,
            info.get("disconnect_reason", ""),
        )

        try:
            self.connection.disconnect()
        except Exception as e:
            logger.warning("Dispatcher cleanup before reconnect failed: %s", e, exc_info=True)

        try:
            if self.connection.connect():
                self._heartbeat_timeout_count = 0
                logger.info("Dispatcher reconnected: connection=%s", self.connection.connection_string)
                return True
        except Exception as e:
            logger.warning("Dispatcher reconnect error: %s", e, exc_info=True)
            return False

        logger.warning("Dispatcher reconnect failed: connection=%s", self.connection.connection_string)
        return False

    def _should_log_heartbeat_timeout(self, timed_out: bool) -> bool:
        """连续心跳超时时按固定间隔降频打印，恢复后重置计数。"""
        if not timed_out:
            self._heartbeat_timeout_count = 0
            return False

        self._heartbeat_timeout_count += 1
        return self._heartbeat_timeout_count % self._heartbeat_timeout_log_interval == 0

    def get_message(self, msg_type: str, sysid: Optional[int] = None) -> Optional[MAVLinkMessage]:
        """
        获取缓存的消息（拉取机制接口）

        Args:
            msg_type: 消息类型
            sysid: 系统ID（可选）

        Returns:
            最新的消息对象或None
        """
        return self.cache.get_latest(msg_type, sysid)

    def get_statistics(self) -> Dict:
        """
        获取运行统计信息

        Returns:
            统计信息字典
        """
        runtime = time.time() - self._start_time if self._start_time > 0 else 0
        msg_rate = self._message_count / runtime if runtime > 0 else 0

        return {
            'runtime': runtime,
            'message_count': self._message_count,
            'message_rate': msg_rate,
            'connected_systems': self.cache.get_all_systems(),
            'message_stats': self.cache.get_statistics(),
            'heartbeat_check_enabled': self._check_heartbeat  # ⭐ 新增统计信息
        }
    def get_modules(self) -> List[MessageObserver]:
        """获取所有注册的模块"""
        with self._lock:
            return self._modules.copy()
