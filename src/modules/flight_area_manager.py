"""
禁飞区管理模块

处理自定义飞行区域相关接口：
- flight_areas_update: 更新飞行区域
- flight_areas_get: 获取飞行区域
"""
from typing import Dict, Any, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder

logger = get_logger("flight_area")


class FlightAreaManager:
    """禁飞区/飞行区域管理器"""

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()
        self._event_callback: Optional[Callable] = None
        self._request_sender: Optional[Callable] = None

    def set_event_callback(self, callback: Callable):
        self._event_callback = callback

    def set_request_sender(self, sender: Callable):
        """设置请求发送器（用于 requests 通道）"""
        self._request_sender = sender

    def request_flight_areas_get(self, callback: Callable = None) -> str:
        """
        主动获取自定义飞行区文件列表 (flight_areas_get)
        Topic: requests / requests_reply
        """
        if not self._request_sender:
            logger.warning("Request sender not set")
            return ""
        return self._request_sender('flight_areas_get', {}, callback)

    def report_flight_areas_sync_progress(self, status: str, reason: int = 0,
                                          file_info: dict = None):
        """
        上报自定义飞行区文件同步状态 (flight_areas_sync_progress)

        Args:
            status: wait_sync/synchronizing/synchronized/fail/switch_fail
            reason: 错误码 (0=成功)
            file_info: {name, checksum}
        """
        if self._event_callback:
            data = {'status': status, 'reason': reason}
            if file_info:
                data['file'] = file_info
            event = MessageBuilder.build_event(
                'flight_areas_sync_progress', data, self.gateway_sn
            )
            self._event_callback(event)
            logger.info(f"Flight areas sync progress: status={status}, reason={reason}")

    def report_flight_areas_drone_location(self, locations: list):
        """
        上报飞行器自定义飞行区距离信息 (flight_areas_drone_location)

        Args:
            locations: [{area_id, area_distance, is_in_area}, ...]
        """
        if self._event_callback:
            event = MessageBuilder.build_event(
                'flight_areas_drone_location',
                {'drone_locations': locations},
                self.gateway_sn
            )
            self._event_callback(event)
            logger.info(f"Flight areas drone location: {len(locations)} areas")

    def handle_flight_areas_update(self, msg: dict) -> dict:
        """
        更新飞行区域 (flight_areas_update)

        文档格式：云端下发空指令，设备收到后主动请求文件列表并同步。
        services 兼容模式：也支持直接传入 areas 数组。
        """
        data = msg.get('data', {})
        if data:
            areas = data.get('areas', [])
            logger.info(f"Flight areas update (direct): {len(areas)} areas")
            # TODO: 直接更新飞行区域数据
        else:
            logger.info("Flight areas update: triggering sync from cloud")
            # TODO: 触发从云端获取并同步飞行区文件
        return MessageBuilder.build_reply(msg, result=0)

    def handle_flight_areas_get(self, msg: dict) -> dict:
        """
        获取飞行区域 (flight_areas_get) - services 兼容模式

        文档标准格式为 requests / requests_reply，但保留 services 兼容。
        """
        logger.info("Flight areas get (services mode)")
        return MessageBuilder.build_reply(msg, result=0, data={
            'areas': []
        })
