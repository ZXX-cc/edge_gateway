"""
RTK 数据源池最小公共接口。

该包由 RTKF9P 独立参考工程迁入主项目，运行时不依赖 RTKF9P 目录。
"""

from .rtcm3 import (
    FrameStatus,
    KNOWN_MESSAGE_TYPES,
    RTCM3_PREAMBLE,
    Rtcm3Frame,
    Rtcm3Parser,
    crc24q,
    crc24q_verify,
    describe_message_type,
)
from .rtk_pool_bridge import create_pool_from_config, update_pool_from_config
from .source_pool import RtkSourcePool, SourceHealth
from .transport import NtripTransport, RtkTransport, TcpTransport

__all__ = [
    "FrameStatus",
    "KNOWN_MESSAGE_TYPES",
    "RTCM3_PREAMBLE",
    "Rtcm3Frame",
    "Rtcm3Parser",
    "crc24q",
    "crc24q_verify",
    "describe_message_type",
    "create_pool_from_config",
    "update_pool_from_config",
    "RtkSourcePool",
    "SourceHealth",
    "NtripTransport",
    "RtkTransport",
    "TcpTransport",
]
