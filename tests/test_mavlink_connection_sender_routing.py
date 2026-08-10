import sys
from threading import Lock
import types
import unittest
from unittest import mock


if "pymavlink" not in sys.modules:
    pymavlink = types.ModuleType("pymavlink")
    pymavlink.mavutil = types.SimpleNamespace()
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = pymavlink.mavutil

if "src.utils.command_trace" not in sys.modules:
    command_trace = types.ModuleType("src.utils.command_trace")
    command_trace.trace_command = lambda *_args, **_kwargs: None
    sys.modules["src.utils.command_trace"] = command_trace

from src.core.connection import MAVLinkConnection, _coerce_real_drone_nest_response
from src.generated_dialect import drone_nest


class FakeSender:
    def __init__(self, name):
        self.name = name


class FakeMaster:
    def __init__(self):
        self.mav = FakeSender("custom")
        self.standard_mav = FakeSender("standard")


class FakeMessage:
    def __init__(self, msg_id):
        self._msg_id = msg_id

    def get_msgId(self):
        return self._msg_id


class FakeRawMessage:
    def __init__(self, raw_hex):
        self._raw = bytes.fromhex(raw_hex)

    def get_msgbuf(self):
        return self._raw

    def get_type(self):
        return "UNKNOWN_12004"

    def get_msgId(self):
        return -2


class MAVLinkConnectionSenderRoutingTest(unittest.TestCase):
    def test_custom_link_routes_standard_messages_to_standard_encoder(self):
        conn = MAVLinkConnection.__new__(MAVLinkConnection)
        conn.use_custom_dialect = True
        conn._custom_valid_message_ids = {12003, 12004, 12005}
        conn.master = FakeMaster()

        sender = conn._select_sender_for_message(FakeMessage(44))

        self.assertEqual(sender.name, "standard")

    def test_custom_link_keeps_nest_messages_on_custom_encoder(self):
        conn = MAVLinkConnection.__new__(MAVLinkConnection)
        conn.use_custom_dialect = True
        conn._custom_valid_message_ids = {12003, 12004, 12005}
        conn.master = FakeMaster()

        sender = conn._select_sender_for_message(FakeMessage(12003))

        self.assertEqual(sender.name, "custom")

    def test_real_33_byte_nest_status_frame_is_coerced(self):
        raw_hex = (
            "fd2100006a6501e42e00"
            "fe7201001f0100000801b4141400de030000000000000000000005050000020202"
            "8b18"
        )

        msg = _coerce_real_drone_nest_response(FakeRawMessage(raw_hex))

        self.assertEqual(msg.get_type(), "DRONE_NEST_RESPONSE")
        self.assertEqual(msg.get_msgId(), 12004)
        self.assertEqual(msg.get_srcSystem(), 0x65)
        self.assertEqual(msg.lumen, 0x000172FE)
        self.assertEqual(msg.ac_temp, 0x011F)
        self.assertEqual(msg.env_temperature, 0x0108)
        self.assertEqual(msg.wind_direction, 0x14B4)
        self.assertEqual(msg.atmospheric_pressure, 0x03DE)
        self.assertEqual(msg.qr_light_current, 0)
        self.assertEqual(msg.boolstate1, 0x0505)
        self.assertEqual(msg.boolstate2, 0)
        self.assertEqual(msg.clamp_state, 2)
        self.assertEqual(msg.gate_state, 2)
        self.assertEqual(msg.landing_light_state, 2)

    def test_generated_v17_dialect_decodes_real_33_byte_nest_status_frame(self):
        raw = bytes.fromhex(
            "fd2100006a6501e42e00"
            "fe7201001f0100000801b4141400de030000000000000000000005050000020202"
            "8b18"
        )

        msg = drone_nest.MAVLink(None).decode(bytearray(raw))

        self.assertEqual(msg.get_type(), "DRONE_NEST_RESPONSE")
        self.assertEqual(msg.get_msgId(), 12004)
        self.assertEqual(msg.lumen, 0x000172FE)
        self.assertEqual(msg.qr_light_current, 0)
        self.assertEqual(msg.boolstate1, 0x0505)
        self.assertEqual(msg.gate_state, 2)

    def test_generated_v17_dialect_uses_firmware_field_order(self):
        self.assertEqual(
            drone_nest.MAVLink_drone_nest_response_message.fieldnames,
            [
                "ac_temp",
                "nest_temperature",
                "env_temperature",
                "wind_direction",
                "wind_speed",
                "atmospheric_pressure",
                "rainfall",
                "charge_volt",
                "charge_current",
                "clamp_state",
                "gate_state",
                "landing_light_state",
                "error_code",
                "lumen",
                "qr_light_current",
                "boolstate1",
                "boolstate2",
            ],
        )
        self.assertEqual(drone_nest.MAVLink_drone_nest_response_message.crc_extra, 120)

    def test_recv_error_marks_disconnected_without_repeated_console_spam(self):
        conn = MAVLinkConnection.__new__(MAVLinkConnection)
        conn.status = 2
        conn.lock = Lock()
        conn.disconnect_reason = ""
        conn._last_recv_error_signature = None
        conn._last_recv_error_log_time = 0.0

        error = ConnectionResetError("Connection reset or closed by peer on TCP socket")

        with mock.patch("builtins.print") as printed:
            conn._record_recv_error(error)
            conn._record_recv_error(error)

        self.assertEqual(conn.status, 0)
        self.assertEqual(
            conn.disconnect_reason,
            "recv error: Connection reset or closed by peer on TCP socket",
        )
        printed.assert_has_calls([
            mock.call("[Connection] 接收消息错误: Connection reset or closed by peer on TCP socket"),
            mock.call("[Connection] 连接已断开: recv error: Connection reset or closed by peer on TCP socket"),
        ])
        self.assertEqual(printed.call_count, 2)


if __name__ == "__main__":
    unittest.main()
