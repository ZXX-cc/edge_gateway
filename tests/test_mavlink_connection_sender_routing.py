import sys
import types
import unittest


if "pymavlink" not in sys.modules:
    pymavlink = types.ModuleType("pymavlink")
    pymavlink.mavutil = types.SimpleNamespace()
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = pymavlink.mavutil

from src.core.connection import MAVLinkConnection


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


if __name__ == "__main__":
    unittest.main()
