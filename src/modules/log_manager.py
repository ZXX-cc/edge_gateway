"""
设备日志管理模块

处理日志上传相关接口：
- fileupload_list: 获取可上传日志列表
- fileupload_start: 开始上传日志
- fileupload_update: 更新日志上传状态
"""
import threading
import urllib.error
import urllib.parse
import urllib.request
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
    scan_log_files,
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

    def _known_logs_by_boot_index(self) -> Dict[str, Dict[str, Any]]:
        logs = scan_log_files(self.log_dir, self.gateway_sn, module=3)
        indexed: Dict[str, Dict[str, Any]] = {}
        for item in logs:
            for key in ("boot_index", "file_id", "fingerprint"):
                value = item.get(key)
                if value not in (None, ""):
                    indexed[str(value)] = item
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

    def _upload_file_http(self, filepath: str, target_url: str) -> bool:
        try:
            with open(filepath, "rb") as handle:
                data = handle.read()
            request = urllib.request.Request(target_url, data=data, method="PUT")
            request.add_header("Content-Type", "application/octet-stream")
            request.add_header("Content-Length", str(len(data)))
            with urllib.request.urlopen(request, timeout=300) as response:
                return 200 <= int(response.status) < 300
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            logger.warning("Fileupload HTTP PUT failed: url=%s, error=%s", target_url, exc)
            return False

    def _run_upload_task(self, task_key: str):
        task = self._upload_tasks.get(task_key)
        if not task:
            return

        files = task.get("files", [])
        target = task.get("target", {})
        endpoint = target.get("endpoint", "")
        provider = target.get("provider", "")
        object_key = target.get("object_key", "")
        target_url = self._upload_target_url(endpoint, object_key)

        if not files:
            task["status"] = TASK_STATUS_REJECTED
            return

        if str(endpoint).startswith("mock://") or str(provider).lower() == "mock":
            task["status"] = TASK_STATUS_OK
            task["progress"] = 100
            self.report_fileupload_progress(TASK_STATUS_OK, files)
            return

        ok = bool(target_url)
        for item in files:
            if task.get("status") == TASK_STATUS_CANCELED:
                self.report_fileupload_progress(TASK_STATUS_CANCELED, files)
                return
            local_path = item.get("local_path", "")
            if not local_path or not Path(local_path).exists():
                logger.warning("Fileupload source missing: %s", local_path)
                ok = False
                break
            if not self._upload_file_http(local_path, target_url):
                ok = False
                break

        task["status"] = TASK_STATUS_OK if ok else TASK_STATUS_FAILED
        task["progress"] = 100 if ok else 0
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

        logs = scan_log_files(self.log_dir, self.gateway_sn, module=3)
        files = group_fileupload_files(logs, module_list, self.gateway_sn)

        return MessageBuilder.build_reply(msg, result=0, data={
            **task_output(TASK_STATUS_OK),
            'files': files,
            'total': sum(len(item.get('list', [])) for item in files),
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
        known_logs = self._known_logs_by_boot_index()
        new_task_keys = []
        for f in files:
            task_key = f"{f.get('module')}_{f.get('object_key')}"
            new_task_keys.append(task_key)
            flattened = []
            for item in f.get('list', []):
                boot_index = str(item.get('boot_index') or '')
                source = known_logs.get(boot_index, {})
                flattened.append({
                    **source,
                    **item,
                    'device_sn': self.gateway_sn,
                    'module': f.get('module', '3'),
                    'object_key': f.get('object_key', ''),
                    'key': f.get('object_key', ''),
                    'local_path': str(Path(self.log_dir) / source.get('path', '')) if source.get('path') else '',
                    'fingerprint': source.get('fingerprint') or item.get('fingerprint') or boot_index,
                    'size': int(source.get('size', item.get('size', 0)) or 0),
                })
            self._upload_tasks[task_key] = {
                'status': TASK_STATUS_IN_PROGRESS,
                'progress': 0,
                'files': flattened,
                'target': {
                    'bucket': bucket,
                    'region': region,
                    'endpoint': endpoint,
                    'provider': provider,
                    'object_key': f.get('object_key', ''),
                },
            }
            task_files.extend(flattened)

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

        if status not in {'cancel', 'pause', 'resume'}:
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data=task_output(TASK_STATUS_REJECTED, {
                    'errors': ['fileupload_update.status must be cancel/pause/resume'],
                }),
                msg_str='fileupload_update.status must be cancel/pause/resume',
            )

        updated_files = []
        if status == 'cancel':
            for task in self._upload_tasks.values():
                task_files = task.get('files', [])
                if not task_files:
                    continue
                if module_list and int(task_files[0].get('module', 3)) not in module_list:
                    continue
                task['status'] = TASK_STATUS_CANCELED
                task['progress'] = 100
                updated_files.extend(task_files)
            self.report_fileupload_progress(TASK_STATUS_CANCELED, updated_files)
        else:
            target_status = TASK_STATUS_IN_PROGRESS
            for task in self._upload_tasks.values():
                task_files = task.get('files', [])
                if not task_files:
                    continue
                if module_list and int(task_files[0].get('module', 3)) not in module_list:
                    continue
                task['status'] = target_status
                updated_files.extend(task_files)
            if updated_files:
                self.report_fileupload_progress(target_status, updated_files)

        return MessageBuilder.build_reply(msg, result=0, data=task_output(TASK_STATUS_OK, {
            'status': status,
            'updated': len(updated_files),
        }))
