"""
RTCM 传输层抽象 —— 统一不同数据源的字节流获取。

不关心上层如何解析/校验/切换，只负责:
  - connect()   : 建立传输连接
  - read()      : 读取原始字节
  - disconnect(): 关闭连接

新增数据源只需实现这个接口，无需改动任何上层代码。

支持:
  - TcpTransport   : F9P 直连 TCP
  - NtripTransport : NTRIP 网络 RTK (HTTP 鉴权 + GGA)
  - 未来: 串口 / UDP / 蓝牙 / 文件回放 ...
"""

from __future__ import annotations

import asyncio
import base64
import logging
import socket
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# NTRIP GGA 刷新间隔 (秒)
NTRIP_GGA_INTERVAL = 30.0


# ---------------------------------------------------------------------------
# 抽象传输接口
# ---------------------------------------------------------------------------

class RtkTransport(ABC):
    """RTCM 数据源传输层抽象。

    子类只需实现 4 个方法，即可接入统一的数据管道。
    """

    @abstractmethod
    async def connect(self) -> None:
        """建立连接。失败时抛出 OSError 或 asyncio.TimeoutError。"""
        ...

    @abstractmethod
    async def read(self) -> bytes:
        """读取一批原始字节。返回空 bytes 表示 EOF (连接关闭)。"""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """关闭连接。幂等，可重复调用。"""
        ...

    @property
    @abstractmethod
    def connected(self) -> bool:
        """当前是否已连接。"""
        ...

    # ---- 可选钩子 ----

    async def on_connected(self) -> None:
        """连接成功后调用一次 (发送初始 GGA 等)。"""

    async def periodic(self) -> None:
        """每次 read() 之后调用 (定期刷新 GGA 等)。"""

    @property
    def description(self) -> str:
        """人类可读的描述，用于日志。"""
        return type(self).__name__

    @property
    def config(self) -> dict:
        """返回传输层配置信息，供前端展示。子类应覆盖。"""
        return {"type": type(self).__name__}


# ---------------------------------------------------------------------------
# TCP 直连 (F9P 基准站)
# ---------------------------------------------------------------------------

