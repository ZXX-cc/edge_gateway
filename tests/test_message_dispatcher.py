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
    connection_string = "udp:127.0.0.1:14550"

    def is_connected(self):
        return True

    def check_heartbeat_timeout(self):
        return False

    def recv_message(self, timeout=0.1):
        return None

    def disconnect(self):
        pass

    def connect(self):
        return True

    def get_connection_info(self):
        return {}


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

    def test_reconnect_runs_when_connection_is_disconnected(self):
        class ReconnectConnection(FakeConnection):
            def __init__(self):
                self.connected = False
                self.disconnect_calls = 0
                self.connect_calls = 0

            def is_connected(self):
                return self.connected

            def disconnect(self):
                self.disconnect_calls += 1

            def connect(self):
                self.connect_calls += 1
                self.connected = True
                return True

            def get_connection_info(self):
                return {"disconnect_reason": "TCP peer closed connection"}

        connection = ReconnectConnection()
        dispatcher = MessageDispatcher(connection)
        dispatcher._last_reconnect_attempt = -999

        self.assertTrue(dispatcher._maybe_reconnect())
        self.assertTrue(connection.connected)
        self.assertEqual(connection.disconnect_calls, 1)
        self.assertEqual(connection.connect_calls, 1)


if __name__ == "__main__":
    unittest.main()
