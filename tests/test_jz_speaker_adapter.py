import sys
import types
import unittest
from copy import deepcopy

if "pymavlink" not in sys.modules:
    pymavlink = types.ModuleType("pymavlink")
    mavutil = types.SimpleNamespace(
        mavlink=types.SimpleNamespace(MAV_CMD_DO_SET_SERVO=183)
    )
    pymavlink.mavutil = mavutil
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = mavutil

from src.payload.jz_speaker import build_frame, parse_frame
from src.payload.third_party import ThirdPartyController
from src.payload.topology import PayloadTopology
from src.utils.config import Config


def message(method, data=None):
    return {
        "method": method,
        "gateway": "EDGEGW001",
        "tid": "speaker-test",
        "data": data or {},
    }


class JzSpeakerAdapterTest(unittest.TestCase):
    def setUp(self):
        self._config = deepcopy(Config._data)
        Config._data = {}

    def tearDown(self):
        Config._data = self._config

    def test_builds_jz_protocol_frame_with_total_length(self):
        frame = build_frame(0x52, 0x10, b"\x50", ack=1, seq=7)

        self.assertEqual(frame.hex(" "), "5a 5a 77 00 0c 01 07 52 10 50 00 23")

    def test_parses_jz_reply_frame(self):
        reply = bytes.fromhex("5b 5b 77 00 0c 00 07 53 01 64 00 23")

        parsed = parse_frame(reply)

        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["func1"], 0x53)
        self.assertEqual(parsed["func2"], 0x01)
        self.assertEqual(parsed["data"], b"\x64")

    def test_default_config_contains_jz_speaker_serial_defaults(self):
        speaker = Config.get_payload_config()["devices"]["speaker"]
        control = speaker["channels"]["control_audio"]

        self.assertEqual(control["serial"]["baudrate"], 115200)
        self.assertEqual(control["serial"]["text_encoding"], "utf-8")

    def test_unconfigured_speaker_tts_keeps_simulated_success(self):
        topology = PayloadTopology(Config.get_payload_config()["devices"])
        controller = ThirdPartyController()
        controller.set_payload_topology(topology)

        reply = controller.handle_speaker_tts_play_start(
            message("speaker_tts_play_start", {"text": "测试喊话", "volume": "88"})
        )

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["status"], "ok")
        self.assertTrue(reply["data"]["output"]["simulated"])
        self.assertTrue(controller.handle_speaker_status_query(message("speaker_status_query"))["data"]["playing"])

    def test_empty_tts_text_is_rejected(self):
        controller = ThirdPartyController()

        reply = controller.handle_speaker_tts_play_start(
            message("speaker_tts_play_start", {"text": ""})
        )

        self.assertEqual(reply["data"]["result"], 325001)


if __name__ == "__main__":
    unittest.main()