class TcpTransport(RtkTransport):
    """直连 ZED-F9P 串转网模块的 TCP 传输。

    Example:
        t = TcpTransport("192.168.1.13", 8600)
        await t.connect()
        data = await t.read()
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float = 10.0,
        read_buffer_size: int = 4096,
    ):
        self._host = host
        self._port = port
        self._connect_timeout = connect_timeout
        self._read_buffer_size = read_buffer_size
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    @property
    def description(self) -> str:
        return f"TCP({self._host}:{self._port})"

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def config(self) -> dict:
        return {
            "type": "tcp",
            "host": self._host,
            "port": self._port,
        }

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(host=self._host, port=self._port),
            timeout=self._connect_timeout,
        )
        # 禁用 Nagle —— RTCM 数据对延迟敏感
        sock = self._writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        logger.info("TCP 已连接 %s:%d", self._host, self._port)

    async def read(self) -> bytes:
        if self._reader is None:
            raise ConnectionError("TCP 未连接")
        return await self._reader.read(self._read_buffer_size)

    async def disconnect(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await asyncio.wait_for(self._writer.wait_closed(), timeout=2.0)
            except (asyncio.TimeoutError, OSError):
                pass
            finally:
                self._reader = None
                self._writer = None
        logger.debug("TCP 已断开 %s:%d", self._host, self._port)

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()


# ---------------------------------------------------------------------------
# NTRIP 网络 RTK
# ---------------------------------------------------------------------------

class NtripTransport(RtkTransport):
    """NTRIP 网络 RTK 传输 —— 通过 HTTP 鉴权连接 CORS/千寻等服务。

    连接过程:
      1. TCP 连接到 caster
      2. 发送 HTTP GET 请求 (Basic Auth + mountpoint)
      3. 逐字节读取响应，等待 "200 OK" 或 "ICY 200 OK"
      4. 发送初始 GGA 启动差分数据流
      5. 定期 (30s) 刷新 GGA 维持数据流

    Example:
        t = NtripTransport(
            host="gnss.ytcors.cn", port=8002,
            mountpoint="RTCM33GRCEJpro",
            username="xxx", password="xxx",
            gga_provider=lambda: generate_gga(lat, lon, alt),
        )
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        mountpoint: str,
        username: str,
        password: str,
        gga_provider: Optional[Callable[[], str]] = None,
        connect_timeout: float = 15.0,
        read_buffer_size: int = 4096,
    ):
        self._host = host
        self._port = port
        self._mountpoint = mountpoint
        self._username = username
        self._password = password
        self._gga_provider = gga_provider
        self._connect_timeout = connect_timeout
        self._read_buffer_size = read_buffer_size

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._last_gga_time: float = 0.0

    @property
    def description(self) -> str:
        return f"NTRIP({self._host}:{self._port}/{self._mountpoint})"

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def mountpoint(self) -> str:
        return self._mountpoint

    @property
    def username(self) -> str:
        return self._username

    @property
    def config(self) -> dict:
        return {
            "type": "ntrip",
            "host": self._host,
            "port": self._port,
            "mountpoint": self._mountpoint,
            "username": self._username,
            "gga_interval": NTRIP_GGA_INTERVAL,
        }

    # ---- 连接 ----

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(host=self._host, port=self._port),
            timeout=self._connect_timeout,
        )

        # 构造 NTRIP HTTP 请求
        auth = f"{self._username}:{self._password}"
        encoded = base64.b64encode(auth.encode()).decode()
        request = (
            f"GET /{self._mountpoint} HTTP/1.1\r\n"
            f"Host: {self._host}\r\n"
            f"User-Agent: NTRIP GNSSInternetRadio/1.4.10\r\n"
            f"Authorization: Basic {encoded}\r\n"
            f"Accept: */*\r\n"
            f"Connection: close\r\n"
            "\r\n"
        )
        self._writer.write(request.encode())
        await self._writer.drain()

        # 逐字节读取 HTTP 响应，等待鉴权成功标志
        response = b""
        max_header = 4096
        while True:
            if len(response) > max_header:
                raise ConnectionError("NTRIP HTTP 响应头过大")

            try:
                chunk = await asyncio.wait_for(self._reader.read(1), timeout=15.0)
            except asyncio.TimeoutError:
                raise ConnectionError("NTRIP 服务器响应超时 (15s)")

            if not chunk:
                raise ConnectionError("NTRIP 服务器未响应就断开")

            response += chunk
            text = response.decode("utf-8", errors="ignore")
            if "ICY 200 OK" in text or "200 OK" in text:
                break

        # 打印服务器响应头
        text = response.decode("utf-8", errors="ignore")
        header_part = text.split("\r\n\r\n")[0] if "\r\n\r\n" in text else text[:200]
        logger.info("NTRIP 鉴权成功 %s:%d/%s", self._host, self._port, self._mountpoint)
        logger.debug("NTRIP 响应头:\n%s", header_part)

    async def on_connected(self) -> None:
        """发送初始 GGA 启动差分数据流。"""
        await self._send_gga()

    async def periodic(self) -> None:
        """每 30s 刷新 GGA。"""
        now = time.monotonic()
        if now - self._last_gga_time >= NTRIP_GGA_INTERVAL:
            await self._send_gga()

    async def _send_gga(self) -> None:
        """发送 GGA 语句到 NTRIP caster。"""
        if self._gga_provider is None:
            return
        if self._writer is None or self._writer.is_closing():
            return

        try:
            gga = self._gga_provider()
            gga_line = gga if gga.endswith("\r\n") else gga + "\r\n"
            self._writer.write(gga_line.encode())
            await self._writer.drain()
            self._last_gga_time = time.monotonic()
            logger.debug("NTRIP GGA 已发送: %s", gga_line.strip())
        except (OSError, asyncio.TimeoutError) as e:
            logger.warning("NTRIP GGA 发送失败: %s", e)

    # ---- 数据读取 ----

    async def read(self) -> bytes:
        if self._reader is None:
            raise ConnectionError("NTRIP 未连接")
        return await self._reader.read(self._read_buffer_size)

    # ---- 断开 ----

    async def disconnect(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await asyncio.wait_for(self._writer.wait_closed(), timeout=2.0)
            except (asyncio.TimeoutError, OSError):
                pass
            finally:
                self._reader = None
                self._writer = None
        logger.debug("NTRIP 已断开 %s:%d", self._host, self._port)

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()
