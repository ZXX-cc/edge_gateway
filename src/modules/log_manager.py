"""
设备日志管理模块

处理日志上传相关接口：
- fileupload_list: 获取可上传日志列表
- fileupload_start: 开始上传日志
- fileupload_update: 更新日志上传状态
"""
import time
from typing import Dict, Any, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder

logger = get_logger("log_mgr")


class LogManager:
    """设备日志管理器"""

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()
        self._event_callback: Optional[Callable] = None
        self._upload_tasks: dict = {}  # 当前上传任务状态

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
        if self._event_callback:
            event = MessageBuilder.build_event(
                'fileupload_progress',
                {
                    'result': 0,
                    'output': {
                        'status': status,
                        'ext': {'files': files}
                    }
                },
                self.gateway_sn
            )
            self._event_callback(event)
            logger.info(f"Fileupload progress reported: status={status}, files={len(files)}")

    def handle_fileupload_list(self, msg: dict) -> dict:
        """
        获取可上传日志文件列表 (fileupload_list)

        Request: { module_list: [0, 3] }
        Response: { result, files: [{device_sn, result, module, list: [{boot_index, start_time, end_time, size}]}, ...] }
        """
        data = msg.get('data', {})
        module_list = data.get('module_list', [])
        logger.info(f"Fileupload list: modules={module_list}")

        # TODO: 扫描日志目录获取可上传文件列表
        # 按 module 分组返回
        files = []
        for module in module_list:
            files.append({
                'device_sn': self.gateway_sn,
                'module': str(module),
                'result': 0,
                'list': []  # TODO: 填充真实的日志文件索引
            })

        return MessageBuilder.build_reply(msg, result=0, data={
            'files': files
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
        params = data.get('params', {})
        files = params.get('files', [])

        logger.info(f"Fileupload start: provider={provider}, bucket={bucket}, "
                   f"files={len(files)}, creds_expire={credentials.get('expire', 0)}")

        # TODO: 使用临时凭证启动日志上传流程
        # 保存任务状态
        for f in files:
            task_key = f"{f.get('module')}_{f.get('object_key')}"
            self._upload_tasks[task_key] = {'status': 'uploading', 'progress': 0}

        return MessageBuilder.build_reply(msg, result=0)

    def handle_fileupload_update(self, msg: dict) -> dict:
        """
        更新日志文件上传状态 (fileupload_update)

        Request: { status: 'cancel', module_list: [0, 3] }
        Response: { result }
        """
        data = msg.get('data', {})
        status = data.get('status', '')
        module_list = data.get('module_list', [])

        logger.info(f"Fileupload update: status={status}, modules={module_list}")

        if status == 'cancel':
            # 取消指定模块的上传任务
            for module in module_list:
                # TODO: 取消对应模块的上传任务
                pass

        return MessageBuilder.build_reply(msg, result=0)
