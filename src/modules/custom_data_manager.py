"""
自定义数据传输模块

处理ESDK和PSDK自定义数据传输接口：
- custom_data_transmission_from_esdk: ESDK->云端
- custom_data_transmission_to_esdk: 云端->ESDK
- custom_data_transmission_from_psdk: PSDK->云端
- custom_data_transmission_to_psdk: 云端->PSDK
"""
from typing import Dict, Any, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.dji_error_codes import DOCK_COMMUNICATION_ERROR
from src.utils.message import MessageBuilder

logger = get_logger("custom_data")


class CustomDataManager:
    """自定义数据透传管理器"""

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()
        self._event_callback: Optional[Callable] = None

    def set_event_callback(self, callback: Callable):
        self._event_callback = callback

    @staticmethod
    def _adapter_unavailable_reply(msg: dict, reason: str) -> dict:
        return MessageBuilder.build_reply(
            msg,
            result=DOCK_COMMUNICATION_ERROR,
            data={"output": {"status": "failed"}},
            msg_str=reason,
        )

    def report_custom_data_from_esdk(self, value: str):
        """
        上报 ESDK 自定义数据 (custom_data_transmission_from_esdk)

        Args:
            value: 数据内容（长度需小于 256 字节）
        """
        if self._event_callback:
            event = MessageBuilder.build_event(
                'custom_data_transmission_from_esdk',
                {'value': value},
                self.gateway_sn
            )
            self._event_callback(event)
            logger.info(f"Custom data from ESDK reported: {len(value)} chars")

    def report_custom_data_from_psdk(self, value: str):
        """
        上报 PSDK 自定义数据 (custom_data_transmission_from_psdk)

        Args:
            value: 数据内容（长度需小于 256 字节）
        """
        if self._event_callback:
            event = MessageBuilder.build_event(
                'custom_data_transmission_from_psdk',
                {'value': value},
                self.gateway_sn
            )
            self._event_callback(event)
            logger.info(f"Custom data from PSDK reported: {len(value)} chars")

    def handle_custom_data_transmission_from_esdk(self, msg: dict) -> dict:
        """
        ESDK自定义数据透传上报 (custom_data_transmission_from_esdk)
        services 兼容模式
        """
        data = msg.get('data', {})
        value = data.get('value', data.get('data', ''))
        logger.info(f"Custom data from ESDK (services): {len(value)} chars")
        return self._adapter_unavailable_reply(
            msg,
            "custom_data_transmission_from_esdk requires a real ESDK data adapter",
        )

    def handle_custom_data_transmission_to_esdk(self, msg: dict) -> dict:
        """
        向ESDK发送自定义数据 (custom_data_transmission_to_esdk)

        Request: { value: text }
        Response: { result }
        """
        data = msg.get('data', {})
        value = data.get('value', data.get('data', ''))
        logger.info(f"Custom data to ESDK: {len(value)} chars")
        return self._adapter_unavailable_reply(
            msg,
            "custom_data_transmission_to_esdk requires a real ESDK data adapter",
        )

    def handle_custom_data_transmission_from_psdk(self, msg: dict) -> dict:
        """
        PSDK自定义数据透传上报 (custom_data_transmission_from_psdk)
        services 兼容模式
        """
        data = msg.get('data', {})
        value = data.get('value', data.get('data', ''))
        logger.info(f"Custom data from PSDK (services): {len(value)} chars")
        return self._adapter_unavailable_reply(
            msg,
            "custom_data_transmission_from_psdk requires a real PSDK data adapter",
        )

    def handle_custom_data_transmission_to_psdk(self, msg: dict) -> dict:
        """
        向PSDK发送自定义数据 (custom_data_transmission_to_psdk)

        Request: { value: text }
        Response: { result }
        """
        data = msg.get('data', {})
        value = data.get('value', data.get('data', ''))
        logger.info(f"Custom data to PSDK: {len(value)} chars")
        return self._adapter_unavailable_reply(
            msg,
            "custom_data_transmission_to_psdk requires a real PSDK data adapter",
        )
