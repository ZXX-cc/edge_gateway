"""
消息缓存管理模块
core/message_cache.py
"""
import time
from typing import Dict, Optional, List
from threading import Lock
from collections import defaultdict


class MAVLinkMessage:
    """MAVLink消息封装类"""

    def __init__(self, msg, timestamp: float, sysid: int, compid: int):
        self.msg = msg  # 原始消息对象
        self.timestamp = timestamp  # 接收时间戳
        self.sysid = sysid  # 系统ID
        self.compid = compid  # 组件ID
        self.msg_type = msg.get_type()  # 消息类型

    def __repr__(self):
        return (f"MAVLinkMessage(type={self.msg_type}, "
                f"sysid={self.sysid}, compid={self.compid}, "
                f"timestamp={self.timestamp:.2f})")


class MessageCache:
    """消息缓存管理器"""

    def __init__(self, max_history: int = 1):
        """
        初始化缓存

        Args:
            max_history: 每种消息类型保留的最大历史数量（默认1，即只保留最新）
        """
        self.max_history = max_history

        # 主缓存：{sysid: {msg_type: [messages]}}
        self._cache: Dict[int, Dict[str, List[MAVLinkMessage]]] = defaultdict(
            lambda: defaultdict(list)
        )

        # 统计信息
        self._stats: Dict[str, int] = defaultdict(int)

        self.lock = Lock()

    def add_message(self, message: MAVLinkMessage):
        """
        添加消息到缓存

        Args:
            message: MAVLink消息对象
        """
        with self.lock:
            sysid = message.sysid
            msg_type = message.msg_type

            # 获取该类型的消息列表
            msg_list = self._cache[sysid][msg_type]

            # 添加新消息
            msg_list.append(message)

            # 保持历史记录数量限制
            if len(msg_list) > self.max_history:
                msg_list.pop(0)  # 移除最旧的消息

            # 更新统计
            self._stats[msg_type] += 1

    def get_latest(self, msg_type: str, sysid: Optional[int] = None) -> Optional[MAVLinkMessage]:
        """
        获取最新的指定类型消息

        Args:
            msg_type: 消息类型
            sysid: 系统ID（如果为None，返回任意系统的消息）

        Returns:
            最新的消息对象或None
        """
        with self.lock:
            if sysid is not None:
                msg_list = self._cache.get(sysid, {}).get(msg_type, [])
                return msg_list[-1] if msg_list else None
            else:
                # 返回任意系统的该类型最新消息
                for sys_cache in self._cache.values():
                    msg_list = sys_cache.get(msg_type, [])
                    if msg_list:
                        return msg_list[-1]
                return None

    def get_history(self, msg_type: str, sysid: int, count: int = None) -> List[MAVLinkMessage]:
        """
        获取历史消息

        Args:
            msg_type: 消息类型
            sysid: 系统ID
            count: 获取的数量（None表示全部）

        Returns:
            消息列表（从旧到新）
        """
        with self.lock:
            msg_list = self._cache.get(sysid, {}).get(msg_type, [])
            if count is None:
                return msg_list.copy()
            else:
                return msg_list[-count:] if len(msg_list) >= count else msg_list.copy()

    def get_all_messages(self, sysid: int) -> Dict[str, MAVLinkMessage]:
        """
        获取指定系统的所有最新消息

        Args:
            sysid: 系统ID

        Returns:
            {msg_type: latest_message} 字典
        """
        with self.lock:
            result = {}
            sys_cache = self._cache.get(sysid, {})
            for msg_type, msg_list in sys_cache.items():
                if msg_list:
                    result[msg_type] = msg_list[-1]
            return result

    def get_all_systems(self) -> List[int]:
        """
        获取所有已缓存消息的系统ID列表

        Returns:
            系统ID列表
        """
        with self.lock:
            return list(self._cache.keys())

    def get_statistics(self) -> Dict[str, int]:
        """
        获取消息统计信息

        Returns:
            {msg_type: count} 字典
        """
        with self.lock:
            return self._stats.copy()

    def clear(self, sysid: Optional[int] = None):
        """
        清空缓存

        Args:
            sysid: 系统ID（如果为None，清空所有）
        """
        with self.lock:
            if sysid is not None:
                if sysid in self._cache:
                    del self._cache[sysid]
            else:
                self._cache.clear()
                self._stats.clear()
