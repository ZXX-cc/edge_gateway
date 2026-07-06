"""
日志模块 - 统一日志管理

日志落盘规则：
- logs/YYYY-MM/edge_gateway_YYYY-MM-DD.log
- 每天一个独立文件，每月一个独立目录
- 控制台、文件、stdout/stderr 捕获共用同一套格式
"""
import logging
import atexit
import os
import queue
import sys
import threading
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from typing import Optional


_BASE_LOGGER_NAME = "edge_gateway"
_ORIGINAL_STDOUT = sys.__stdout__
_ORIGINAL_STDERR = sys.__stderr__
_configured = False
_listener: Optional[QueueListener] = None


class DailyMonthlyFileHandler(logging.Handler):
    """按月份建目录、按日期写文件的日志 handler。"""

    def __init__(self, base_dir: str, filename_prefix: str = "edge_gateway", encoding: str = "utf-8"):
        super().__init__()
        self.base_dir = Path(base_dir)
        self.filename_prefix = filename_prefix
        self.encoding = encoding
        self._current_path: Optional[Path] = None
        self._stream = None
        self._stream_lock = threading.RLock()

    def _path_for_record(self, record: logging.LogRecord) -> Path:
        record_dt = datetime.fromtimestamp(record.created)
        month_dir = record_dt.strftime("%Y-%m")
        day_name = record_dt.strftime("%Y-%m-%d")
        return self.base_dir / month_dir / f"{self.filename_prefix}_{day_name}.log"

    def _open_for_record(self, record: logging.LogRecord):
        target_path = self._path_for_record(record)
        if self._stream and self._current_path == target_path:
            return

        if self._stream:
            self._stream.close()
            self._stream = None

        target_path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = target_path.open("a", encoding=self.encoding)
        self._current_path = target_path

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            with self._stream_lock:
                self._open_for_record(record)
                self._stream.write(msg + "\n")
                self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        with self._stream_lock:
            if self._stream:
                self._stream.close()
                self._stream = None
        super().close()


class StreamToLogger:
    """把 print/stdout/stderr 按行转成日志，避免旧模块 print 丢失。"""

    def __init__(self, logger_name: str, level: int):
        self.logger = logging.getLogger(logger_name)
        self.level = level
        self._buffer = ""

    def write(self, message: str):
        if not message:
            return
        self._buffer += message
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip()
            if line:
                self.logger.log(self.level, line)

    def flush(self):
        line = self._buffer.rstrip()
        if line:
            self.logger.log(self.level, line)
        self._buffer = ""

    def isatty(self):
        return False


class Logger:
    """兼容旧代码的轻量包装器。"""

    def __init__(self, name: str = _BASE_LOGGER_NAME, **kwargs):
        self.logger = get_logger(name)

    def debug(self, msg, *args, **kwargs): self.logger.debug(msg, *args, **kwargs)
    def info(self, msg, *args, **kwargs): self.logger.info(msg, *args, **kwargs)
    def warning(self, msg, *args, **kwargs): self.logger.warning(msg, *args, **kwargs)
    def error(self, msg, *args, **kwargs): self.logger.error(msg, *args, **kwargs)
    def critical(self, msg, *args, **kwargs): self.logger.critical(msg, *args, **kwargs)

    def get_logger(self, sub_name):
        return get_logger(sub_name)


def _parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _resolve_log_dir(config: dict) -> tuple[str, str]:
    """解析新旧日志配置，返回 (base_dir, filename_prefix)。"""
    filename_prefix = config.get("filename_prefix", "edge_gateway")
    base_dir = config.get("dir") or config.get("base_dir")

    if not base_dir:
        legacy_file = config.get("file")
        if legacy_file:
            legacy_path = Path(legacy_file)
            filename_prefix = legacy_path.stem or filename_prefix
            base_dir = legacy_path.parent / filename_prefix
        else:
            base_dir = "logs"

    return str(base_dir), filename_prefix


def _build_handlers(config: dict, level: int) -> list[logging.Handler]:
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] "
        "[%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    handlers: list[logging.Handler] = []
    if _parse_bool(config.get("console"), True):
        console = logging.StreamHandler(_ORIGINAL_STDOUT)
        console.setLevel(level)
        console.setFormatter(formatter)
        handlers.append(console)

    base_dir, filename_prefix = _resolve_log_dir(config)
    file_handler = DailyMonthlyFileHandler(base_dir, filename_prefix)
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)

    return handlers


def _replace_handlers(logger: logging.Logger, handlers: list[logging.Handler], level: int):
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    logger.setLevel(level)
    for handler in handlers:
        logger.addHandler(handler)


def _shutdown_logging():
    global _listener
    if _listener:
        _listener.stop()
        _listener = None


def _install_exception_hooks():
    def log_uncaught(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger(f"{_BASE_LOGGER_NAME}.exception").critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback)
        )

    def log_thread_exception(args):
        logging.getLogger(f"{_BASE_LOGGER_NAME}.thread").critical(
            "Uncaught thread exception: thread=%s", args.thread.name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )

    sys.excepthook = log_uncaught
    if hasattr(threading, "excepthook"):
        threading.excepthook = log_thread_exception


def _capture_standard_streams(config: dict):
    if not _parse_bool(config.get("capture_print"), True):
        return

    if not isinstance(sys.stdout, StreamToLogger):
        sys.stdout = StreamToLogger(f"{_BASE_LOGGER_NAME}.stdout", logging.INFO)
    if not isinstance(sys.stderr, StreamToLogger):
        sys.stderr = StreamToLogger(f"{_BASE_LOGGER_NAME}.stderr", logging.ERROR)


def init_logger(config):
    """初始化全局日志。可重复调用，后一次会重建 handler。"""
    global _configured, _listener

    config = config or {}
    level = getattr(logging, str(config.get("level", "INFO")).upper(), logging.INFO)
    output_handlers = _build_handlers(config, level)
    log_queue: queue.Queue = queue.Queue(-1)
    queue_handler = QueueHandler(log_queue)
    queue_handler.setLevel(level)

    if _listener:
        _listener.stop()
    _listener = QueueListener(log_queue, *output_handlers, respect_handler_level=True)
    _listener.start()

    edge_logger = logging.getLogger(_BASE_LOGGER_NAME)
    _replace_handlers(edge_logger, [queue_handler], level)
    edge_logger.propagate = False

    # 兼容少量直接使用 logging.getLogger(__name__) 的模块和第三方库。
    root_logger = logging.getLogger()
    _replace_handlers(root_logger, [queue_handler], level)

    _install_exception_hooks()
    _capture_standard_streams(config)
    atexit.register(_shutdown_logging)

    _configured = True
    base_dir, filename_prefix = _resolve_log_dir(config)
    edge_logger.info(
        "Logger initialized: dir=%s, prefix=%s, level=%s, daily_files=true, monthly_dirs=true",
        base_dir,
        filename_prefix,
        logging.getLevelName(level)
    )
    return edge_logger


def get_logger(name="edge_gateway"):
    """获取统一命名空间下的 logger。"""
    if not name or name == _BASE_LOGGER_NAME:
        return logging.getLogger(_BASE_LOGGER_NAME)
    if name.startswith(f"{_BASE_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_BASE_LOGGER_NAME}.{name}")
