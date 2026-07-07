"""Non-blocking request trace recorder for MQTT request/reply evidence."""

import atexit
import json
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, Optional

from src.utils.config import Config
from src.utils.logger import get_logger


logger = get_logger("request_trace")


def now_ms() -> int:
    return int(time.time() * 1000)


def _safe_result(payload: Any) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    return result if isinstance(result, int) else None


def _safe_json_value(value: Any, max_chars: int) -> Any:
    if value is None:
        return None
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        raw = str(value)
    if max_chars > 0 and len(raw) > max_chars:
        return {
            "_truncated": True,
            "chars": len(raw),
            "preview": raw[:max_chars],
        }
    try:
        return json.loads(raw)
    except Exception:
        return raw[:max_chars] if max_chars > 0 else raw


class RequestTraceRecorder:
    """Threaded JSONL writer with an in-memory ring buffer."""

    def __init__(self):
        self.enabled = True
        self.base_dir = Path("./logs/request_trace")
        self.filename_prefix = "request_trace"
        self.payload_max_chars = 3000
        self.max_memory = 5000
        self._queue_max = 10000
        self._queue: queue.Queue = queue.Queue(self._queue_max)
        self._memory: Deque[dict] = deque(maxlen=self.max_memory)
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._dropped = 0
        self._configured = False

    def configure_from_config(self):
        if self._configured:
            return
        self.enabled = bool(Config.get("request_trace.enabled", True))
        self.base_dir = Path(Config.get("request_trace.dir", "./logs/request_trace"))
        self.filename_prefix = str(Config.get("request_trace.filename_prefix", "request_trace"))
        self.payload_max_chars = int(Config.get("request_trace.payload_max_chars", 3000))
        self.max_memory = int(Config.get("request_trace.max_memory", 5000))
        self._queue_max = int(Config.get("request_trace.max_queue", 10000))
        self._queue = queue.Queue(self._queue_max)
        self._memory = deque(maxlen=self.max_memory)
        self._configured = True

    def start(self):
        self.configure_from_config()
        if not self.enabled or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._writer_loop, name="RequestTraceWriter", daemon=True)
        self._thread.start()
        atexit.register(self.stop)
        logger.info(
            "RequestTraceRecorder started: dir=%s, prefix=%s, max_memory=%s, max_queue=%s",
            self.base_dir,
            self.filename_prefix,
            self.max_memory,
            self._queue_max,
        )

    def stop(self):
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def record(
        self,
        event: str,
        *,
        direction: str = "",
        topic: str = "",
        method: str = "",
        tid: str = "",
        bid: str = "",
        gateway: str = "",
        need_reply: Any = None,
        result: Optional[int] = None,
        elapsed_ms: Optional[int] = None,
        payload: Any = None,
        payload_bytes: Optional[int] = None,
        error: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.configure_from_config()
        if not self.enabled:
            return

        if isinstance(payload, dict):
            method = method or str(payload.get("method", "") or "")
            tid = tid or str(payload.get("tid", "") or "")
            bid = bid or str(payload.get("bid", "") or "")
            gateway = gateway or str(payload.get("gateway", "") or "")
            if need_reply is None:
                need_reply = payload.get("need_reply")
            if result is None:
                result = _safe_result(payload)

        record = {
            "ts": now_ms(),
            "event": event,
            "direction": direction,
            "topic": topic,
            "method": method,
            "tid": tid,
            "bid": bid,
            "gateway": gateway,
            "need_reply": need_reply,
            "result": result,
            "elapsed_ms": elapsed_ms,
            "payload_bytes": payload_bytes,
            "error": error,
            "payload": _safe_json_value(payload, self.payload_max_chars),
            "extra": extra or {},
        }

        with self._lock:
            self._memory.append(record)
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 100 == 0:
                logger.warning("Request trace queue full, dropped=%s", self._dropped)

    def recent(self, limit: int = 100, *, tid: str = "", method: str = "") -> list[dict]:
        with self._lock:
            records = list(self._memory)
        if tid:
            records = [item for item in records if item.get("tid") == tid]
        if method:
            records = [item for item in records if item.get("method") == method]
        return records[-limit:]

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": self._running,
            "queue_size": self._queue.qsize(),
            "memory_size": len(self._memory),
            "dropped": self._dropped,
            "dir": str(self.base_dir),
        }

    def _writer_loop(self):
        while self._running or not self._queue.empty():
            try:
                record = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._write_record(record)
            except Exception as exc:
                logger.error("Failed to write request trace: %s", exc, exc_info=True)

    def _path_for_record(self, record: dict) -> Path:
        ts = float(record.get("ts") or now_ms()) / 1000.0
        local_time = time.localtime(ts)
        month = time.strftime("%Y-%m", local_time)
        day = time.strftime("%Y-%m-%d", local_time)
        return self.base_dir / month / f"{self.filename_prefix}_{day}.jsonl"

    def _write_record(self, record: dict):
        path = self._path_for_record(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


_recorder = RequestTraceRecorder()


def get_request_trace_recorder() -> RequestTraceRecorder:
    return _recorder


def find_trace_files(base_dir: str = "", days: int = 7) -> Iterable[Path]:
    root = Path(base_dir or Config.get("request_trace.dir", "./logs/request_trace"))
    if not root.exists():
        return []
    files = sorted(root.glob("*/*.jsonl"), reverse=True)
    if days <= 0:
        return files
    cutoff = now_ms() - days * 24 * 3600 * 1000
    return [path for path in files if int(path.stat().st_mtime * 1000) >= cutoff]
