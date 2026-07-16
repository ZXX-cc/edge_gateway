"""
Pydantic 数据校验模型（用于创建和响应）
主键 UUID 字段以字符串形式处理，兼容 SQLite/MongoDB 等数据库。
业务侧 device_id 允许使用网关 SN；不能强制按 UUID 校验。
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any
from datetime import datetime
import uuid


class MissionCreate(BaseModel):
    name: str
    device_id: str  # 网关 SN 或设备 UUID 字符串
    status: str = "created"
    execution_conditions: Optional[Dict[str, Any]] = None

    @field_validator('device_id', mode='before')
    @classmethod
    def validate_device_id(cls, v):
        """接受网关 SN 或 UUID 字符串，返回非空字符串。"""
        if isinstance(v, uuid.UUID):
            return str(v)
        if isinstance(v, str):
            value = v.strip()
            if value:
                return value
        raise ValueError("device_id must be a non-empty gateway SN or UUID string")


class MissionResponse(BaseModel):
    id: str  # 存储为字符串
    name: str
    device_id: str
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None  # 若表中无 updated_at 可忽略


class WaypointCreate(BaseModel):
    mission_id: str  # 任务主键字符串
    sequence: int
    command: int
    frame: int
    latitude: float
    longitude: float
    altitude: float
    param1: float = 0.0
    param2: float = 0.0
    param3: float = 0.0
    param4: float = 0.0
    autocontinue: int = 1
    action_type: str = "waypoint"
    flight_speed: Optional[float] = None
    photo_interval: Optional[float] = None


class WaypointResponse(BaseModel):
    id: int
    mission_id: str
    sequence: int
    latitude: float
    longitude: float
    altitude: float
