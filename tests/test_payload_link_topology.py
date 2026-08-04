import unittest
import sys
import types
from copy import deepcopy

if "pymavlink" not in sys.modules:
    pymavlink = types.ModuleType("pymavlink")
    mavutil = types.SimpleNamespace(
        mavlink=types.SimpleNamespace(MAV_CMD_DO_SET_SERVO=183)
    )
    pymavlink.mavutil = mavutil
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = mavutil

from src.payload.third_party import ThirdPartyController
from src.payload.topology import PayloadTopology
from src.utils.config import Config


def message(method):
    return {"method": method, "gateway": "EDGEGW001", "tid": "topology-test", "data": {}}


class PayloadLinkTopologyTest(unittest.TestCase):
    def setUp(self):
        self._config = deepcopy(Config._data)
        Config._data = {}

    def tearDown(self):
        Config._data = self._config

    def test_default_topology_matches_direct_payload_diagram(self):
        payload_config = Config.get_payload_config()
        topology = PayloadTopology(payload_config["devices"])

        self.assertEqual(topology.validate(), [])
        self.assertFalse(payload_config["flight_controller_fallback_enabled"])
        self.assertEqual(
            set(topology.get_device("gimbal")["channels"]),
            {"control", "video"},
        )
        self.assertEqual(
            set(topology.get_device("mapping_camera")["channels"]),
            {"control", "video", "post_landing_raw_data"},
        )
        self.assertEqual(
            set(topology.get_device("lidar")["channels"]),
            {"post_landing_raw_data"},
        )
        self.assertEqual(
            set(topology.get_device("spectral_camera")["channels"]),
            {"control", "video", "post_landing_raw_data"},
        )
        self.assertEqual(
            set(topology.get_device("speaker")["channels"]),
            {"control_audio"},
        )
        mapping_channels = topology.get_device("mapping_camera")["channels"]
        spectral_channels = topology.get_device("spectral_camera")["channels"]
        speaker_channels = topology.get_device("speaker")["channels"]
        self.assertEqual(
            mapping_channels["post_landing_raw_data"]["timing"], "post_landing"
        )
        self.assertEqual(
            mapping_channels["control"]["transport"],
            "ttl_via_flight_controller_when_armed_gateway_direct_when_disarmed",
        )
        self.assertEqual(
            spectral_channels["video"]["timing"], "realtime"
        )
        self.assertEqual(spectral_channels["control"]["remote_host"], "192.168.1.115")
        self.assertEqual(spectral_channels["control"]["command_port"], 23001)
        self.assertEqual(spectral_channels["post_landing_raw_data"]["listen_port"], 23002)
        self.assertEqual(
            speaker_channels["control_audio"]["direction"],
            "gateway_to_payload",
        )
        for _, device in topology.iter_devices():
            self.assertTrue(device["direct_to_gateway"])

    def test_direct_gimbal_endpoint_can_be_overridden(self):
        Config._data = {
            "payload": {
                "devices": {
                    "gimbal": {
                        "channels": {
                            "control": {
                                "host": "10.20.30.40",
                                "port": 2300,
                            }
                        }
                    }
                }
            }
        }
        control = Config.get_payload_config()["devices"]["gimbal"]["channels"]["control"]

        self.assertEqual(control["host"], "10.20.30.40")
        self.assertEqual(control["port"], 2300)
        self.assertEqual(control["adapter"], "viewlink")

    def test_legacy_viewlink_fields_override_direct_gimbal_endpoint(self):
        Config._data = {
            "payload": {
                "viewlink_sdk_dir": "D:/ViewLink/bin/win64",
                "viewlink_gimbal_ip": "10.10.10.10",
                "viewlink_gimbal_port": 2400,
            }
        }

        control = Config.get_payload_config()["devices"]["gimbal"]["channels"]["control"]

        self.assertEqual(control["sdk_dir"], "D:/ViewLink/bin/win64")
        self.assertEqual(control["host"], "10.10.10.10")
        self.assertEqual(control["port"], 2400)

    def test_topology_rejects_realtime_raw_data_transfer(self):
        devices = Config.get_payload_config()["devices"]
        devices["lidar"]["channels"]["post_landing_raw_data"]["timing"] = "realtime"

        errors = PayloadTopology(devices).validate()

        self.assertIn(
            "payload.devices.lidar.channels.post_landing_raw_data.timing must be post_landing",
            errors,
        )

    def test_payload_status_query_exposes_all_direct_links(self):
        topology = PayloadTopology(Config.get_payload_config()["devices"])
        controller = ThirdPartyController()
        controller.set_payload_topology(
            topology,
            lambda: {
                "gimbal": {
                    "connection_state": "connected",
                    "payload_type": "gimbal",
                    "payload_index": "52-0-0",
                    "gimbal": {"pitch": -20, "roll": 0, "yaw": 30, "mode": 0},
                },
                "mapping_camera": {
                    "connection_state": "configured",
                    "payload_type": "mapping_camera",
                    "payload_model_key": "rainpoo-r6p",
                    "payload_index": "52-0-1",
                    "control_route": "gateway_direct",
                    "video": {
                        "transport": "rtsp",
                        "endpoint": "rtsp://192.168.5.74:8554/LIVE",
                        "video_id": "EDGEGW001/52-0-1/mapping-0",
                        "reachable": False,
                    },
                    "data_link": {
                        "transport": "ftp",
                        "endpoint": "ftp://192.168.5.74/DCIM",
                        "reachable": False,
                    },
                    "last_photo_cache": {"file_path": "/data/media/Riebo000001.jpg"},
                },
                "spectral_camera": {
                    "connection_state": "configured",
                    "payload_type": "spectral_camera",
                    "payload_model_key": "yusense-ycam",
                    "payload_index": "52-0-2",
                    "host": "192.168.1.115",
                    "control": {
                        "transport": "ycam_tcp_server",
                        "command_port": 23001,
                        "file_port": 23002,
                    },
                    "video": {
                        "transport": "rtp",
                        "endpoint": "rtp://0.0.0.0:23008",
                        "video_id": "EDGEGW001/52-0-2/spectral-0",
                        "reachable": True,
                    },
                    "data_link": {
                        "transport": "ycam_tcp_file",
                        "endpoint": "tcp://0.0.0.0:23002",
                    },
                },
            },
        )

        reply = controller.handle_payload_status_query(message("payload_status_query"))

        payloads = reply["data"]["payloads"]
        self.assertEqual(len(payloads), 5)
        self.assertEqual(payloads[0]["id"], "gimbal")
        self.assertEqual(payloads[0]["connection_state"], "connected")
        self.assertEqual(payloads[0]["payload_type"], "gimbal")
        self.assertEqual(payloads[0]["payload_index"], "52-0-0")
        self.assertEqual(payloads[0]["type_subtype_gimbalindex"]["payload_index"], "52-0-0")
        self.assertEqual(payloads[0]["gimbal"]["pitch"], -20)
        mapping = next(item for item in payloads if item["id"] == "mapping_camera")
        self.assertEqual(mapping["connection_state"], "configured")
        self.assertEqual(mapping["payload_type"], "mapping_camera")
        self.assertEqual(mapping["payload_index"], "52-0-1")
        self.assertEqual(mapping["control_route"], "gateway_direct")
        self.assertEqual(mapping["video"]["video_id"], "EDGEGW001/52-0-1/mapping-0")
        self.assertEqual(mapping["data_link"]["transport"], "ftp")
        self.assertEqual(mapping["last_photo_cache"]["file_path"], "/data/media/Riebo000001.jpg")
        spectral = next(item for item in payloads if item["id"] == "spectral_camera")
        self.assertEqual(spectral["payload_type"], "spectral_camera")
        self.assertEqual(spectral["payload_index"], "52-0-2")
        self.assertEqual(spectral["host"], "192.168.1.115")
        self.assertEqual(spectral["control"]["command_port"], 23001)
        self.assertEqual(spectral["video"]["video_id"], "EDGEGW001/52-0-2/spectral-0")
        self.assertTrue(all(item["direct_to_gateway"] for item in payloads))
        self.assertFalse(reply["data"]["flight_controller_fallback"])


if __name__ == "__main__":
    unittest.main()
