"""
配置模块 - 加载和管理网关配置
"""
import os
import yaml
from pathlib import Path
from copy import deepcopy


DEFAULT_PAYLOAD_DEVICES = {
    'gimbal': {
        'name': '吊舱',
        'enabled': True,
        'auto_connect': True,
        'direct_to_gateway': True,
        'cloud_api': {
            'type': 52,
            'subtype': 0,
            'gimbal_index': 0,
            'video_index': 'normal-0',
            'video_type': 'normal',
            'switchable_video_types': ['normal', 'wide', 'zoom', 'infrared'],
        },
        'channels': {
            'control': {
                'direction': 'bidirectional',
                'transport': 'viewlink_tcp',
                'adapter': 'viewlink',
                'host': '192.168.2.119',
                'port': 2000,
                'sdk_dir': '',
            },
            'video': {
                'direction': 'payload_to_gateway',
                'transport': 'rtsp',
                'endpoint': '',
                'timing': 'realtime',
            },
        },
    },
    'mapping_camera': {
        'name': '正射/倾斜相机',
        'enabled': True,
        'direct_to_gateway': True,
        'cloud_api': {
            'type': 52,
            'subtype': 0,
            'gimbal_index': 1,
            'video_index': 'mapping-0',
            'video_type': 'normal',
            'source_key': 'mapping_camera',
            'switchable_video_types': ['normal'],
        },
        'channels': {
            'control': {
                'direction': 'conditional',
                'transport': 'ttl_via_flight_controller_when_armed_gateway_direct_when_disarmed',
                'protocol': 'rainpoo_v3',
                'flight_controller_state_key': 'armed',
                'gateway_host': '192.168.5.74',
                'serial': {
                    'port': '',
                    'baudrate': 115200,
                    'level': 'ttl_3v3',
                    'photo_command_hex': 'FA FA 00 01 00 00 01 A1 03 01 AC 11 AF AF',
                    'photo_success_reply_hex': 'FB FB 00 01 00 00 01 A1 03 00 BC 30 BF BF',
                },
                'trigger': {
                    'mode': 'low_level',
                    'hold_ms': 50,
                },
                'feedback': {
                    'mode': 'low_level',
                    'hold_ms': 5,
                },
                'timing': 'on_demand',
            },
            'video': {
                'direction': 'payload_to_gateway',
                'transport': 'rtsp',
                'endpoint': '',
                'timing': 'realtime',
            },
            'post_landing_raw_data': {
                'direction': 'payload_to_gateway',
                'transport': 'ftp',
                'endpoint': 'ftp://192.168.5.74/DCIM',
                'smb_endpoint': '\\\\192.168.5.74\\DCIM',
                'file_pattern': '*/Riebo*.jpg',
                'timeout_seconds': 5,
                'timing': 'post_landing',
            },
        },
    },
    'lidar': {
        'name': '激光雷达',
        'enabled': True,
        'direct_to_gateway': True,
        'channels': {
            'post_landing_raw_data': {
                'direction': 'payload_to_gateway',
                'transport': 'vendor_file_transfer',
                'endpoint': '',
                'timing': 'post_landing',
            },
        },
    },
    'spectral_camera': {
        'name': '多光谱/高光谱',
        'enabled': True,
        'direct_to_gateway': True,
        'channels': {
            'video': {
                'direction': 'payload_to_gateway',
                'transport': 'vendor_stream',
                'endpoint': '',
                'timing': 'realtime',
            },
            'post_landing_raw_data': {
                'direction': 'payload_to_gateway',
                'transport': 'vendor_file_transfer',
                'endpoint': '',
                'timing': 'post_landing',
            },
        },
    },
    'speaker': {
        'name': '喊话器',
        'enabled': True,
        'direct_to_gateway': True,
        'channels': {
            'control_audio': {
                'direction': 'gateway_to_payload',
                'transport': 'vendor_protocol',
                'endpoint': '',
                'timing': 'on_demand',
            },
        },
    },
}


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
    def merge(cls, overrides: dict):
        """Merge runtime overrides into loaded configuration."""
        if not overrides:
            return Config._data

        def deep_merge(base, patch):
            for key, value in (patch or {}).items():
                if isinstance(value, dict) and isinstance(base.get(key), dict):
                    deep_merge(base[key], value)
                else:
                    base[key] = deepcopy(value)
            return base

        if not isinstance(Config._data, dict):
            Config._data = {}
        deep_merge(Config._data, overrides)
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
            'topic_prefix': cls.get('nanomq.topic_prefix', 'thing/product'),
            # 仅用于日志/媒体等明确的本地 IO handler，控制类 method 保持同步执行。
            'background_handler_workers': cls.get('nanomq.background_handler_workers', 4),
            'dedup_ttl_seconds': cls.get('nanomq.dedup_ttl_seconds', 300),
            'dedup_max_entries': cls.get('nanomq.dedup_max_entries', 1024),
        }

    @classmethod
    def get_mavlink_config(cls):
        """获取MAVLink配置"""
        return {
            'enabled': cls.get('mavlink.enabled', True),
            'connection_type': cls.get('mavlink.connection.type', 'serial'),
            'port': cls.get('mavlink.connection.port', '/dev/ttyACM0'),
            'baudrate': cls.get('mavlink.connection.baudrate', 921600),
            'host': cls.get('mavlink.connection.host', '127.0.0.1'),
            'udp_port': cls.get('mavlink.connection.port', 14550),
            'system_id': cls.get('mavlink.system_id', 1),
            'component_id': cls.get('mavlink.component_id', 190),
            'telemetry_rate': cls.get('mavlink.telemetry_rate', {}) or {},
        }

    @classmethod
    def get_database_config(cls):
        """获取运行时数据库配置。"""
        return cls.get('database', {}) or {}

    @classmethod
    def get_payload_config(cls):
        """获取负载控制配置。"""
        configured_devices = cls.get('payload.devices', {}) or {}
        devices = deepcopy(DEFAULT_PAYLOAD_DEVICES)

        def deep_merge(base, patch):
            for key, value in patch.items():
                if isinstance(value, dict) and isinstance(base.get(key), dict):
                    deep_merge(base[key], value)
                else:
                    base[key] = deepcopy(value)

        if isinstance(configured_devices, dict):
            deep_merge(devices, configured_devices)

        legacy_viewlink_enabled = cls.get('payload.direct_viewlink_sdk_enabled', None)
        if legacy_viewlink_enabled is not None:
            devices['gimbal']['enabled'] = bool(legacy_viewlink_enabled)

        gimbal_control = devices['gimbal'].setdefault('channels', {}).setdefault('control', {})
        legacy_sdk_dir = cls.get('payload.viewlink_sdk_dir', None)
        legacy_gimbal_ip = cls.get('payload.viewlink_gimbal_ip', None)
        legacy_gimbal_port = cls.get('payload.viewlink_gimbal_port', None)
        if legacy_sdk_dir is not None:
            gimbal_control['sdk_dir'] = legacy_sdk_dir
        if legacy_gimbal_ip is not None:
            gimbal_control['host'] = legacy_gimbal_ip
        if legacy_gimbal_port is not None:
            gimbal_control['port'] = legacy_gimbal_port

        return {
            'control_path': cls.get('payload.control_path', 'direct_gateway'),
            'flight_controller_fallback_enabled': cls.get(
                'payload.flight_controller_fallback_enabled', False
            ),
            'devices': devices,
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
