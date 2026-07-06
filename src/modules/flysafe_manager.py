"""
FlySafe 管理模块

处理飞行安全相关接口：
- unlock_license_switch: 解禁证书开关
- unlock_license_update: 解禁证书更新
- unlock_license_list: 获取解禁证书列表
- flysafe_geo_fence_update: 地理围栏更新
"""
import os
import time
import urllib.request
import hashlib
from typing import Dict, Any, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder

logger = get_logger("flysafe")

LICENSE_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'flysafe')


class FlySafeManager:
    """FlySafe管理器 - 解禁证书管理"""

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()
        self._event_callback: Optional[Callable] = None

        # 本地解禁证书缓存
        self._licenses: list = []
        self._consistence = False

        # 证书存储路径
        self._license_dir = LICENSE_CACHE_DIR
        os.makedirs(self._license_dir, exist_ok=True)

        # 地理围栏数据
        self._geo_fences = []

        # 加载缓存的证书
        self._load_cached_licenses()

    def set_event_callback(self, callback: Callable):
        self._event_callback = callback

    def _load_cached_licenses(self):
        """从本地缓存加载证书列表"""
        cache_file = os.path.join(self._license_dir, 'licenses.json')
        if os.path.exists(cache_file):
            try:
                import json
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._licenses = data.get('licenses', [])
                    self._consistence = data.get('consistence', False)
                logger.info(f"Loaded {len(self._licenses)} licenses from cache")
            except Exception as e:
                logger.warning(f"Failed to load cached licenses: {e}")

    def _save_cached_licenses(self):
        """保存证书列表到本地缓存"""
        cache_file = os.path.join(self._license_dir, 'licenses.json')
        try:
            import json
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'licenses': self._licenses,
                    'consistence': self._consistence,
                    'update_time': int(time.time() * 1000)
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save cached licenses: {e}")

    def _download_license(self, url: str, fingerprint: str) -> Optional[str]:
        """
        下载证书文件并校验指纹

        Args:
            url: 证书文件URL
            fingerprint: 预期的MD5指纹

        Returns:
            本地文件路径，失败返回None
        """
        try:
            filename = os.path.basename(url) or f"license_{int(time.time())}.bin"
            local_path = os.path.join(self._license_dir, filename)

            logger.info(f"Downloading license from {url}...")
            urllib.request.urlretrieve(url, local_path)

            # 校验MD5
            if fingerprint:
                actual_md5 = self._calc_file_md5(local_path)
                if actual_md5.lower() != fingerprint.lower():
                    logger.error(f"License MD5 mismatch: expected={fingerprint}, actual={actual_md5}")
                    os.remove(local_path)
                    return None

            logger.info(f"License downloaded: {local_path}")
            return local_path
        except Exception as e:
            logger.error(f"Failed to download license: {e}")
            return None

    @staticmethod
    def _calc_file_md5(filepath: str) -> str:
        """计算文件MD5"""
        h = hashlib.md5()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()

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
        found = False
        for lic in self._licenses:
            if lic.get('common_fields', {}).get('license_id') == license_id:
                lic['common_fields']['enabled'] = enabled
                found = True
                break

        if found:
            self._save_cached_licenses()

            # 通知事件
            if self._event_callback:
                self._event_callback({
                    'method': 'unlock_license_switch_result',
                    'data': {'license_id': license_id, 'enabled': enabled}
                })

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

        # 下载证书文件
        local_path = self._download_license(url, fingerprint)
        if not local_path:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Failed to download license")

        # 更新本地证书列表
        new_license = {
            'common_fields': {
                'license_id': int(time.time()),
                'enabled': True,
                'fingerprint': fingerprint,
                'file_path': local_path,
                'update_time': int(time.time() * 1000)
            }
        }
        self._licenses.append(new_license)
        self._save_cached_licenses()

        # 通知事件
        if self._event_callback:
            self._event_callback({
                'method': 'unlock_license_update_result',
                'data': {'url': url, 'result': 'success'}
            })

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

        # 构造标准格式的证书列表
        formatted_licenses = []
        for lic in self._licenses:
            common = lic.get('common_fields', {})
            formatted_licenses.append({
                'common_fields': {
                    'license_id': common.get('license_id', 0),
                    'license_type': common.get('license_type', 0),
                    'enabled': common.get('enabled', False),
                    'create_time': common.get('create_time', 0),
                    'update_time': common.get('update_time', 0),
                    'fingerprint': common.get('fingerprint', ''),
                }
            })

        return MessageBuilder.build_reply(msg, result=0, data={
            'device_model_domain': device_model_domain,
            'consistence': self._consistence,
            'licenses': formatted_licenses
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
        self._save_cached_licenses()
        logger.info(f"Licenses updated: {len(licenses)} licenses, consistence={consistence}")

    def handle_flysafe_geo_fence_update(self, msg: dict) -> dict:
        """
        地理围栏更新 (flysafe_geo_fence_update)

        Request: { geo_fences: [{id, type, coordinates, ...}, ...] }
        Response: { result }
        """
        data = msg.get('data', {})
        geo_fences = data.get('geo_fences', [])
        logger.info(f"FlySafe geo fence update: {len(geo_fences)} fences")

        self._geo_fences = geo_fences

        # 保存到本地
        fence_file = os.path.join(self._license_dir, 'geo_fences.json')
        try:
            import json
            with open(fence_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'geo_fences': geo_fences,
                    'update_time': int(time.time() * 1000)
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save geo fences: {e}")

        return MessageBuilder.build_reply(msg, result=0)
