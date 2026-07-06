"""
Pydantic 数据校验模型（用于创建和响应）
所有 UUID 字段均以字符串形式处理，兼容 SQLite 等数据库
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any
from datetime import datetime
import uuid


class MissionCreate(BaseModel):
    name: str
    device_id: str  # 任意合法 UUID 字符串
    status: str = "created"
    execution_conditions: Optional[Dict[str, Any]] = None

    @field_validator('device_id', mode='before')
    @classmethod
    def validate_device_id(cls, v):
        """验证是否为合法 UUID（任意版本），返回字符串"""
        if isinstance(v, uuid.UUID):
            return str(v)
        if isinstance(v, str):
            try:
                uuid.UUID(v)  # 仅校验格式
                return v
            except ValueError:
                raise ValueError(f"Invalid UUID format: {v}")
        raise ValueError("device_id must be a valid UUID string or UUID object")


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