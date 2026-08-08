"""
RTKModule ↔ RtkSourcePool 桥接层。

从命名源配置字典创建 RtkSourcePool。

配置格式:

    config = {
        "active_source": "auto",         # "auto" / 源名
        "sources": {
            "F9P-1": {
                "type": "tcp",
                "host": "192.168.1.21",
                "port": 8600,
                "priority": 10,
            },
            "NTRIP": {
                "type": "ntrip",
                "host": "101.52.241.18",
                "port": 8002,
                "mountpoint": "RTCM33GRCEJpro",
                "username": "ytcors14925",
                "password": "fyx56797",
                "priority": 8,
            },
        },
        "failover": {
            "max_fails": 3,
            "retry_after": 300,
        },
    }
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from .source_pool import RtkSourcePool
from .transport import NtripTransport, TcpTransport

logger = logging.getLogger(__name__)


def create_pool_from_config(
    config: Dict[str, Any],
    inject: Callable[[bytes], None],
    *,
    gga_provider: Optional[Callable[[], str]] = None,
) -> RtkSourcePool:
    """
    从命名源配置字典创建 RtkSourcePool。

    Args:
        config: 配置字典 (见模块文档)。
        inject: RTCM 帧注入回调。
        gga_provider: NTRIP GGA 生成回调。

    Returns:
        已配置好但未启动的 RtkSourcePool。

    Raises:
        ValueError: 配置缺少必要字段或源类型无效。
    """
    sources = config.get("sources", {})
    if not sources:
        raise ValueError("配置缺少 'sources' 字段或为空")

    pool = RtkSourcePool(inject=inject)
    pool.set_preferred_active(config.get("active_source", "auto"))
    pool.on_switch = lambda old, new: logger.info(
        "RTK 数据源切换: %s → %s", old, new
    )

    for name, src_cfg in sources.items():
        transport = _build_transport(name, src_cfg, gga_provider)
        priority = src_cfg.get("priority", 0)
        pool.add(name, transport, priority=priority)

    logger.info(
        "RtkSourcePool 配置完成: %d 个源: %s",
        len(pool.source_names),
        ", ".join(pool.source_names),
    )
    return pool


def _build_transport(
    name: str,
    src_cfg: Dict[str, Any],
    gga_provider: Optional[Callable[[], str]],
) -> TcpTransport | NtripTransport:
    """根据源配置创建对应的 Transport。"""
    src_type = src_cfg.get("type", "")
    host = src_cfg.get("host", "")
    port = src_cfg.get("port", 0)

    if not host or not port:
        raise ValueError(f"源 '{name}' 缺少 host/port")

    if src_type == "tcp":
        return TcpTransport(
            host=host,
            port=port,
            connect_timeout=src_cfg.get("connect_timeout", 10.0),
        )

    elif src_type == "ntrip":
        mountpoint = src_cfg.get("mountpoint", "")
        username = src_cfg.get("username", "")
        password = src_cfg.get("password", "")
        if not mountpoint or not username:
            raise ValueError(f"NTRIP 源 '{name}' 缺少 mountpoint/username")
        return NtripTransport(
            host=host,
            port=port,
            mountpoint=mountpoint,
            username=username,
            password=password,
            gga_provider=gga_provider,
            connect_timeout=src_cfg.get("connect_timeout", 15.0),
        )

    else:
        raise ValueError(f"源 '{name}' 的 type 无效: '{src_type}' (支持 tcp/ntrip)")


def update_pool_from_config(
    pool: RtkSourcePool,
    config: Dict[str, Any],
    *,
    gga_provider: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """
    运行时热更新 pool 配置。

    对比新旧 sources，新增/更新/删除源。

    Args:
        pool: 已启动的 RtkSourcePool。
        config: 新配置字典。
        gga_provider: NTRIP GGA 生成回调。

    Returns:
        {"ok": True/False, "changes": [...], "errors": [...]}
    """
    result: Dict[str, Any] = {"ok": True, "changes": [], "errors": []}
    new_sources = config.get("sources", {})
    pool.set_preferred_active(config.get("active_source", "auto"))

    # ── 删除不在新配置中的源 ──
    for name in list(pool.source_names):
        if name not in new_sources:
            if pool._active == name:
                result["errors"].append(f"不能删除当前活跃源 '{name}'")
                result["ok"] = False
                return result
            pool._remove_source(name)
            result["changes"].append(f"已删除源 '{name}'")

    # ── 新增或更新源 ──
    for name, src_cfg in new_sources.items():
        new_transport = _build_transport(name, src_cfg, gga_provider)
        new_priority = src_cfg.get("priority", 0)

        if name not in pool.source_names:
            # 新增源 (需要 pool 已启动才能热加)
            pool._add_running(name, new_transport, priority=new_priority)
            result["changes"].append(f"新增源 '{name}'")
        else:
            # 更新已有源
            changed = pool.reconfigure(name, new_transport, priority=new_priority)
            if changed:
                result["changes"].append(f"更新源 '{name}'")
            else:
                result["changes"].append(f"源 '{name}' 配置无变化")

    if not result["changes"] and not result["errors"]:
        result["changes"].append("配置无变化")

    return result
