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
            {"video", "post_landing_raw_data"},
        )
        self.assertEqual(
            set(topology.get_device("lidar")["channels"]),
            {"post_landing_raw_data"},
        )
        self.assertEqual(
            set(topology.get_device("spectral_camera")["channels"]),
            {"video", "post_landing_raw_data"},
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
            spectral_channels["video"]["timing"], "realtime"
        )
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
                    "payload_index": "0",
                    "gimbal": {"pitch": -20, "roll": 0, "yaw": 30, "mode": 0},
                }
            },
        )

        reply = controller.handle_payload_status_query(message("payload_status_query"))

        payloads = reply["data"]["payloads"]
        self.assertEqual(len(payloads), 5)
        self.assertEqual(payloads[0]["id"], "gimbal")
        self.assertEqual(payloads[0]["connection_state"], "connected")
        self.assertEqual(payloads[0]["payload_type"], "gimbal")
        self.assertEqual(payloads[0]["payload_index"], "0")
        self.assertEqual(payloads[0]["gimbal"]["pitch"], -20)
        self.assertTrue(all(item["direct_to_gateway"] for item in payloads))
        self.assertFalse(reply["data"]["flight_controller_fallback"])


if __name__ == "__main__":
    unittest.main()
