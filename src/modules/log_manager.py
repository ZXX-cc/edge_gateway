"""
设备日志管理模块

处理日志上传相关接口：
- fileupload_list: 获取可上传日志列表
- fileupload_start: 开始上传日志
- fileupload_update: 更新日志上传状态
"""
from typing import Dict, Any, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from src.cloud_api.dock3_hms_log import (
    TASK_STATUS_CANCELED,
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
        for f in files:
            task_key = f"{f.get('module')}_{f.get('object_key')}"
            flattened = []
            for item in f.get('list', []):
                flattened.append({
                    **item,
                    'device_sn': self.gateway_sn,
                    'module': f.get('module', '3'),
                    'object_key': f.get('object_key', ''),
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
                },
            }
            task_files.extend(flattened)

        self.report_fileupload_progress(TASK_STATUS_IN_PROGRESS, task_files)

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
