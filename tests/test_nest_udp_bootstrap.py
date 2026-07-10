"""Regression tests for the passive UDP nest simulator bootstrap packet."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.connection import build_mavlink_v1_heartbeat, send_udp_bootstrap_heartbeat  # noqa: E402


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, target):
        self.sent.append((data, target))


class NestUdpBootstrapTest(unittest.TestCase):
    def test_heartbeat_has_standard_mavlink_v1_header_and_payload(self):
        frame = build_mavlink_v1_heartbeat(1, 190)

        self.assertEqual(frame[:6], bytes((0xFE, 9, 0, 1, 190, 0)))
        self.assertEqual(frame[6:15], bytes((0, 0, 0, 0, 6, 8, 0, 0, 3)))
        self.assertEqual(len(frame), 17)

    def test_bootstrap_sends_heartbeat_to_nest_endpoint(self):
        sock = FakeSocket()

        send_udp_bootstrap_heartbeat(sock, ("127.0.0.1", 14551), 1, 190)

        self.assertEqual(len(sock.sent), 1)
        self.assertEqual(sock.sent[0][1], ("127.0.0.1", 14551))
        self.assertEqual(sock.sent[0][0][0], 0xFE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
