"""
日志字段中文标注工具。

输出示例：
{"battery(电池)":{"capacity_percent(电量百分比)":100}}
"""
import json
from typing import Any


FIELD_LABELS = {
    # MQTT 标准信封
    "tid": "事务ID",
    "bid": "业务ID",
    "timestamp": "时间戳",
    "gateway": "网关序列号",
    "gateway_sn": "网关序列号",
    "device_sn": "设备序列号",
    "method": "方法",
    "need_reply": "需要回包",
    "data": "数据",
    "result": "结果码",
    "msg": "消息",
    "output": "输出",
    "status": "状态",
    "state": "状态",
    "property": "属性",
    "changes": "变化字段",

    # DRC / 远程控制
    "drc_state": "DRC链路状态",
    "drc_session_id": "DRC会话ID",
    "session_id": "会话ID",
    "restore_mission": "恢复任务",
    "mqtt_broker": "MQTT代理",
    "osd_frequency": "OSD频率",
    "hsi_frequency": "HSI频率",
    "client_id": "客户端ID",
    "username": "用户名",
    "password": "密码",
    "enable_tls": "启用TLS",
    "expire_time": "过期时间",

    # OSD / 飞行遥测
    "longitude": "经度",
    "latitude": "纬度",
    "height": "相对高度",
    "altitude": "海拔高度",
    "horizontal_speed": "水平速度",
    "vertical_speed": "垂直速度",
    "speed": "速度",
    "attitude_pitch": "俯仰角",
    "attitude_roll": "横滚角",
    "attitude_head": "航向角",
    "heading": "航向",
    "battery": "电池",
    "capacity_percent": "电量百分比",
    "battery_percent": "电量百分比",
    "voltage": "电压",
    "current": "电流",
    "temperature": "温度",
    "position_state": "定位状态",
    "gps_number": "GPS星数",
    "satellite_number": "卫星数量",
    "is_fixed": "是否定位",
    "gps_signal_level": "GPS信号等级",
    "mode_code": "模式码",
    "flight_mode": "飞行模式",
    "gear": "档位",
    "armed": "解锁状态",
    "online": "在线状态",
    "flight_time": "飞行时长",
    "wind_speed": "风速",
    "rc_signal_quality": "遥控信号质量",
    "transmission_signal_quality": "图传信号质量",
    "home_longitude": "返航点经度",
    "home_latitude": "返航点纬度",

    # 机场 / 机巢状态
    "dock_status": "机场状态",
    "drone_status": "无人机状态",
    "cover_state": "舱盖状态",
    "putter_state": "推杆状态",
    "charging_state": "充电状态",
    "drone_in_dock": "无人机在舱",
    "environment_temperature": "环境温度",
    "nest_temperature": "舱内温度",
    "rainfall": "降雨",
    "humidity": "湿度",
    "air_conditioner_mode": "空调模式",
    "air_conditioner": "空调",
    "supplement_light_state": "补光灯状态",
    "alarm_state": "告警状态",
    "rtk_state": "RTK状态",
    "simulation_mode": "仿真模式",
    "silent_mode": "静音模式",
    "remaining_power_for_return_home": "返航预留电量",
    "battery_store_mode": "电池存储模式",
    "battery_maintenance_mode": "电池保养模式",
    "user_experience_improvement": "用户体验改进",
    "storage": "存储",
    "total": "总量",
    "used": "已用",
    "remain_storage_capacity": "剩余存储容量",
    "storage_capacity": "存储容量",

    # 任务 / 航线
    "flighttask_step_code": "任务步骤码",
    "flighttask_id": "飞行任务ID",
    "wayline_id": "航线ID",
    "job_id": "作业ID",
    "progress": "进度",
    "reason": "原因",
    "error_code": "错误码",
    "error_msg": "错误消息",

    # 机巢自定义 MAVLink
    "ordinary_order": "普通指令",
    "takeoff_land_order": "起降指令",
    "ordinary_order_response": "普通指令响应",
    "takeoff_land_order_response": "起降指令响应",
    "ac_temp": "空调温度",
    "env_temperature": "环境温度",
    "wind_direction": "风向",
    "atmospheric_pressure": "气压",
    "charge_volt": "充电电压",
    "charge_current": "充电电流",
    "clamp_state": "夹持状态",
    "gate_state": "舱门状态",
    "landing_light_state": "降落灯状态",
}


SENSITIVE_KEYWORDS = (
    "password",
    "passwd",
    "secret",
    "token",
    "access_key",
    "private_key",
    "credential",
)


def label_key(key: Any) -> str:
    key_str = str(key)
    label = FIELD_LABELS.get(key_str)
    return f"{key_str}({label})" if label else key_str


def _is_sensitive_key(key: Any) -> bool:
    key_lower = str(key).lower()
    return any(keyword in key_lower for keyword in SENSITIVE_KEYWORDS)


def annotate_fields(value: Any, max_depth: int = 6, _depth: int = 0) -> Any:
    """递归标注 dict key，保留原始 value。"""
    if _depth >= max_depth:
        return "<max-depth>"

    if isinstance(value, dict):
        annotated = {}
        for key, item in value.items():
            annotated_key = label_key(key)
            if _is_sensitive_key(key):
                annotated[annotated_key] = "***"
            else:
                annotated[annotated_key] = annotate_fields(item, max_depth=max_depth, _depth=_depth + 1)
        return annotated

    if isinstance(value, list):
        return [annotate_fields(item, max_depth=max_depth, _depth=_depth + 1) for item in value]

    return value


def format_with_labels(value: Any, max_length: int = 3000) -> str:
    """格式化为紧凑 JSON 字符串，key 中附带中文名。"""
    try:
        text = json.dumps(
            annotate_fields(value),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except Exception:
        text = str(value)

    if max_length > 0 and len(text) > max_length:
        return text[:max_length] + "...<truncated>"
    return text
