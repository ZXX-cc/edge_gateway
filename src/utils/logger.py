"""
日志模块 - 统一日志管理
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from datetime import datetime


class Logger:
    """统一日志管理器"""

    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, name="edge_gateway", level="INFO", log_file=None, max_size="10MB", backup_count=5):
        if Logger._initialized:
            return

        self.name = name
        self.level = getattr(logging, level.upper(), logging.INFO)
        self.log_file = log_file
        self.backup_count = backup_count

        # 解析max_size
        self.max_bytes = self._parse_size(max_size)

        # 创建logger
        self.logger = logging.getLogger(name)
        self.logger.setLevel(self.level)
        self.logger.handlers = []

        # 格式化器
        formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # 控制台输出
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(self.level)
        console.setFormatter(formatter)
        self.logger.addHandler(console)

        # 文件输出
        if self.log_file:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            file_handler = RotatingFileHandler(
                self.log_file,
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(self.level)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)

        Logger._initialized = True

    def _parse_size(self, size_str):
        """解析大小字符串为字节数"""
        units = [('GB', 1024**3), ('MB', 1024**2), ('KB', 1024), ('B', 1)]
        size_str = size_str.upper().strip()
        for unit, mult in units:
            if size_str.endswith(unit):
                return int(float(size_str[:-len(unit)])) * mult
        return int(float(size_str))

    def debug(self, msg, *args, **kwargs): self.logger.debug(msg, *args, **kwargs)
    def info(self, msg, *args, **kwargs): self.logger.info(msg, *args, **kwargs)
    def warning(self, msg, *args, **kwargs): self.logger.warning(msg, *args, **kwargs)
    def error(self, msg, *args, **kwargs): self.logger.error(msg, *args, **kwargs)
    def critical(self, msg, *args, **kwargs): self.logger.critical(msg, *args, **kwargs)

    def get_logger(self, sub_name):
        """获取子模块logger"""
        return logging.getLogger(f"{self.name}.{sub_name}")


# 全局logger实例
g_logger = None


def init_logger(config):
    """初始化全局logger"""
    global g_logger
    g_logger = Logger(
        name="edge_gateway",
        level=config.get('level', 'INFO'),
        log_file=config.get('file'),
        max_size=config.get('max_size', '10MB'),
        backup_count=config.get('backup_count', 5)
    )
    return g_logger


def get_logger(name="edge_gateway"):
    """获取logger实例"""
    if g_logger is None:
        return Logger(name=name)
    if name == "edge_gateway":
        return g_logger
    return g_logger.get_logger(name)
