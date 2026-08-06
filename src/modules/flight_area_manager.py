"""
禁飞区管理模块

处理自定义飞行区域相关接口：
- flight_areas_update: 更新飞行区域
- flight_areas_get: 获取飞行区域
"""
import hashlib
import json
import os
import time
import urllib.request
from typing import Dict, Any, Optional, Callable, List, Tuple
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.utils.dji_error_codes import DOCK_SYSTEM_ERROR, INVALID_COMMAND_FORMAT

logger = get_logger("flight_area")

REASON_SUCCESS = 0
REASON_CLOUD_INFO_ERROR = 1
REASON_DOWNLOAD_FAILED = 3
REASON_FILE_TRANSFER_FAILED = 5
REASON_AIRCRAFT_LOAD_FAILED = 8
REASON_CHECKSUM_FAILED = 12
REASON_TIMEOUT = 13

FLIGHT_AREA_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data',
    'flight_area',
)


class FlightAreaSyncError(Exception):
    """Error with a Dock3 custom-fly-region sync reason code."""

    def __init__(self, reason: int, message: str, file_info: dict = None):
        super().__init__(message)
        self.reason = reason
        self.file_info = file_info or {}


class FlightAreaManager:
    """禁飞区/飞行区域管理器"""

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()
        self._event_callback: Optional[Callable] = None
        self._request_sender: Optional[Callable] = None
        self._device_applier: Optional[Any] = None
        self._cache_dir = FLIGHT_AREA_CACHE_DIR
        self._cache_file = os.path.join(self._cache_dir, 'flight_areas.json')
        self._file_dir = os.path.join(self._cache_dir, 'files')
        self._files: List[dict] = []
        self._last_sync: Dict[str, Any] = {
            'status': 'wait_sync',
            'reason': 0,
            'update_time': 0,
        }

        os.makedirs(self._file_dir, exist_ok=True)
        self._load_cache()

    def set_event_callback(self, callback: Callable):
        self._event_callback = callback

    def set_request_sender(self, sender: Callable):
        """设置请求发送器（用于 requests 通道）"""
        self._request_sender = sender

    def set_device_applier(self, applier):
        """设置设备侧飞行区生效器。"""
        self._device_applier = applier

    def request_flight_areas_get(self, callback: Callable = None) -> str:
        """
        主动获取自定义飞行区文件列表 (flight_areas_get)
        Topic: requests / requests_reply
        """
        if not self._request_sender:
            logger.warning("Request sender not set")
            return ""
        return self._request_sender(
            'flight_areas_get',
            None,
            callback or self._handle_flight_areas_get_reply,
        )

    def _load_cache(self):
        """从本地缓存加载飞行区元数据。"""
        if not os.path.exists(self._cache_file):
            return
        try:
            with open(self._cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._files = data.get('files', []) if isinstance(data.get('files'), list) else []
            if isinstance(data.get('last_sync'), dict):
                self._last_sync.update(data['last_sync'])
            logger.info(
                "Loaded flight area cache: %s files",
                len(self._files),
            )
        except Exception as e:
            logger.warning(f"Failed to load flight area cache: {e}")

    def _save_cache(self):
        """保存飞行区缓存。"""
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            with open(self._cache_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'files': self._files,
                    'last_sync': self._last_sync,
                    'update_time': int(time.time() * 1000),
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save flight area cache: {e}")

    @staticmethod
    def _payload_root(data: dict) -> dict:
        if not isinstance(data, dict):
            return {}
        output = data.get('output')
        return output if isinstance(output, dict) else data

    @staticmethod
    def _safe_filename(file_info: dict, index: int) -> str:
        raw_name = (
            file_info.get('name')
            or file_info.get('filename')
            or file_info.get('file_name')
            or os.path.basename(str(file_info.get('url', '')))
            or f'flight_area_{index}.json'
        )
        name = os.path.basename(str(raw_name)).strip()
        return name or f'flight_area_{index}.json'

    @staticmethod
    def _checksum_fields(file_info: dict) -> Tuple[str, str]:
        checksum = str(
            file_info.get('checksum')
            or file_info.get('sha256')
            or file_info.get('fingerprint')
            or file_info.get('md5')
            or ''
        ).strip().lower()
        if not checksum:
            return '', ''
        if file_info.get('sha256') or len(checksum) == 64:
            return 'sha256', checksum
        if file_info.get('md5') or len(checksum) == 32:
            return 'md5', checksum
        return '', checksum

    @staticmethod
    def _calc_file_hash(filepath: str, algorithm: str) -> str:
        h = hashlib.new(algorithm)
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()

    def _download_flight_area_file(self, file_info: dict, index: int) -> dict:
        url = str(file_info.get('url') or file_info.get('download_url') or '').strip()
        if not url:
            raise FlightAreaSyncError(REASON_CLOUD_INFO_ERROR, 'flight area file url required', file_info)

        name = self._safe_filename(file_info, index)
        local_path = os.path.join(self._file_dir, name)
        logger.info(f"Downloading flight area file: {name} from {url}")

        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                content = response.read()
        except TimeoutError as e:
            raise FlightAreaSyncError(REASON_TIMEOUT, f'timeout downloading {name}: {e}', file_info)
        except Exception as e:
            raise FlightAreaSyncError(REASON_DOWNLOAD_FAILED, f'failed to download {name}: {e}', file_info)
        try:
            with open(local_path, 'wb') as f:
                f.write(content)
        except Exception as e:
            raise FlightAreaSyncError(REASON_FILE_TRANSFER_FAILED, f'failed to save {name}: {e}', file_info)

        expected_size = file_info.get('size')
        if expected_size is not None:
            try:
                expected_size = int(expected_size)
            except (TypeError, ValueError):
                raise FlightAreaSyncError(REASON_CLOUD_INFO_ERROR, f'invalid size for {name}: {file_info.get("size")}', file_info)
            if expected_size >= 0 and len(content) != expected_size:
                try:
                    os.remove(local_path)
                except OSError:
                    pass
                raise FlightAreaSyncError(
                    REASON_CHECKSUM_FAILED,
                    f'size mismatch for {name}: expected={expected_size}, actual={len(content)}',
                    file_info,
                )

        algorithm, expected = self._checksum_fields(file_info)
        actual = ''
        if algorithm:
            actual = self._calc_file_hash(local_path, algorithm)
            if expected and actual.lower() != expected:
                try:
                    os.remove(local_path)
                except OSError:
                    pass
                raise FlightAreaSyncError(
                    REASON_CHECKSUM_FAILED,
                    f'checksum mismatch for {name}: expected={expected}, actual={actual}',
                    file_info,
                )

        cached = dict(file_info)
        cached.update({
            'name': name,
            'local_path': local_path,
            'size': len(content),
            'update_time': int(time.time() * 1000),
        })
        if actual:
            cached[algorithm] = actual
        return cached

    @staticmethod
    def _extract_files(msg: dict) -> List[dict]:
        data = msg.get('data', {}) if isinstance(msg, dict) else {}
        root = FlightAreaManager._payload_root(data)
        files = root.get('files', data.get('files', []) if isinstance(data, dict) else [])
        if files is None:
            files = []
        if not isinstance(files, list):
            raise ValueError('files must be a list')
        return files

    def _set_last_sync(self, status: str, reason: int = 0, file_info: dict = None):
        self._last_sync = {
            'status': status,
            'reason': reason,
            'update_time': int(time.time() * 1000),
        }
        if file_info:
            self._last_sync['file'] = file_info

    def _sync_from_cloud_reply(self, msg: dict) -> bool:
        data = msg.get('data', {}) if isinstance(msg, dict) else {}
        if not isinstance(data, dict):
            self._set_last_sync('fail', REASON_CLOUD_INFO_ERROR)
            self._save_cache()
            self.report_flight_areas_sync_progress('fail', REASON_CLOUD_INFO_ERROR)
            return False
        result = data.get('result', 0)
        if result:
            self._set_last_sync('fail', REASON_CLOUD_INFO_ERROR)
            self._save_cache()
            self.report_flight_areas_sync_progress('fail', self._last_sync['reason'])
            return False

        try:
            files = self._extract_files(msg)
            self.report_flight_areas_sync_progress('synchronizing', 0)

            cached_files = []
            for index, file_info in enumerate(files):
                if not isinstance(file_info, dict):
                    raise FlightAreaSyncError(REASON_CLOUD_INFO_ERROR, 'file item must be an object')
                cached = self._download_flight_area_file(file_info, index)
                cached_files.append(cached)
                self.report_flight_areas_sync_progress('synchronizing', 0, {
                    'name': cached.get('name', ''),
                    'checksum': cached.get('checksum') or cached.get('sha256') or cached.get('md5') or '',
                })

            self._files = cached_files

            apply_result = self._apply_to_device(cached_files)
            if not apply_result.get('success'):
                detail = apply_result.get('reason', 'flight_area_device_apply_failed')
                logger.error(f"Flight area device apply failed: {apply_result}")
                self._set_last_sync('fail', REASON_AIRCRAFT_LOAD_FAILED, {'reason': detail})
                self._save_cache()
                self.report_flight_areas_sync_progress('fail', REASON_AIRCRAFT_LOAD_FAILED, {
                    'reason': detail,
                })
                return False

            self._set_last_sync('synchronized', 0)
            self._save_cache()
            self.report_flight_areas_sync_progress('synchronized', 0)
            logger.info(
                "Flight areas synchronized: %s files",
                len(self._files),
            )
            return True
        except FlightAreaSyncError as e:
            logger.error(f"Flight area sync failed: {e}")
            file_info = {'reason': str(e)}
            if e.file_info:
                file_info.update({
                    'name': e.file_info.get('name', ''),
                    'checksum': e.file_info.get('checksum', ''),
                })
            self._set_last_sync('fail', e.reason, file_info)
            self._save_cache()
            self.report_flight_areas_sync_progress('fail', e.reason, file_info)
            return False
        except Exception as e:
            logger.error(f"Failed to synchronize flight areas: {e}")
            self._set_last_sync('fail', REASON_CLOUD_INFO_ERROR)
            self._save_cache()
            self.report_flight_areas_sync_progress('fail', REASON_CLOUD_INFO_ERROR)
            return False

    def _apply_to_device(self, files: list) -> dict:
        if not self._device_applier:
            return {
                'success': False,
                'reason': 'flight_area_device_applier_not_configured',
                'applied': False,
            }
        try:
            return self._device_applier.apply_files(files)
        except Exception as e:
            logger.error(f"Flight area device applier error: {e}", exc_info=True)
            return {
                'success': False,
                'reason': str(e),
                'applied': False,
            }

    def _handle_flight_areas_get_reply(self, method: str, msg: dict):
        if method != 'flight_areas_get':
            logger.warning(f"Unexpected flight area reply method: {method}")
            return
        self._sync_from_cloud_reply(msg)

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
            event['need_reply'] = 1
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
            event['need_reply'] = 0
            self._event_callback(event)
            logger.info(f"Flight areas drone location: {len(locations)} areas")

    def handle_flight_areas_update(self, msg: dict) -> dict:
        """
        更新飞行区域 (flight_areas_update)

        Dock3 格式：云端下发空指令，设备收到后主动请求文件列表并同步。
        """
        data = msg.get('data')
        if data not in (None, {}):
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str="data must be null",
            )

        logger.info("Flight areas update: triggering sync from cloud")
        self.report_flight_areas_sync_progress('wait_sync', 0)
        tid = self.request_flight_areas_get()
        if not tid:
            self._set_last_sync('fail', REASON_CLOUD_INFO_ERROR)
            self._save_cache()
            self.report_flight_areas_sync_progress('fail', REASON_CLOUD_INFO_ERROR)
            return MessageBuilder.build_reply(
                msg,
                result=DOCK_SYSTEM_ERROR,
                msg_str='Request sender not set',
            )
        return MessageBuilder.build_reply(msg, result=0)

    def handle_flight_areas_get(self, msg: dict) -> dict:
        """
        获取飞行区域 (flight_areas_get) - services 兼容模式

        文档标准格式为 requests / requests_reply，但保留 services 兼容。
        """
        logger.info("Flight areas get (services mode)")
        return MessageBuilder.build_reply(msg, result=0, data={
            'output': {
                'files': [self._public_file_info(file_info) for file_info in self._files],
            },
        })

    @staticmethod
    def _public_file_info(file_info: dict) -> dict:
        output = {}
        for key in ('name', 'url', 'checksum', 'size'):
            if key in file_info:
                output[key] = file_info[key]
        return output
