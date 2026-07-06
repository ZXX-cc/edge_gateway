"""
大疆上云API (DJI Cloud API) 接口模块

本模块提供DJI Cloud API规范的完整接口实现，参考DJI Dock设备端实现。
接口涵盖：
- 设备管理（绑定、OTA、HMS、日志）
- 机场控制（舱盖、充电、推杆、灯光等）
- 飞行任务（航线、作业、飞行控制）
- 负载控制（相机、云台、第三方负载）
- 媒体管理（文件、直播、存储）
- DRC实时控制（速度、航向、高度、指点飞行）

通信协议：MQTT (thing/product/{sn}/services, thing/product/{sn}/events等)
"""

from src.cloud_api.dock_manager import DockManager
from src.cloud_api.connection_manager import CloudConnectionManager
from src.cloud_api.telemetry_reporter import TelemetryReporter
from src.cloud_api.capability_reporter import CapabilityReporter

__all__ = [
    'DockManager',
    'CloudConnectionManager',
    'TelemetryReporter',
    'CapabilityReporter'
]
