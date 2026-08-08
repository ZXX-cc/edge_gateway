"""
多源 RTCM 数据池 —— 热备，仅两种切换方式。

切换规则:
  1. 自动切换: 活跃源断开 / 15s 无数据 → 切到 priority 最高的健康源
  2. 手动切换: switch_to(name) → 用户主动切到指定源

不会自动切回高优先级源——切换后停在当前源，直到断线或手动切。

使用方式::

    from src.modules.rtk_forwarder import RtkSourcePool, TcpTransport, NtripTransport

    pool = RtkSourcePool(inject=my_inject_func)

    pool.add("F9P-1", TcpTransport("192.168.1.13", 8600), priority=10)
    pool.add("NTRIP", NtripTransport(
        host="gnss.ytcors.cn", port=8002,
        mountpoint="RTCM33GRCEJpro",
        username="xxx", password="xxx",
        gga_provider=lambda: gen_gga(lat, lon, alt),
    ), priority=8)

    pool.start()    # 默认第一个连上的源为 active
    pool.switch_to("NTRIP")  # 手动切到 NTRIP，不会自动切回 F9P-1
    pool.stop()
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .rtcm3 import Rtcm3Parser
from .transport import RtkTransport

logger = logging.getLogger(__name__)

# 健康检查间隔 (秒)
HEALTH_CHECK_INTERVAL = 1.0
# 读取超时 (秒) — 超时后输出警告但不视为断开
READ_TIMEOUT = 10.0
# 无数据超过此时间视为 stale (秒)
STALE_THRESHOLD = 15.0
# 重连退避上限 (秒)
MAX_RECONNECT_DELAY = 30.0


# ---------------------------------------------------------------------------
# 源状态
# ---------------------------------------------------------------------------

@dataclass
class SourceHealth:
    """单个数据源的健康状态。"""

    connected: bool = False
    """传输层是否连接"""

    last_frame_time: float = 0.0
    """最后一次收到帧的时间戳 (monotonic)"""

    frames_received: int = 0
    """累计接收帧数"""

    bytes_received: int = 0
    """累计接收字节数"""

    crc_fail: int = 0
    """CRC 校验失败帧数"""

    reconnect_count: int = 0
    """累计重连次数"""

    last_error: str = ""
    """最后一次错误描述"""

    @property
    def stale_seconds(self) -> float:
        """距最后一次收帧的秒数。"""
        if self.last_frame_time == 0:
            return float("inf")
        return time.monotonic() - self.last_frame_time

    @property
    def is_fresh(self) -> bool:
        """最近 STALE_THRESHOLD 秒内是否收到过数据。

        刚连接还未收帧时也视为 fresh（给予 5s 宽限期）。
        """
        if not self.connected:
            return False
        if self.last_frame_time == 0:
            return True  # 刚连接，给宽限期
        return (time.monotonic() - self.last_frame_time) < STALE_THRESHOLD

    @property
    def status(self) -> str:
        """单字状态: healthy / stale / disconnected / error。"""
        if not self.connected:
            return "disconnected"
        if self.last_error:
            return "error"
        if not self.is_fresh:
            return "stale"
        return "healthy"


# ---------------------------------------------------------------------------
# 源池
# ---------------------------------------------------------------------------

class RtkSourcePool:
    """多源 RTCM 数据池 —— 热备 + 断线切换 + 直接注入。

    切换规则:
      1. 自动: 活跃源断开或 15s 无数据 → 切到 priority 最高的健康源
      2. 手动: switch_to(name) → 切到指定源

    所有源共用一个 daemon 线程的 asyncio event loop。
    活跃源的帧直接回调 inject(data) 注入 MAVLink，无队列延迟。
    切换只需要改一个字符串引用 (< 1μs)。
    """

    def __init__(
        self,
        inject: Callable[[bytes], None],
    ):
        """
        Args:
            inject: 帧注入回调。接收 CRC 校验通过的完整 RTCM 帧 bytes。
                    在 daemon 线程中被调用，必须线程安全。
        """
        self._inject = inject
        self._sources: dict[str, _SourceEntry] = {}
        self._active: str = ""          # 当前活跃源名 (str 赋值在 CPython 中原子)
        self._preferred_active: str = "auto"
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        # 外部可设置的回调
        self.on_switch: Optional[Callable[[str, str], None]] = None
        """源切换回调 (old_name, new_name)。"""

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def add(
        self,
        name: str,
        transport: RtkTransport,
        *,
        priority: int = 0,
    ) -> None:
        """注册一个数据源。必须在 start() 之前调用。

        Args:
            name: 源名称 (唯一标识)。
            transport: 传输层实例。
            priority: 优先级 (越大越优先)。同优先级选先注册的。
        """
        if name in self._sources:
            raise ValueError(f"源名称重复: {name}")
        if self._running:
            raise RuntimeError("start() 之后不能添加新源")

        self._sources[name] = _SourceEntry(
            name=name,
            transport=transport,
            priority=priority,
            health=SourceHealth(),
        )
        logger.info("已注册数据源: %s priority=%d (%s)", name, priority, transport.description)

    def set_preferred_active(self, name: str) -> None:
        """设置首选活跃源；auto 表示按 priority 自动选择。"""
        self._preferred_active = str(name or "auto")

    def start(self) -> None:
        """启动所有数据源。

        在 daemon 线程中创建 event loop，并发连接所有源并开始接收。
        调用后立即返回，不会阻塞。
        """
        if not self._sources:
            raise RuntimeError("至少需要注册一个数据源")
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="rtk-pool",
            daemon=True,
        )
        self._thread.start()
        logger.info("RtkSourcePool 已启动 (%d 个源)", len(self._sources))

    def stop(self) -> None:
        """停止所有源并释放资源。"""
        self._running = False
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._thread = None
        self._loop = None
        logger.info("RtkSourcePool 已停止")

    def switch_to(self, name: str) -> bool:
        """手动切换到指定源。

        Args:
            name: 目标源名称。

        Returns:
            成功返回 True，源不存在或不健康返回 False。
        """
        if name not in self._sources:
            logger.warning("切换失败: 源 '%s' 不存在", name)
            return False

        entry = self._sources[name]
        if not entry.health.connected:
            logger.warning("切换失败: 源 '%s' 未连接", name)
            return False

        old = self._active
        self._active = name
        entry.health.last_error = ""
        logger.info("手动切换: %s → %s", old, name)
        if self.on_switch:
            self._on_switch_safe(old, name)
        return True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @property
    def active(self) -> str:
        """当前活跃源名。"""
        return self._active

    @property
    def active_description(self) -> str:
        """当前活跃源的描述。"""
        if self._active and self._active in self._sources:
            return self._sources[self._active].transport.description
        return "(无)"

    def health(self, name: Optional[str] = None) -> dict:
        """查询健康状态。

        Args:
            name: 源名称。None 则返回所有源的汇总。

        Returns:
            {"F9P-1": "healthy", "F9P-2": "stale", ...} 或单个源详情。
        """
        if name:
            entry = self._sources.get(name)
            if entry is None:
                return {"error": f"未知源 '{name}'"}
            return _health_dict(name, entry)

        return {n: _health_dict(n, e) for n, e in self._sources.items()}

    @property
    def source_names(self) -> list[str]:
        return list(self._sources.keys())

    def config(self) -> dict:
        """返回完整配置 + 状态，供前端一次性渲染。

        Returns:
            {
                "active": "NTRIP",
                "sources": [
                    {
                        "name": "NTRIP",
                        "priority": 10,
                        "config": {"type": "ntrip", "host": "...", "port": 8002, ...},
                        "health": {"status": "healthy", "connected": True, ...},
                    },
                    ...
                ],
            }
        """
        return {
            "active": self._active,
            "sources": [
                {
                    "name": name,
                    "priority": entry.priority,
                    "config": entry.transport.config,
                    "health": _health_dict(name, entry),
                }
                for name, entry in self._sources.items()
            ],
        }

    def reconfigure(self, name: str, transport: RtkTransport, *, priority: int = 0) -> bool:
        """运行时热更新指定源的传输层配置。

        旧的 transport 会被断开。新的 transport 在后台自动连接。

        Args:
            name: 要更新的源名称（必须已注册）。
            transport: 新的传输层实例。
            priority: 新优先级（0 表示保持不变）。

        Returns:
            True 如果源存在且更新成功。
        """
        if name not in self._sources:
            logger.warning("reconfigure 失败: 源 '%s' 不存在", name)
            return False
        if not self._running:
            logger.warning("reconfigure 失败: pool 未启动")
            return False

        old_entry = self._sources[name]
        old_transport = old_entry.transport

        # 断开旧连接
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._safe_disconnect(old_transport), self._loop
            )

        # 替换
        new_priority = priority if priority != 0 else old_entry.priority
        new_entry = _SourceEntry(
            name=name,
            transport=transport,
            priority=new_priority,
            health=SourceHealth(),
        )
        self._sources[name] = new_entry

        # 启动新协程
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._receive_source(name, new_entry), self._loop
            )

        logger.info(
            "已热更新源 '%s': %s → %s",
            name,
            old_transport.description,
            transport.description,
        )
        return True

    # ------------------------------------------------------------------
    # 内部: event loop 线程
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Daemon 线程入口 —— 创建 event loop 并运行 gather。"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # 收集所有源 + 健康检查协程
        coros = [
            self._receive_source(name, entry)
            for name, entry in self._sources.items()
        ]
        coros.append(self._health_monitor())

        try:
            self._loop.run_until_complete(asyncio.gather(*coros))
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Pool event loop 异常退出")
        finally:
            # 断开所有源
            for entry in self._sources.values():
                self._loop.run_until_complete(
                    self._safe_disconnect(entry.transport)
                )
            self._loop.close()

    # ------------------------------------------------------------------
    # 内部: 单源接收协程
    # ------------------------------------------------------------------

    async def _receive_source(self, name: str, entry: "_SourceEntry") -> None:
        """单个源的接收协程 —— 连接 → 读字节 → 解析 → 注入 → 断线重连。"""
        transport = entry.transport
        parser = Rtcm3Parser(validate_crc=True)
        reconnect_delay = 1.0
        h = entry.health

        while self._running:
            # ---- 连接 ----
            try:
                await transport.connect()
                await transport.on_connected()
                h.connected = True
                h.last_error = ""
                reconnect_delay = 1.0
                parser.reset()
                logger.info("[%s] 已连接", name)

                self._select_active_after_connect(name, entry)

            except (OSError, asyncio.TimeoutError, ConnectionError) as e:
                h.connected = False
                h.last_error = str(e)
                h.reconnect_count += 1
                logger.warning("[%s] 连接失败: %s (%.1fs 后重试)", name, e, reconnect_delay)

                if not self._running:
                    return
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)
                continue

            # ---- 接收循环 ----
            try:
                async for frame in self._read_frames(name, transport, parser):
                    h.last_frame_time = time.monotonic()
                    h.frames_received += 1
                    h.bytes_received += frame.total_length

                    # 只有活跃源的帧才注入
                    if self._active == name:
                        try:
                            self._inject(frame.raw)
                        except Exception:
                            logger.exception("[%s] inject 回调异常", name)

            except (OSError, asyncio.TimeoutError, ConnectionError) as e:
                h.last_error = str(e)
                logger.warning("[%s] 连接断开: %s", name, e)

            finally:
                h.connected = False
                await self._safe_disconnect(transport)

            # ---- 活跃源断开 → 立即切换 ----
            if self._active == name:
                old = name
                self._select_best_standby()
                if self._active != old:
                    logger.warning("[%s] 活跃源断开，自动切换: %s → %s",
                                   name, old, self._active)
                else:
                    logger.error("[%s] 活跃源断开且无可用备用源！", name)

            # ---- 后台重连 ----
            if self._running:
                logger.info("[%s] %.1fs 后重连...", name, reconnect_delay)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)

    async def _read_frames(self, name, transport, parser):
        """从 transport 读取字节，产出 CRC 校验通过的帧。"""
        while self._running:
            try:
                data = await asyncio.wait_for(
                    transport.read(), timeout=READ_TIMEOUT
                )
            except asyncio.TimeoutError:
                # 正常超时 —— 触发 periodic 钩子 (GGA 刷新等)
                await transport.periodic()
                continue

            if not data:
                # EOF —— 远端关闭
                raise ConnectionError("远端关闭连接 (EOF)")

            # 解析帧
            for frame in parser.feed(data):
                yield frame

            # 定期维护
            await transport.periodic()

    # ------------------------------------------------------------------
    # 内部: 健康监控 & 自动切换
    # ------------------------------------------------------------------

    async def _health_monitor(self) -> None:
        """定期检查所有源的健康状态，必要时自动切换。"""
        while self._running:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

            active_entry = self._sources.get(self._active)
            if active_entry is None:
                continue

            h = active_entry.health

            # 活跃源异常 → 切换
            if not h.connected:
                # 已由 _receive_source 处理
                continue

            if not h.is_fresh and h.last_frame_time > 0:
                # 只有"曾经收到过帧但中断了"才触发切换
                # last_frame_time==0 表示刚连接还没收帧，给它时间
                logger.warning("[%s] 活跃源 %s 秒无数据，尝试切换...", self._active, h.stale_seconds)
                self._select_best_standby()

    def _select_best_standby(self) -> None:
        """选择最佳备用源并设为 active (不改变 active 则不调用)。"""
        best = self._best_healthy()
        if best and best != self._active:
            old = self._active
            self._active = best
            if self.on_switch:
                self._on_switch_safe(old, best)

    def _select_active_after_connect(self, name: str, entry: "_SourceEntry") -> None:
        """连接成功后选择 active 源，支持指定源和 auto priority。"""
        preferred = self._preferred_active
        if preferred and preferred != "auto":
            if name == preferred:
                old = self._active
                self._active = name
                logger.info("[%s] 按配置选为 active", name)
                if old and old != name and self.on_switch:
                    self._on_switch_safe(old, name)
            elif not self._active:
                self._active = name
                logger.info("[%s] 临时选为 active，等待首选源 %s", name, preferred)
            return

        active_entry = self._sources.get(self._active)
        if not active_entry or entry.priority > active_entry.priority:
            old = self._active
            self._active = name
            logger.info("[%s] 按 priority 选为 active: %s -> %s", name, old or "(无)", name)
            if old and old != name and self.on_switch:
                self._on_switch_safe(old, name)

    def _remove_source(self, name: str) -> None:
        """移除一个源 (update_pool_from_config 内部使用)。"""
        if name not in self._sources:
            return
        entry = self._sources.pop(name)
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._safe_disconnect(entry.transport), self._loop
            )
        logger.info("已移除源 '%s'", name)

    def _add_running(self, name: str, transport: RtkTransport, *, priority: int = 0) -> None:
        """运行时新增一个源 (update_pool_from_config 内部使用)。"""
        if name in self._sources:
            return
        entry = _SourceEntry(
            name=name,
            transport=transport,
            priority=priority,
            health=SourceHealth(),
        )
        self._sources[name] = entry
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._receive_source(name, entry), self._loop
            )
        logger.info("已新增源 '%s' priority=%d (%s)", name, priority, transport.description)

    def _best_healthy(self) -> Optional[str]:
        """选出优先级最高且健康的源。"""
        candidates = []
        for name, entry in self._sources.items():
            if name == self._active:
                continue  # 不和自己比
            h = entry.health
            if h.connected and h.is_fresh:
                candidates.append((entry.priority, name))

        if not candidates:
            # 没有完全健康的 → 退而求其次，选 connected 的
            for name, entry in self._sources.items():
                if name == self._active:
                    continue
                if entry.health.connected:
                    candidates.append((entry.priority - 100, name))  # 大幅降权

        if not candidates:
            return None

        # 按 priority 降序，同 priority 按注册顺序
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]

    # ------------------------------------------------------------------
    # 内部: 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    async def _safe_disconnect(transport: RtkTransport) -> None:
        """安全断开 transport，吞掉异常。"""
        try:
            await transport.disconnect()
        except Exception:
            pass

    def _on_switch_safe(self, old: str, new: str) -> None:
        """安全调用 on_switch 回调，吞掉异常。"""
        try:
            self.on_switch(old, new)
        except Exception:
            logger.exception("on_switch 回调异常")


# ---------------------------------------------------------------------------
# 内部结构
# ---------------------------------------------------------------------------

@dataclass
class _SourceEntry:
    """池中单个源的内部记录。"""
    name: str
    transport: RtkTransport
    priority: int
    health: SourceHealth


def _health_dict(name: str, entry: _SourceEntry) -> dict:
    """将源状态序列化为字典。"""
    h = entry.health
    return {
        "name": name,
        "priority": entry.priority,
        "transport": entry.transport.description,
        "status": h.status,
        "connected": h.connected,
        "frames_received": h.frames_received,
        "bytes_received": h.bytes_received,
        "crc_fail": h.crc_fail,
        "stale_seconds": round(h.stale_seconds, 1) if h.last_frame_time else None,
        "reconnect_count": h.reconnect_count,
        "last_error": h.last_error or None,
    }
