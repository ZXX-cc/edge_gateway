"""
消息格式模块 - MQTT消息构造与解析
封装上云API标准消息格式
"""
import json
import uuid
import time
from typing import Optional, Dict, Any

from src.utils.dji_error_codes import describe_error, normalize_error_code


class MessageBuilder:
    """标准消息构造器"""

    @staticmethod
    def build(method: str, data: dict = None, gateway_sn: str = "",
              need_reply: bool = True, tid: str = None) -> dict:
        """
        构造标准MQTT消息

        Args:
            method: API方法名
            data: 业务数据载荷
            gateway_sn: 网关序列号
            need_reply: 是否需要响应
            tid: 事务ID(自动生成的UUID)
        """
        msg = {
            "bid": str(uuid.uuid4()),
            "data": data or {},
            "gateway": gateway_sn,
            "tid": tid or str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "method": method
        }
        if need_reply:
            msg["need_reply"] = 1
        return msg

    @staticmethod
    def build_reply(original_msg: dict, result: int = 0, data: dict = None, msg_str: str = "") -> dict:
        """
        构造响应消息

        Args:
            original_msg: 原始请求消息
            result: 结果码(0=成功)
            data: 响应数据
            msg_str: 结果描述
        """
        normalized_result = normalize_error_code(result)
        reply_msg = msg_str or (describe_error(normalized_result) if normalized_result else "")
        return {
            "bid": str(uuid.uuid4()),
            "data": {
                **(data or {}),
                "result": normalized_result,
            },
            "gateway": original_msg.get("gateway", ""),
            "tid": original_msg.get("tid", ""),
            "timestamp": int(time.time() * 1000),
            "method": original_msg.get("method", ""),
            "msg": reply_msg
        }

    @staticmethod
    def build_property_set_reply(original_msg: dict, results: Dict[str, int] = None) -> dict:
        """
        构造 Dock3 property/set_reply。

        Dock3 属性设置回包的 data 直接按属性名返回每项设置状态：
        {
            "data": {
                "distance_limit_status": {
                    "state": {"result": 0}
                }
            }
        }
        """
        data = {}
        for name, result in (results or {}).items():
            data[name] = {"state": {"result": normalize_error_code(result, context="property")}}
        return {
            "bid": str(uuid.uuid4()),
            "data": data,
            "gateway": original_msg.get("gateway", ""),
            "tid": original_msg.get("tid", ""),
            "timestamp": int(time.time() * 1000),
            "method": original_msg.get("method", ""),
            "msg": ""
        }

    @staticmethod
    def build_event(method: str, data: dict, gateway_sn: str) -> dict:
        """构造事件上报消息"""
        return MessageBuilder.build(method, data, gateway_sn, need_reply=False)

    @staticmethod
    def build_osd(gateway_sn: str, osd_data: dict) -> dict:
        """构造OSD定频数据"""
        return {
            "tid": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "data": osd_data,
            "gateway": gateway_sn
        }

    @staticmethod
    def build_state(gateway_sn: str, state_data: dict) -> dict:
        """构造状态变更消息"""
        return {
            "tid": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "data": state_data,
            "gateway": gateway_sn
        }


class TopicBuilder:
    """Topic构造器"""

    @staticmethod
    def topic(prefix: str, gateway_sn: str, suffix: str, device_sn: str = None) -> str:
        """构造Topic路径"""
        sn = device_sn or gateway_sn
        return f"{prefix}/{sn}/{suffix}"

    @classmethod
    def requests(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "requests")

    @classmethod
    def requests_reply(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "requests_reply")

    @classmethod
    def events(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "events")

    @classmethod
    def events_reply(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "events_reply")

    @classmethod
    def services(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "services")

    @classmethod
    def services_reply(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "services_reply")

    @classmethod
    def property_set(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "property/set")

    @classmethod
    def property_set_reply(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "property/set_reply")

    @classmethod
    def osd(cls, device_sn: str) -> str:
        return cls.topic("thing/product", device_sn, "osd")

    @classmethod
    def state(cls, device_sn: str) -> str:
        return cls.topic("thing/product", device_sn, "state")

    @classmethod
    def drc_down(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "drc/down")

    @classmethod
    def drc_up(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "drc/up")

    @classmethod
    def hms(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "hms")

    @classmethod
    def hms_reply(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "hms_reply")

    @classmethod
    def log_upload(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "log/upload")

    @classmethod
    def log_upload_reply(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "log/upload_reply")

    @classmethod
    def workspace(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "workspace")

    @classmethod
    def workspace_reply(cls, gateway_sn: str) -> str:
        return cls.topic("thing/product", gateway_sn, "workspace_reply")

    @classmethod
    def status(cls, gateway_sn: str) -> str:
        return cls.topic("sys/product", gateway_sn, "status")

    @classmethod
    def status_reply(cls, gateway_sn: str) -> str:
        return cls.topic("sys/product", gateway_sn, "status_reply")


def parse_message(payload) -> Optional[Dict[str, Any]]:
    """解析MQTT消息载荷"""
    try:
        if isinstance(payload, bytes):
            return json.loads(payload.decode('utf-8'))
        elif isinstance(payload, str):
            return json.loads(payload)
        elif isinstance(payload, dict):
            return payload
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
