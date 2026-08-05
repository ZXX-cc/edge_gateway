"""
设备日志管理模块

处理日志上传相关接口：
- fileupload_list: 获取可上传日志列表
- fileupload_start: 开始上传日志
- fileupload_update: 更新日志上传状态
"""
import threading
import http.client
import time
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from src.cloud_api.dock3_hms_log import (
    TASK_STATUS_CANCELED,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_OK,
    TASK_STATUS_REJECTED,
    group_fileupload_files,
    normalize_fileupload_start_files,
    normalize_module_list,
    resolve_log_root,
    scan_log_files_for_modules,
    task_output,
    upload_progress_event_data,
)

logger = get_logger("log_mgr")


class LogManager:
    """设备日志管理器"""

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()
        self._event_callback: Optional[Callable] = None
        self._upload_tasks: dict = {}  # 当前上传任务状态
        self._tasks_lock = threading.RLock()
        self._task_retention_seconds = int(Config.get("logging.upload_task_retention_seconds", 3600) or 3600)
        self.log_dir = resolve_log_root(Config.get('logging.dir', './logs'))

    def set_event_callback(self, callback: Callable):
        self._event_callback = callback

    def report_fileupload_progress(self, status: str, files: list):
        """
        上报文件上传进度 (fileupload_progress)

        Args:
            status: ok / in_progress / fail
            files: 文件进度列表
                [{device_sn, module, size, key, fingerprint,
                  progress: {progress, finish_time, upload_rate, status, result}}, ...]
        """
        if not self._event_callback:
            return
        event_data = upload_progress_event_data(
            files,
            device_sn=self.gateway_sn,
            status=status,
            progress=100 if status in {TASK_STATUS_OK, TASK_STATUS_CANCELED} else 0,
        )
        event = MessageBuilder.build_event('fileupload_progress', event_data, self.gateway_sn)
        self._event_callback(event)
        logger.info(f"Fileupload progress reported: status={status}, files={len(files)}")

    def _known_logs_by_boot_index(self, modules: list[int] | None = None) -> Dict[str, Dict[str, Any]]:
        module_list = modules or list(normalize_module_list({}))
        logs = scan_log_files_for_modules(self.log_dir, self.gateway_sn, module_list)
        indexed: Dict[str, Dict[str, Any]] = {}
        for item in logs:
            module = str(item.get("module", ""))
            for key in ("boot_index", "file_id", "fingerprint"):
                value = item.get(key)
                if value not in (None, ""):
                    indexed[str(value)] = item
                    indexed[f"{module}:{value}"] = item
        return indexed

    def _upload_target_url(self, endpoint: str, object_key: str) -> str:
        endpoint = str(endpoint or "").strip()
        object_key = str(object_key or "").lstrip("/")
        if not endpoint:
            return ""
        if endpoint.startswith("mock://"):
            return endpoint
        if endpoint.startswith(("http://", "https://")):
            return urllib.parse.urljoin(endpoint.rstrip("/") + "/", object_key)
        return ""

    @staticmethod
    def _credentials_expired(credentials: dict) -> bool:
        if not isinstance(credentials, dict):
            return False
        expire = credentials.get("expire")
        if expire in (None, ""):
            return False
        try:
            expire_value = int(float(expire))
        except (TypeError, ValueError):
            return True
        now = int(time.time() * (1000 if expire_value > 10_000_000_000 else 1))
        return expire_value <= now

    @staticmethod
    def _credential_headers(credentials: dict) -> dict:
        if not isinstance(credentials, dict):
            return {}
        token = credentials.get("security_token") or credentials.get("token") or credentials.get("session_token")
        headers = {}
        if token:
            headers["x-oss-security-token"] = str(token)
            headers["x-amz-security-token"] = str(token)
        return headers

    @staticmethod
    def _http_request_path(parsed: urllib.parse.ParseResult) -> str:
        path = parsed.path or "/"
        if parsed.params:
            path = f"{path};{parsed.params}"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path

    def _upload_file_http(
        self,
        filepath: str,
        target_url: str,
        credentials: dict | None = None,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        if self._credentials_expired(credentials or {}):
            logger.warning("Fileupload credential expired: url=%s", target_url)
            return False
        try:
            if cancel_event and cancel_event.is_set():
                return False
            parsed = urllib.parse.urlparse(target_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return False
            size = Path(filepath).stat().st_size
            connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
            connection = connection_cls(parsed.hostname, parsed.port, timeout=300)
            try:
                connection.putrequest("PUT", self._http_request_path(parsed))
                headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(size),
                }
                headers.update(self._credential_headers(credentials or {}))
                for name, value in headers.items():
                    connection.putheader(name, value)
                connection.endheaders()
                with open(filepath, "rb") as handle:
                    while True:
                        if cancel_event and cancel_event.is_set():
                            connection.close()
                            return False
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        connection.send(chunk)
                response = connection.getresponse()
                response.read()
                return 200 <= int(response.status) < 300
            finally:
                connection.close()
        except (OSError, urllib.error.URLError, TimeoutError, http.client.HTTPException) as exc:
            logger.warning("Fileupload HTTP PUT failed: url=%s, error=%s", target_url, exc)
            return False

    def _item_upload_key(self, parent_key: str, item: dict, source: dict, multiple: bool) -> str:
        item_key = item.get("object_key") or item.get("key")
        if item_key:
            return str(item_key).lstrip("/")
        source_key = source.get("object_key") or source.get("path") or source.get("name") or source.get("file_name")
        if not parent_key:
            return str(source_key or "").lstrip("/")
        parent_key = str(parent_key).lstrip("/")
        if not multiple:
            return parent_key
        file_name = str(source.get("name") or source.get("file_name") or source.get("boot_index") or "log")
        return f"{parent_key.rstrip('/')}/{file_name}".lstrip("/")

    def _cleanup_upload_tasks_locked(self):
        if self._task_retention_seconds <= 0:
            return
        now = time.time()
        expired = []
        for task_key, task in self._upload_tasks.items():
            status = task.get("status")
            updated_at = float(task.get("updated_at", task.get("created_at", now)) or now)
            if status == TASK_STATUS_IN_PROGRESS:
                continue
            if now - updated_at >= self._task_retention_seconds:
                expired.append(task_key)
        for task_key in expired:
            self._upload_tasks.pop(task_key, None)

    def _set_task_status(self, task_key: str, status: str, progress: int):
        with self._tasks_lock:
            task = self._upload_tasks.get(task_key)
            if task:
                task["status"] = status
                task["progress"] = progress
                task["updated_at"] = time.time()
                self._cleanup_upload_tasks_locked()

    def _run_upload_task(self, task_key: str):
        with self._tasks_lock:
            task = self._upload_tasks.get(task_key)
        if not task:
            return

        files = task.get("files", [])
        target = task.get("target", {})
        endpoint = target.get("endpoint", "")
        provider = target.get("provider", "")
        credentials = target.get("credentials", {})
        cancel_event = task.get("cancel_event")

        if not files:
            self._set_task_status(task_key, TASK_STATUS_REJECTED, 0)
            return

        if str(endpoint).startswith("mock://") or str(provider).lower() == "mock":
            for item in files:
                item["upload_status"] = TASK_STATUS_OK
                item["upload_result"] = 0
                item["upload_progress"] = 100
            self._set_task_status(task_key, TASK_STATUS_OK, 100)
            self.report_fileupload_progress(TASK_STATUS_OK, files)
            return

        ok = True
        for item in files:
            if cancel_event and cancel_event.is_set():
                item["upload_status"] = TASK_STATUS_CANCELED
                item["upload_result"] = 0
                item["upload_progress"] = 100
                self._set_task_status(task_key, TASK_STATUS_CANCELED, 100)
                self.report_fileupload_progress(TASK_STATUS_CANCELED, files)
                return
            local_path = item.get("local_path", "")
            if not local_path or not Path(local_path).exists():
                logger.warning("Fileupload source missing: %s", local_path)
                item["upload_status"] = TASK_STATUS_FAILED
                item["upload_result"] = INVALID_COMMAND_FORMAT
                item["upload_progress"] = 0
                ok = False
                continue
            target_url = self._upload_target_url(endpoint, item.get("object_key", ""))
            if not target_url:
                item["upload_status"] = TASK_STATUS_FAILED
                item["upload_result"] = INVALID_COMMAND_FORMAT
                item["upload_progress"] = 0
                ok = False
                continue
            if not self._upload_file_http(local_path, target_url, credentials, cancel_event):
                if cancel_event and cancel_event.is_set():
                    item["upload_status"] = TASK_STATUS_CANCELED
                    item["upload_result"] = 0
                    item["upload_progress"] = 100
                    self._set_task_status(task_key, TASK_STATUS_CANCELED, 100)
                    self.report_fileupload_progress(TASK_STATUS_CANCELED, files)
                    return
                item["upload_status"] = TASK_STATUS_FAILED
                item["upload_result"] = INVALID_COMMAND_FORMAT
                item["upload_progress"] = 0
                ok = False
                continue
            item["upload_status"] = TASK_STATUS_OK
            item["upload_result"] = 0
            item["upload_progress"] = 100

        self._set_task_status(task_key, TASK_STATUS_OK if ok else TASK_STATUS_FAILED, 100 if ok else 0)
        self.report_fileupload_progress(TASK_STATUS_OK if ok else TASK_STATUS_FAILED, files)

    def handle_fileupload_list(self, msg: dict) -> dict:
        """
        获取可上传日志文件列表 (fileupload_list)

        Request: { module_list: [0, 3] }
        Response: { result, files: [{device_sn, result, module, list: [{boot_index, start_time, end_time, size}]}, ...] }
        """
        data = msg.get('data', {})
        module_list = normalize_module_list(data)
        logger.info(f"Fileupload list: modules={module_list}")

        logs = scan_log_files_for_modules(self.log_dir, self.gateway_sn, module_list)
        files = group_fileupload_files(logs, module_list, self.gateway_sn)

        return MessageBuilder.build_reply(msg, result=0, data={
            'files': files,
        })

    def handle_fileupload_start(self, msg: dict) -> dict:
        """
        开始上传日志文件 (fileupload_start)

        Request: {
            bucket, region, endpoint, provider,
            credentials: {access_key_id, access_key_secret, expire, security_token},
            params: { files: [{object_key, module, list: [{boot_index}]}] }
        }
        Response: { result }
        """
        data = msg.get('data', {})
        bucket = data.get('bucket', '')
        region = data.get('region', '')
        endpoint = data.get('endpoint', '')
        provider = data.get('provider', '')
        credentials = data.get('credentials', {})
        files, errors = normalize_fileupload_start_files(data)
        if errors:
            logger.warning("Rejected fileupload_start payload: %s", "; ".join(errors))
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=task_output(TASK_STATUS_REJECTED, {"errors": errors}),
                msg_str="; ".join(errors),
            )

        logger.info(f"Fileupload start: provider={provider}, bucket={bucket}, "
                   f"files={len(files)}, creds_expire={credentials.get('expire', 0)}")

        task_files = []
        requested_modules = []
        for item in files:
            try:
                module = int(item.get("module", 3))
            except (TypeError, ValueError):
                module = 3
            if module not in requested_modules:
                requested_modules.append(module)
        known_logs = self._known_logs_by_boot_index(requested_modules)
        new_task_keys = []
        for file_index, f in enumerate(files):
            task_key = f"{msg.get('tid', '')}_{file_index}_{f.get('module')}_{f.get('object_key')}"
            new_task_keys.append(task_key)
            flattened = []
            source_items = f.get('list', [])
            multiple = len(source_items) > 1
            for item in source_items:
                boot_index = str(item.get('boot_index') or '')
                module = str(f.get('module', '3'))
                source = known_logs.get(f"{module}:{boot_index}") or known_logs.get(boot_index, {})
                if not source:
                    logger.warning("Fileupload source not found: module=%s, boot_index=%s", module, boot_index)
                    continue
                object_key = self._item_upload_key(f.get('object_key', ''), item, source, multiple)
                flattened.append({
                    **source,
                    **item,
                    'device_sn': self.gateway_sn,
                    'module': module,
                    'object_key': object_key,
                    'key': object_key,
                    'local_path': str(Path(self.log_dir) / source.get('path', '')) if source.get('path') else '',
                    'fingerprint': source.get('fingerprint') or item.get('fingerprint') or boot_index,
                    'size': int(source.get('size', item.get('size', 0)) or 0),
                    'upload_status': TASK_STATUS_IN_PROGRESS,
                    'upload_result': 0,
                    'upload_progress': 0,
                })
            if flattened:
                with self._tasks_lock:
                    self._upload_tasks[task_key] = {
                        'status': TASK_STATUS_IN_PROGRESS,
                        'progress': 0,
                        'files': flattened,
                        'cancel_event': threading.Event(),
                        'created_at': time.time(),
                        'updated_at': time.time(),
                        'target': {
                            'bucket': bucket,
                            'region': region,
                            'endpoint': endpoint,
                            'provider': provider,
                            'credentials': credentials,
                        },
                    }
            else:
                new_task_keys.remove(task_key)
            task_files.extend(flattened)

        if not task_files:
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=task_output(TASK_STATUS_REJECTED, {
                    'errors': ['fileupload_start selected logs not found'],
                }),
                msg_str='fileupload_start selected logs not found',
            )

        self.report_fileupload_progress(TASK_STATUS_IN_PROGRESS, task_files)
        for task_key in new_task_keys:
            thread = threading.Thread(
                target=self._run_upload_task,
                args=(task_key,),
                name=f"dock3-log-upload-{task_key}",
                daemon=True,
            )
            thread.start()

        return MessageBuilder.build_reply(msg, result=0, data=task_output(
            TASK_STATUS_IN_PROGRESS,
            {
                'task_id': msg.get('tid', ''),
                'files': files,
            },
        ))

    def handle_fileupload_update(self, msg: dict) -> dict:
        """
        更新日志文件上传状态 (fileupload_update)

        Request: { status: 'cancel', module_list: [0, 3] }
        Response: { result }
        """
        data = msg.get('data', {})
        status = data.get('status', '')
        module_list = normalize_module_list(data, default=[])

        logger.info(f"Fileupload update: status={status}, modules={module_list}")

        if status != 'cancel':
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=task_output(TASK_STATUS_REJECTED, {
                    'errors': ['fileupload_update.status must be cancel'],
                }),
                msg_str='fileupload_update.status must be cancel',
            )

        updated_files = []
        with self._tasks_lock:
            for task in self._upload_tasks.values():
                task_files = task.get('files', [])
                if not task_files:
                    continue
                if module_list and int(task_files[0].get('module', 3)) not in module_list:
                    continue
                task['status'] = TASK_STATUS_CANCELED
                task['progress'] = 100
                task['updated_at'] = time.time()
                cancel_event = task.get("cancel_event")
                if cancel_event:
                    cancel_event.set()
                for item in task_files:
                    item["upload_status"] = TASK_STATUS_CANCELED
                    item["upload_result"] = 0
                    item["upload_progress"] = 100
                updated_files.extend(task_files)
            self._cleanup_upload_tasks_locked()
        if updated_files:
            self.report_fileupload_progress(TASK_STATUS_CANCELED, updated_files)

        return MessageBuilder.build_reply(msg, result=0, data=task_output(TASK_STATUS_OK, {
            'status': status,
            'updated': len(updated_files),
        }))
