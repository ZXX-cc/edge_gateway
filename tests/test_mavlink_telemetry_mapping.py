#!/usr/bin/env python3
"""标准 MAVLink 遥测字段映射测试。"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.mavlink.telemetry.mapping import apply_mavlink_message, build_osd_data, new_telemetry_state  # noqa: E402


class MavlinkTelemetryMappingTest(unittest.TestCase):
    def test_extended_telemetry_messages(self):
        telemetry = new_telemetry_state()

        apply_mavlink_message(
            SimpleNamespace(
                msg_type="GPS_RAW_INT",
                fix_type=3,
                satellites_visible=18,
                eph=80,
                epv=120,
            ),
            telemetry,
        )
        apply_mavlink_message(
            SimpleNamespace(msg_type="VFR_HUD", airspeed=12.5, groundspeed=10.0, climb=1.2, heading=87),
            telemetry,
        )
        apply_mavlink_message(
            SimpleNamespace(
                msg_type="GIMBAL_DEVICE_ATTITUDE_STATUS",
                q=[math.cos(math.radians(45)), 0.0, 0.0, math.sin(math.radians(45))],
            ),
            telemetry,
        )
        apply_mavlink_message(
            SimpleNamespace(
                msg_type="DISTANCE_SENSOR",
                orientation=0,
                current_distance=350,
                min_distance=20,
                max_distance=1200,
            ),
            telemetry,
        )
        apply_mavlink_message(
            SimpleNamespace(
                msg_type="EKF_STATUS_REPORT",
                flags=831,
                velocity_variance=0.1,
                pos_horiz_variance=0.2,
                pos_vert_variance=0.3,
                compass_variance=0.4,
                terrain_alt_variance=0.5,
            ),
            telemetry,
        )

        osd = build_osd_data(telemetry)
        self.assertEqual(telemetry["gps_signal_level"], 4)
        self.assertEqual(osd["position_state"]["quality"], 4)
        self.assertEqual(osd["course"], 87)
        self.assertAlmostEqual(osd["gimbal_yaw"], 90.0)
        self.assertEqual(osd["obstacle_state"]["0"]["distance"], 3.5)
        self.assertEqual(telemetry["estimator_status"]["flags"], 831)


if __name__ == "__main__":
    unittest.main(verbosity=2)
