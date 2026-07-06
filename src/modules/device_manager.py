"""
设备管理器 - 管理多个无人机设备
modules/device_manager.py
"""
from typing import Dict, Optional, List
from threading import Lock
import time

from src.modules.base_module import BaseModule
from src.core.message_dispatcher import MAVLinkMessage


class DeviceInfo:
    """设备信息"""

    def __init__(self, sysid: int):
        self.sysid = sysid
        self.compid = 1
        self.last_heartbeat = 0
        self.autopilot_type = None
        self.vehicle_type = None
        self.is_armed = False
        self.current_mode = None
        self.online = False

    def update_heartbeat(self, msg):
        """更新心跳信息"""
        self.last_heartbeat = time.time()
        self.autopilot_type = msg.autopilot
        self.vehicle_type = msg.type
        self.is_armed = bool(msg.base_mode & 128)  # MAV_MODE_FLAG_SAFETY_ARMED
        self.current_mode = msg.custom_mode
        self.online = True


class DeviceManager(BaseModule):
    """设备管理器"""

    def __init__(self):
        super().__init__("DeviceManager")
        self.devices: Dict[int, DeviceInfo] = {}
        self.lock = Lock()
        self.heartbeat_timeout = 5.0  # 5秒无心跳认为离线

    def on_message(self, message: MAVLinkMessage):
        """接收心跳消息"""
        if message.msg_type == 'HEARTBEAT':
            sysid = message.sysid

            with self.lock:
                if sysid not in self.devices:
                    self.devices[sysid] = DeviceInfo(sysid)
                    self.log(f"发现新设备: sysid={sysid}")

                self.devices[sysid].update_heartbeat(message.msg)

    def idle_task(self, dispatcher):
        """检查设备在线状态"""
        current_time = time.time()

        with self.lock:
            for sysid, device in self.devices.items():
                if device.online:
                    elapsed = current_time - device.last_heartbeat
                    if elapsed > self.heartbeat_timeout:
                        device.online = False
                        self.log(f"设备离线: sysid={sysid}", "WARNING")

    def get_device(self, sysid: int) -> Optional[DeviceInfo]:
        """获取设备信息"""
        with self.lock:
            return self.devices.get(sysid)

    def get_all_devices(self) -> List[DeviceInfo]:
        """获取所有设备"""
        with self.lock:
            return list(self.devices.values())

    def get_online_devices(self) -> List[DeviceInfo]:
        """获取在线设备"""
        with self.lock:
            return [d for d in self.devices.values() if d.online]
