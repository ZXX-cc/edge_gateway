import sys
import types
import unittest

if "pymavlink" not in sys.modules:
    fake_pymavlink = types.ModuleType("pymavlink")
    fake_mavutil = types.ModuleType("pymavlink.mavutil")
    fake_mavutil.mavlink_connection = object
    fake_pymavlink.mavutil = fake_mavutil
    sys.modules["pymavlink"] = fake_pymavlink
    sys.modules["pymavlink.mavutil"] = fake_mavutil

from src.core.message_dispatcher import MessageDispatcher


class FakeConnection:
    use_custom_dialect = False

    def is_connected(self):
        return True

    def check_heartbeat_timeout(self):
        return False

    def recv_message(self, timeout=0.1):
        return None


class MessageDispatcherHeartbeatTest(unittest.TestCase):
    def test_heartbeat_timeout_log_every_ten_times(self):
        dispatcher = MessageDispatcher(FakeConnection())

        for _ in range(9):
            self.assertFalse(dispatcher._should_log_heartbeat_timeout(True))
        self.assertTrue(dispatcher._should_log_heartbeat_timeout(True))

        for _ in range(9):
            self.assertFalse(dispatcher._should_log_heartbeat_timeout(True))
        self.assertTrue(dispatcher._should_log_heartbeat_timeout(True))
        self.assertEqual(dispatcher._heartbeat_timeout_count, 20)

    def test_heartbeat_timeout_counter_resets_after_recovery(self):
        dispatcher = MessageDispatcher(FakeConnection())

        for _ in range(9):
            dispatcher._should_log_heartbeat_timeout(True)
        self.assertFalse(dispatcher._should_log_heartbeat_timeout(False))
        self.assertEqual(dispatcher._heartbeat_timeout_count, 0)

        for _ in range(9):
            self.assertFalse(dispatcher._should_log_heartbeat_timeout(True))
        self.assertTrue(dispatcher._should_log_heartbeat_timeout(True))
        self.assertEqual(dispatcher._heartbeat_timeout_count, 10)


if __name__ == "__main__":
    unittest.main()
