"""
配置模块 - 加载和管理网关配置
"""
import os
import yaml
from pathlib import Path


class Config:
    """配置管理器 - 单例模式"""

    _instance = None
    _data = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, path="/etc/edge_gateway/gateway.yaml"):
        """从YAML文件加载配置"""
        # 按优先级查找配置文件
        search_paths = [
            path,
            os.environ.get('EDGE_GATEWAY_CONFIG', ''),
            './config/gateway.yaml',
            '../config/gateway.yaml',
        ]

        for p in search_paths:
            if p and Path(p).exists():
                with open(p, 'r', encoding='utf-8') as f:
                    Config._data = yaml.safe_load(f)
                return Config._data

        raise FileNotFoundError(f"Config file not found in: {search_paths}")

    @classmethod
    def get(cls, key_path, default=None):
        """通过点分隔路径获取配置值"""
        keys = key_path.split('.')
        value = Config._data
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    @classmethod
    def all(cls):
        """获取完整配置"""
        return Config._data

    @classmethod
    def get_gateway_sn(cls):
        """获取网关SN"""
        return cls.get('gateway.sn', 'UNKNOWN')

    @classmethod
    def get_mqtt_config(cls):
        """获取MQTT配置"""
        return {
            'host': cls.get('nanomq.host', '127.0.0.1'),
            'port': cls.get('nanomq.port', 1883),
            'username': cls.get('nanomq.username', ''),
            'password': cls.get('nanomq.password', ''),
            'topic_prefix': cls.get('nanomq.topic_prefix', 'thing/product')
        }

    @classmethod
    def get_mavlink_config(cls):
        """获取MAVLink配置"""
        return {
            'connection_type': cls.get('mavlink.connection.type', 'serial'),
            'port': cls.get('mavlink.connection.port', '/dev/ttyACM0'),
            'baudrate': cls.get('mavlink.connection.baudrate', 921600),
            'host': cls.get('mavlink.connection.host', '127.0.0.1'),
            'udp_port': cls.get('mavlink.connection.port', 14550),
            'system_id': cls.get('mavlink.system_id', 1),
            'component_id': cls.get('mavlink.component_id', 190)
        }

    @classmethod
    def get_nest_mavlink_config(cls):
        """获取机巢MAVLink配置"""
        return {
            'enabled': cls.get('nest_mavlink.enabled', False),
            'connection_type': cls.get('nest_mavlink.connection.type', 'udp'),
            'port': cls.get('nest_mavlink.connection.port', 14550),
            'baudrate': cls.get('nest_mavlink.connection.baudrate', 57600),
            'host': cls.get('nest_mavlink.connection.host', '127.0.0.1'),
            'udp_port': cls.get('nest_mavlink.connection.port', 14550),
            'system_id': cls.get('nest_mavlink.system_id', 1),
            'component_id': cls.get('nest_mavlink.component_id', 190)
        }


# 便捷函数
def load_config(path="/etc/edge_gateway/gateway.yaml"):
    """加载配置"""
    return Config().load(path)


def config(key_path, default=None):
    """获取配置值"""
    return Config.get(key_path, default)
