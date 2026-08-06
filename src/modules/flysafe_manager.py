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
from src.utils.dji_error_codes import (
    DEVICE_STATE_UNSUPPORTED,
    DOCK_SYSTEM_ERROR,
    INVALID_COMMAND_FORMAT,
)

logger = get_logger("flysafe")

LICENSE_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'flysafe')


class FlySafeManager:
    """FlySafe管理器 - 解禁证书管理"""

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()
        self._event_callback: Optional[Callable] = None
        self._device_applier: Optional[Any] = None

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

    def set_device_applier(self, applier):
        """设置设备侧解禁证书生效器。

        当前项目没有 DJI FlySafe SDK 直连实现，调用方可注入真实设备适配器。
        适配器可为 callable，或实现 apply_unlock_license_switch/apply_license_switch。
        """
        self._device_applier = applier

    def _emit_event(self, method: str, data: dict):
        if self._event_callback:
            self._event_callback(MessageBuilder.build_event(method, data, self.gateway_sn))

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_bool(value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "启用")
        return bool(value)

    @staticmethod
    def _first_present(default, *values):
        for value in values:
            if value is not None:
                return value
        return default

    @staticmethod
    def _common_fields(license_entry: dict) -> dict:
        if not isinstance(license_entry, dict):
            return {}
        common = license_entry.get('common_fields')
        return common if isinstance(common, dict) else {}

    def _find_license(self, license_id: int):
        for index, license_entry in enumerate(self._licenses):
            if self._common_fields(license_entry).get('license_id') == license_id:
                return index, license_entry
        return -1, None

    def _normalize_apply_result(self, result) -> dict:
        if isinstance(result, dict):
            success = bool(result.get('success', result.get('applied', False)))
            return {
                'success': success,
                'applied': bool(result.get('applied', success)),
                'reason': str(result.get('reason') or ('accepted' if success else 'device_apply_failed')),
                **{k: v for k, v in result.items() if k not in ('success', 'applied', 'reason')},
            }
        return {
            'success': bool(result),
            'applied': bool(result),
            'reason': 'accepted' if result else 'device_apply_failed',
        }

    def _apply_license_switch_to_device(self, license_entry: dict, enabled: bool) -> dict:
        if not self._device_applier:
            return {
                'success': False,
                'applied': False,
                'reason': 'flysafe_device_applier_not_configured',
            }

        try:
            if callable(self._device_applier):
                return self._normalize_apply_result(self._device_applier(license_entry, enabled))
            if hasattr(self._device_applier, 'apply_unlock_license_switch'):
                return self._normalize_apply_result(
                    self._device_applier.apply_unlock_license_switch(license_entry, enabled)
                )
            if hasattr(self._device_applier, 'apply_license_switch'):
                return self._normalize_apply_result(
                    self._device_applier.apply_license_switch(license_entry, enabled)
                )
            return {
                'success': False,
                'applied': False,
                'reason': 'flysafe_device_applier_method_missing',
            }
        except Exception as e:
            logger.error(f"Failed to apply unlock license to device: {e}", exc_info=True)
            return {
                'success': False,
                'applied': False,
                'reason': str(e),
            }

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
        if not isinstance(data, dict):
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str="unlock_license_switch data must be object",
            )

        license_id = self._to_int(data.get('license_id'), 0)
        if not license_id:
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str="unlock_license_switch license_id required",
            )
        if not isinstance(data.get('enabled'), bool):
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str="unlock_license_switch enabled must be bool",
            )
        enabled = data.get('enabled', False)
        logger.info(f"Unlock license switch: id={license_id}, enabled={enabled}")

        index, license_entry = self._find_license(license_id)
        if index < 0:
            logger.warning(f"Unlock license switch rejected: license_id not found: {license_id}")
            self._emit_event('unlock_license_switch_result', {
                'license_id': license_id,
                'enabled': enabled,
                'result': DEVICE_STATE_UNSUPPORTED,
                'status': 'failed',
                'reason': 'license_id_not_found',
            })
            return MessageBuilder.build_reply(
                msg,
                result=DEVICE_STATE_UNSUPPORTED,
                data={
                    'license_id': license_id,
                    'found': False,
                    'applied': False,
                },
                msg_str="license_id not found",
            )

        apply_result = self._apply_license_switch_to_device(license_entry, enabled)
        if not apply_result.get('success'):
            logger.error(f"Unlock license switch device apply failed: {apply_result}")
            self._emit_event('unlock_license_switch_result', {
                'license_id': license_id,
                'enabled': enabled,
                'result': DOCK_SYSTEM_ERROR,
                'status': 'failed',
                'found': True,
                'applied': False,
                'reason': apply_result.get('reason', 'device_apply_failed'),
            })
            return MessageBuilder.build_reply(
                msg,
                result=DOCK_SYSTEM_ERROR,
                data={
                    'license_id': license_id,
                    'found': True,
                    'applied': False,
                    'reason': apply_result.get('reason', 'device_apply_failed'),
                },
                msg_str="unlock license device apply failed",
            )

        self._licenses[index]['common_fields']['enabled'] = enabled
        self._licenses[index]['common_fields']['update_time'] = int(time.time() * 1000)
        self._save_cached_licenses()

        self._emit_event('unlock_license_switch_result', {
            'license_id': license_id,
            'enabled': enabled,
            'result': 0,
            'status': 'ok',
            'found': True,
            'applied': True,
        })

        return MessageBuilder.build_reply(msg, result=0, data={
            'license_id': license_id,
            'found': True,
            'applied': True,
        })

    def handle_unlock_license_update(self, msg: dict) -> dict:
        """
        解禁证书更新 (unlock_license_update)

        Request: { file: { url, fingerprint } }
        Response: { result }
        """
        data = msg.get('data', {})
        if not isinstance(data, dict):
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str="unlock_license_update data must be object",
            )
        file_info = data.get('file', {})
        if not isinstance(file_info, dict):
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str="unlock_license_update file must be object",
            )
        url = file_info.get('url', '')
        fingerprint = file_info.get('fingerprint', '')

        logger.info(f"Unlock license update: url={url}, fingerprint={fingerprint}")

        # 下载证书文件
        local_path = self._download_license(url, fingerprint)
        if not local_path:
            self._emit_event('unlock_license_update_result', {
                'url': url,
                'result': DOCK_SYSTEM_ERROR,
                'status': 'failed',
                'reason': 'download_or_md5_verify_failed',
            })
            return MessageBuilder.build_reply(
                msg,
                result=DOCK_SYSTEM_ERROR,
                msg_str="Failed to download license",
            )

        # 更新本地证书列表
        now_ms = int(time.time() * 1000)
        common_input = data.get('common_fields') or file_info.get('common_fields') or {}
        common_input = common_input if isinstance(common_input, dict) else {}
        license_id = self._to_int(
            self._first_present(
                int(time.time()),
                common_input.get('license_id'),
                data.get('license_id'),
                file_info.get('license_id'),
            ),
            int(time.time()),
        )
        license_type = self._to_int(
            self._first_present(
                0,
                common_input.get('license_type'),
                data.get('license_type'),
                file_info.get('license_type'),
            ),
            0,
        )
        device_model_domain = self._to_int(
            self._first_present(
                0,
                common_input.get('device_model_domain'),
                data.get('device_model_domain'),
                file_info.get('device_model_domain'),
            ),
            0,
        )
        create_time = self._to_int(
            self._first_present(
                now_ms,
                common_input.get('create_time'),
                data.get('create_time'),
                file_info.get('create_time'),
            ),
            now_ms,
        )
        enabled = self._to_bool(
            self._first_present(
                False,
                common_input.get('enabled'),
                data.get('enabled'),
                file_info.get('enabled'),
            ),
            False,
        )
        new_license = {
            'common_fields': {
                'license_id': license_id,
                'license_type': license_type,
                'enabled': enabled,
                'device_model_domain': device_model_domain,
                'create_time': create_time,
                'fingerprint': fingerprint,
                'file_path': local_path,
                'update_time': now_ms,
            }
        }
        index, _ = self._find_license(license_id)
        if index >= 0:
            self._licenses[index] = new_license
        else:
            self._licenses.append(new_license)
        self._save_cached_licenses()

        self._emit_event('unlock_license_update_result', {
            'url': url,
            'license_id': license_id,
            'license_type': license_type,
            'device_model_domain': device_model_domain,
            'result': 0,
            'status': 'ok',
        })

        return MessageBuilder.build_reply(msg, result=0, data={
            'license_id': license_id,
            'license_type': license_type,
            'device_model_domain': device_model_domain,
        })

    def handle_unlock_license_list(self, msg: dict) -> dict:
        """
        获取解禁证书列表 (unlock_license_list)

        Request: { device_model_domain: 0=飞行器, 3=机场 }
        Response: { result, device_model_domain, consistence, licenses }
        """
        data = msg.get('data', {})
        if not isinstance(data, dict):
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str="unlock_license_list data must be object",
            )
        device_model_domain = self._to_int(data.get('device_model_domain'), 0)
        logger.info(f"Unlock license list: domain={device_model_domain}")

        # 构造标准格式的证书列表
        formatted_licenses = []
        for lic in self._licenses:
            common = lic.get('common_fields', {})
            license_domain = common.get('device_model_domain')
            if license_domain is not None and self._to_int(license_domain, -1) != device_model_domain:
                continue
            formatted_licenses.append({
                'common_fields': {
                    'license_id': common.get('license_id', 0),
                    'license_type': common.get('license_type', 0),
                    'enabled': common.get('enabled', False),
                    'device_model_domain': common.get('device_model_domain', device_model_domain),
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
