"""
功能模块基类
"""
from abc import ABC
from src.core.observer import MessageObserver


class BaseModule(MessageObserver):
    """
    功能模块基类

    提供通用的模块功能：
    - 启用/禁用
    - 配置管理
    - 日志记录
    """

    def __init__(self, name: str, config: dict = None):
        """
        初始化模块

        Args:
            name: 模块名称
            config: 配置字典
        """
        super().__init__(name)
        self.config = config or {}
        self._initialized = False

    def on_message(self, message):
        """
        消息回调方法 - 提供默认空实现

        子类如果需要推送机制，可以重写此方法
        """
        pass  # 默认不处理，子类可选择重写

    def idle_task(self, dispatcher):
        """
        空闲任务方法 - 提供默认空实现

        子类如果需要拉取机制，可以重写此方法
        """
        pass  # 默认不处理，子类可选择重写

    def on_start(self):
        """模块启动时调用"""
        if not self._initialized:
            self.initialize()
            self._initialized = True
        print(f"[{self.name}] 模块已启动")

    def on_stop(self):
        """模块停止时调用"""
        self.cleanup()
        print(f"[{self.name}] 模块已停止")

    def initialize(self):
        """初始化方法 - 子类可重写"""
        pass

    def cleanup(self):
        """清理方法 - 子类可重写"""
        pass

    def log(self, message: str, level: str = "INFO"):
        """
        记录日志

        Args:
            message: 日志消息
            level: 日志级别
        """
        print(f"[{self.name}][{level}] {message}")
