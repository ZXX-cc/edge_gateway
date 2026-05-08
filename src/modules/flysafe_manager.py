"""
FlySafe 管理模块

处理飞行安全相关接口：
- unlock_license_switch: 解禁证书开关
- unlock_license_update: 解禁证书更新
- unlock_license_list: 获取解禁证书列表
"""
from typing import Dict, Any, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder

logger = get_logger("flysafe")


class FlySafeManager:
    """FlySafe管理器 - 解禁证书管理"""

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()
        self._event_callback: Optional[Callable] = None

        # 本地解禁证书缓存
        self._licenses: list = []
        self._consistence = False

    def set_event_callback(self, callback: Callable):
        self._event_callback = callback

    def handle_unlock_license_switch(self, msg: dict) -> dict:
        """
        解禁证书开关 (unlock_license_switch)

        Request: { license_id: int, enabled: bool }
        Response: { result, license_id }
        """
        data = msg.get('data', {})
        license_id = data.get('license_id', 0)
        enabled = data.get('enabled', False)
        logger.info(f"Unlock license switch: id={license_id}, enabled={enabled}")

        # 更新本地缓存
        for lic in self._licenses:
            if lic.get('common_fields', {}).get('license_id') == license_id:
                lic['common_fields']['enabled'] = enabled
                break

        # TODO: 将开关状态同步到飞控/FlySafe 模块

        return MessageBuilder.build_reply(msg, result=0, data={
            'license_id': license_id
        })

    def handle_unlock_license_update(self, msg: dict) -> dict:
        """
        解禁证书更新 (unlock_license_update)

        Request: { file: { url, fingerprint } }
        Response: { result }
        """
        data = msg.get('data', {})
        file_info = data.get('file', {})
        url = file_info.get('url', '')
        fingerprint = file_info.get('fingerprint', '')

        logger.info(f"Unlock license update: url={url}, fingerprint={fingerprint}")

        # TODO: 下载证书文件、校验 MD5、导入 FlySafe 模块

        return MessageBuilder.build_reply(msg, result=0)

    def handle_unlock_license_list(self, msg: dict) -> dict:
        """
        获取解禁证书列表 (unlock_license_list)

        Request: { device_model_domain: 0=飞行器, 3=机场 }
        Response: { result, device_model_domain, consistence, licenses }
        """
        data = msg.get('data', {})
        device_model_domain = data.get('device_model_domain', 0)
        logger.info(f"Unlock license list: domain={device_model_domain}")

        # TODO: 从飞控或本地缓存查询真实证书列表

        return MessageBuilder.build_reply(msg, result=0, data={
            'device_model_domain': device_model_domain,
            'consistence': self._consistence,
            'licenses': self._licenses
        })

    def update_licenses(self, licenses: list, consistence: bool = False):
        """
        更新本地解禁证书缓存

        Args:
            licenses: 证书列表，格式参考 flysafe.md
            consistence: 证书一致性状态
        """
        self._licenses = licenses
        self._consistence = consistence
        logger.info(f"Licenses updated: {len(licenses)} licenses, consistence={consistence}")
