"""
Topic路由模块 - API方法路由分发
将MQTT消息按method路由到对应业务模块
"""
from typing import Dict, Callable
from src.utils.logger import get_logger
from src.utils.dji_error_codes import normalize_error_code

logger = get_logger("router")


class TopicRouter:
    """
    Topic路由器

    职责:
    1. 注册各业务模块的API处理器
    2. 将MQTT消息按method路由到对应模块
    3. 管理处理器生命周期

    API分类:
    - 设备管理: ota_*, config_*
    - 飞行任务: load_wayline, execute_task, flyto, take_off, ...
    - 负载控制: camera_*, gimbal_*, drc_light_*, drc_speaker_*, ...
    - 媒体管理: adjust_upload_priority, live_*, ...
    - DRC实时: drc_down通道的速度/航向/高度指令
    """

    def __init__(self):
        # 按method注册的路由表
        self._routes: Dict[str, Callable] = {}
        # 按分类组织的路由(便于管理)
        self._categories: Dict[str, Dict[str, Callable]] = {
            'device': {},      # 设备管理
            'mission': {},     # 飞行任务
            'payload': {},     # 负载控制
            'media': {},       # 媒体管理
            'drc': {},         # DRC实时控制
            'dock': {},        # 机场管理
            'log': {},         # 日志管理
            'flysafe': {},     # FlySafe
            'flight_area': {}, # 飞行区域
            'custom_data': {}, # 自定义数据透传
        }

    def register(self, method: str, handler: Callable, category: str = None):
        """
        注册API处理器

        Args:
            method: API方法名
            handler: 处理函数 (msg: dict) -> Optional[dict]
            category: 分类(device/mission/payload/media/drc)
        """
        self._routes[method] = handler
        if category and category in self._categories:
            self._categories[category][method] = handler
        logger.debug(f"Route registered: {method} -> {category or 'default'}")

    def register_batch(self, handlers: Dict[str, Callable], category: str = None):
        """批量注册处理器"""
        for method, handler in handlers.items():
            self.register(method, handler, category)

    def unregister(self, method: str):
        """注销处理器"""
        self._routes.pop(method, None)
        for cat in self._categories.values():
            cat.pop(method, None)

    def get_handler(self, method: str) -> Callable:
        """获取处理器"""
        return self._routes.get(method)

    def route(self, method: str, msg: dict) -> dict:
        """
        路由消息到处理器

        Args:
            method: API方法名
            msg: 消息体

        Returns:
            响应消息(如需)
        """
        handler = self._routes.get(method)
        if handler:
            try:
                return handler(msg)
            except Exception as e:
                logger.error(f"Handler error for {method}: {e}")
                return self._build_error_response(msg, -1, f"Handler error: {e}", context="handler_exception")
        else:
            logger.warning(f"No route for method: {method}")
            return self._build_error_response(msg, -2, f"Unknown method: {method}", context="unknown_method")

    def list_routes(self) -> Dict[str, str]:
        """列出所有注册的路由"""
        result = {}
        for cat, methods in self._categories.items():
            for method in methods:
                result[method] = cat
        # 未分类的
        for method in self._routes:
            if method not in result:
                result[method] = 'uncategorized'
        return result

    @staticmethod
    def _build_error_response(msg: dict, code: int, error_msg: str, context: str = "generic") -> dict:
        """构造错误响应"""
        return {
            "tid": msg.get("tid", ""),
            "bid": msg.get("bid", ""),
            "timestamp": msg.get("timestamp", 0),
            "method": msg.get("method", ""),
            "data": {"result": normalize_error_code(code, context=context)},
            "msg": error_msg
        }
