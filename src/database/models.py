import time
import uuid
import gzip
import json
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, JSON, ForeignKey, DateTime, LargeBinary
from sqlalchemy.orm import relationship
from .connection import Base


def compress_json(data):
    """压缩 JSON 数据"""
    if data is None:
        return None
    json_str = json.dumps(data, separators=(',', ':'))
    return gzip.compress(json_str.encode('utf-8'))


def decompress_json(compressed_data):
    """解压 JSON 数据"""
    if compressed_data is None:
        return None
    json_str = gzip.decompress(compressed_data).decode('utf-8')
    return json.loads(json_str)


class Mission(Base):
    __tablename__ = "missions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    device_id = Column(String(36), nullable=False)
    status = Column(String(50), default="uploaded")
    execution_conditions = Column(JSON, nullable=True)
    created_at = Column(Integer, default=lambda: int(time.time()))

    waypoints = relationship("Waypoint", back_populates="mission", cascade="all, delete-orphan")
    progress = relationship("MissionProgress", back_populates="mission", cascade="all, delete-orphan", uselist=False)

    def __repr__(self):
        return f"<Mission(id={self.id}, name={self.name}, status={self.status})>"


class Waypoint(Base):
    __tablename__ = "waypoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mission_id = Column(String(36), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False)
    sequence = Column(Integer, nullable=False)
    command = Column(Integer, nullable=False)
    frame = Column(Integer, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    altitude = Column(Float, nullable=False)
    param1 = Column(Float, default=0.0)
    param2 = Column(Float, default=0.0)
    param3 = Column(Float, default=0.0)
    param4 = Column(Float, default=0.0)
    autocontinue = Column(Integer, default=1)
    action_type = Column(String(50), default="waypoint")
    flight_speed = Column(Float, nullable=True)
    photo_interval = Column(Float, nullable=True)

    mission = relationship("Mission", back_populates="waypoints")

    def __repr__(self):
        return f"<Waypoint(id={self.id}, seq={self.sequence}, lat={self.latitude}, lon={self.longitude})>"


class MissionProgress(Base):
    __tablename__ = "mission_progress"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    mission_id = Column(String(36), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False)
    current_waypoint_seq = Column(Integer, nullable=False)
    last_update = Column(DateTime, default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())

    mission = relationship("Mission", back_populates="progress")

    def __repr__(self):
        return f"<MissionProgress(mission={self.mission_id}, seq={self.current_waypoint_seq})>"


class MissionPlatformSnapshot(Base):
    __tablename__ = "mission_platform_snapshot"

    gateway_sn = Column(String(64), primary_key=True)
    method = Column(String(128), nullable=False)
    flight_id = Column(String(255), default="")
    source = Column(String(64), default="")
    payload = Column(JSON, nullable=False)
    created_at = Column(Integer, default=lambda: int(time.time() * 1000))
    updated_at = Column(Integer, default=lambda: int(time.time() * 1000))

    def __repr__(self):
        return (
            f"<MissionPlatformSnapshot(gateway={self.gateway_sn}, "
            f"method={self.method}, flight_id={self.flight_id})>"
        )


class DeviceState(Base):
    __tablename__ = "device_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gateway_sn = Column(String(64), nullable=False, unique=True)
    cover_state = Column(Integer, default=0)
    putter_state = Column(Integer, default=0)
    charging_state = Column(Integer, default=0)
    drone_in_dock = Column(Integer, default=1)
    mode_code = Column(Integer, default=0)
    emergency_stop_state = Column(Integer, default=0)
    battery_maintenance_mode = Column(Integer, default=0)
    air_conditioner_mode = Column(Integer, default=0)
    alarm_state = Column(Integer, default=0)
    supplement_light_state = Column(Integer, default=0)
    last_update = Column(DateTime, default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())

    def __repr__(self):
        return f"<DeviceState(gateway={self.gateway_sn}, cover={self.cover_state}, charging={self.charging_state})>"


class Alarm(Base):
    __tablename__ = "alarms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gateway_sn = Column(String(64), nullable=False)
    alarm_id = Column(String(64), nullable=False, unique=True)
    alarm_level = Column(Integer, nullable=False)
    alarm_content = Column(String(512), nullable=False)
    alarm_type = Column(String(64), default="hms")
    is_read = Column(Integer, default=0)
    create_time = Column(Integer, default=lambda: int(time.time()))
    extra_info = Column(JSON, nullable=True)

    def __repr__(self):
        return f"<Alarm(id={self.alarm_id}, level={self.alarm_level}, content={self.alarm_content[:20]})>"


class DeviceBinding(Base):
    __tablename__ = "device_binding"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gateway_sn = Column(String(64), nullable=False, unique=True)
    workspace_id = Column(String(64), nullable=True)
    workspace_name = Column(String(128), nullable=True)
    organization_id = Column(String(64), nullable=True)
    organization_name = Column(String(128), nullable=True)
    is_bound = Column(Integer, default=0)
    bind_time = Column(Integer, default=0)
    last_heartbeat = Column(Integer, default=0)

    def __repr__(self):
        return f"<DeviceBinding(gateway={self.gateway_sn}, workspace={self.workspace_id}, bound={self.is_bound})>"


class TelemetryData(Base):
    __tablename__ = "telemetry_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_sn = Column(String(64), nullable=False)
    timestamp = Column(Integer, nullable=False)
    latitude = Column(Float)
    longitude = Column(Float)
    altitude = Column(Float)
    height = Column(Float)
    horizontal_speed = Column(Float)
    vertical_speed = Column(Float)
    attitude_pitch = Column(Float)
    attitude_roll = Column(Float)
    attitude_head = Column(Float)
    battery_capacity = Column(Integer)
    battery_voltage = Column(Float)
    battery_current = Column(Float)
    battery_temperature = Column(Float)
    gps_number = Column(Integer)
    gps_fixed = Column(Integer)
    wind_speed = Column(Float)
    wind_direction = Column(Float)
    mode_code = Column(Integer)
    gear = Column(Integer)
    flight_time = Column(Integer)

    def __repr__(self):
        return f"<TelemetryData(device={self.device_sn}, time={self.timestamp})>"


class OperationRecord(Base):
    __tablename__ = "operation_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_sn = Column(String(64), nullable=False)
    operation_id = Column(String(64), nullable=False, unique=True)
    command = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False)
    progress = Column(Integer, default=0)
    params = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error_message = Column(String(512))
    start_time = Column(Integer, nullable=False)
    end_time = Column(Integer)
    operator = Column(String(64))

    def __repr__(self):
        return f"<OperationRecord(id={self.operation_id}, cmd={self.command}, status={self.status})>"


class MediaFile(Base):
    __tablename__ = "media_files"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    mission_id = Column(String(36), ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    device_sn = Column(String(64), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_size = Column(Integer, nullable=False)
    file_type = Column(String(50), nullable=False)
    resolution = Column(String(50))
    duration = Column(Float)
    capture_time = Column(Integer)
    latitude = Column(Float)
    longitude = Column(Float)
    altitude = Column(Float)
    upload_status = Column(String(32), default="pending")
    upload_url = Column(String(512))
    thumbnail_url = Column(String(512))
    created_at = Column(Integer, default=lambda: int(time.time()))

    mission = relationship("Mission", backref="media_files")

    def __repr__(self):
        return f"<MediaFile(name={self.file_name}, type={self.file_type}, status={self.upload_status})>"
