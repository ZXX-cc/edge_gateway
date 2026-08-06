"""Yusense/YCam multispectral camera direct payload adapter."""
from __future__ import annotations

import socket
from copy import deepcopy
from typing import Any, Mapping

from src.payload.dock3_payload import payload_index_from_config
from src.utils.config import Config


class SpectralCameraAdapter:
    """Expose the YCam multispectral camera as a direct gateway payload.

    YCamSDK v2.0's Linux/Qt demos use a gateway-side TCP server model:
    command messages arrive on 23001, returned files on 23002, and realtime
    video is enabled through the SDK before the stream is pushed to the gateway.
    This adapter keeps the hardware contract visible to Dock3 status/live
    queries without requiring the native SDK library in the Python process.
    """

    def __init__(self, config: Mapping[str, Any] | None = None):
        self.config = deepcopy(dict(config or {}))

    def build_payload_state(self) -> dict:
        config = self.config
        channels = config.get("channels", {}) if isinstance(config, dict) else {}
        control = channels.get("control", {}) if isinstance(channels, dict) else {}
        video = channels.get("video", {}) if isinstance(channels, dict) else {}
        data_channel = channels.get("post_landing_raw_data", {}) if isinstance(channels, dict) else {}
        cloud_api = config.get("cloud_api", {}) if isinstance(config, dict) else {}

        host = str(
            control.get("remote_host")
            or control.get("host")
            or config.get("host")
            or ""
        )
        probe_timeout = float(control.get("probe_timeout_seconds", 0.3) or 0.3)
        probe_ports = control.get("probe_ports")
        if not isinstance(probe_ports, list) or not probe_ports:
            probe_ports = [80, 554]
        reachable_ports = [
            int(port)
            for port in probe_ports
            if self._tcp_host_reachable(host, int(port), probe_timeout)
        ]

        endpoint = str(video.get("endpoint") or Config.get("media.video_sources.spectral_camera", ""))
        stream_configured = bool(endpoint)
        payload_index = payload_index_from_config(config)
        video_index = str(cloud_api.get("video_index") or "spectral-0")
        connection_state = "connected" if reachable_ports else "configured"
        command_port = int(control.get("command_port", 23001) or 23001)
        file_port = int(control.get("file_port", 23002) or 23002)
        sdk_runtime_state = str(control.get("sdk_runtime_state") or "native_helper_required")

        return {
            "payload_type": "spectral_camera",
            "payload_model_key": str(config.get("model_key", "yusense-ycam")),
            "payload_name": str(config.get("name", "多光谱/高光谱")),
            "payload_index": payload_index,
            "connection_state": connection_state,
            "adapter": str(control.get("adapter") or "ycam_sdk"),
            "host": host,
            "sdk": {
                "name": "YCamSDK",
                "version": str(control.get("sdk_version") or "v2.0"),
                "protocol": str(control.get("protocol") or "ycam_api"),
                "runtime_state": sdk_runtime_state,
                "server_model": "camera_client_gateway_server",
                "required_callbacks": [
                    "CmdParseCallBack",
                    "PicDataCallBack",
                    "SdkSendMesToCamera",
                    "SdkSendPicMesToCamera",
                ],
                "required_entrypoints": [
                    "YUS_DLL_Init",
                    "YUS_DLL_ProtocolParse",
                    "YUS_DLL_ImageParse",
                    "YUS_DLL_CameraTakePic",
                    "YUS_DLL_ImageTransmit",
                    "YUS_DLL_RealVideoPlay",
                ],
            },
            "control": {
                "transport": control.get("transport", "ycam_tcp_server"),
                "listen_host": control.get("listen_host", "0.0.0.0"),
                "command_port": command_port,
                "file_port": file_port,
                "remote_host": host,
                "probe_ports": [int(port) for port in probe_ports],
                "reachable_ports": reachable_ports,
                "native_helper_required": True,
                "native_helper_ready": sdk_runtime_state == "native_helper_ready",
            },
            "video": {
                "transport": video.get("transport", "rtp"),
                "endpoint": endpoint,
                "video_id": f"{Config.get_gateway_sn()}/{payload_index}/{video_index}",
                "reachable": stream_configured,
                "push_stream": True,
            },
            "data_link": {
                "transport": data_channel.get("transport", "ycam_tcp_file"),
                "endpoint": data_channel.get("endpoint", "tcp://0.0.0.0:23002"),
                "listen_host": data_channel.get("listen_host", control.get("listen_host", "0.0.0.0")),
                "listen_port": int(data_channel.get("listen_port", control.get("file_port", 23002)) or 23002),
                "file_pattern": data_channel.get("file_pattern", "*"),
                "timing": data_channel.get("timing", "post_landing"),
            },
        }

    @staticmethod
    def _tcp_host_reachable(host: str, port: int, timeout: float = 0.3) -> bool:
        if not host or port <= 0:
            return False
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False
