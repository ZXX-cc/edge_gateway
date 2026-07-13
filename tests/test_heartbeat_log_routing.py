"""无人机 HEARTBEAT 仅落盘的日志路由测试。"""
from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.connection import _log_raw_mavlink, mavlink_raw_logger  # noqa: E402
from src.utils.logger import ConsoleVisibilityFilter  # noqa: E402


class HeartbeatLogRoutingTest(unittest.TestCase):
    @staticmethod
    def _message(msg_type: str, msg_id: int):
        return SimpleNamespace(
            get_type=lambda: msg_type,
            get_msgId=lambda: msg_id,
            get_srcSystem=lambda: 1,
            get_srcComponent=lambda: 1,
            get_msgbuf=lambda: b"\xfd\x00",
        )

    def test_heartbeat_raw_record_is_marked_console_silent(self):
        with patch.object(mavlink_raw_logger, "info") as info:
            _log_raw_mavlink("RX", "udp:127.0.0.1:14551", self._message("HEARTBEAT", 0))

        self.assertTrue(info.call_args.kwargs["extra"]["console_silent"])

    def test_non_heartbeat_raw_record_remains_visible(self):
        with patch.object(mavlink_raw_logger, "info") as info:
            _log_raw_mavlink("RX", "udp:127.0.0.1:14551", self._message("SYS_STATUS", 1))

        self.assertFalse(info.call_args.kwargs["extra"]["console_silent"])

    def test_console_filter_does_not_affect_file_records(self):
        visibility_filter = ConsoleVisibilityFilter()
        heartbeat = logging.LogRecord("edge_gateway.mavlink.raw", logging.INFO, "", 0, "heartbeat", (), None)
        heartbeat.console_silent = True
        status = logging.LogRecord("edge_gateway.mavlink.raw", logging.INFO, "", 0, "status", (), None)

        self.assertFalse(visibility_filter.filter(heartbeat))
        self.assertTrue(visibility_filter.filter(status))


if __name__ == "__main__":
    unittest.main(verbosity=2)
