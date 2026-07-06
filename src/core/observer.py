"""
观察者模式基类定义
core/observer.py
"""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .message_dispatcher import MAVLinkMessage, MessageDispatcher


class MessageObserver(ABC):
    """消息观察者抽象基类"""

    def __init__(self, name: str):
        self.name = name
        self.enabled = True

    @abstractmethod
    def on_message(self, message: 'MAVLinkMessage'):
        """
        消息回调方法（推送机制）

        Args:
            message: MAVLink消息封装对象
        """
        pass

    def idle_task(self, dispatcher: 'MessageDispatcher'):
        """
        空闲任务方法（拉取机制）

        Args:
            dispatcher: 消息分发器实例，可用于查询缓存
        """
        pass

    def enable(self):
        """启用观察者"""
        self.enabled = True

    def disable(self):
        """禁用观察者"""
        self.enabled = False

    def on_start(self):
        """模块启动时调用"""
        pass

    def on_stop(self):
        """模块停止时调用"""
        pass
