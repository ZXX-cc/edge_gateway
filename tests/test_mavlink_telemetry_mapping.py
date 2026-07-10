#!/usr/bin/env python3
"""标准 MAVLink 遥测字段映射测试。"""
from __future__ import annotations

import math
import sys
import time
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

    def test_heartbeat_uses_vehicle_specific_mode_profile(self):
        telemetry = new_telemetry_state()

        apply_mavlink_message(
            SimpleNamespace(msg_type="HEARTBEAT", type=2, base_mode=0x80, custom_mode=9, autopilot=3),
            telemetry,
        )
        osd = build_osd_data(telemetry)

        self.assertEqual(telemetry["flight_mode_profile"], "copter")
        self.assertEqual(telemetry["flight_mode"], "LAND")
        self.assertEqual(osd["mode_code"], 7)
        self.assertEqual(osd["gear"], 1)

        apply_mavlink_message(
            SimpleNamespace(msg_type="HEARTBEAT", type=1, base_mode=0, custom_mode=15, autopilot=3),
            telemetry,
        )

        self.assertEqual(telemetry["flight_mode_profile"], "plane")
        self.assertEqual(telemetry["flight_mode"], "GUIDED")

    def test_gps_quality_falls_back_to_satellite_count(self):
        telemetry = new_telemetry_state()

        apply_mavlink_message(
            SimpleNamespace(msg_type="GPS_RAW_INT", fix_type=3, satellites_visible=17),
            telemetry,
        )

        self.assertEqual(telemetry["gps_fixed"], 1)
        self.assertEqual(telemetry["gps_signal_level"], 4)
        self.assertEqual(build_osd_data(telemetry)["position_state"]["quality"], 4)

    def test_battery_status_uses_extended_voltage_cells_and_clamps_capacity(self):
        telemetry = new_telemetry_state()

        apply_mavlink_message(
            SimpleNamespace(
                msg_type="BATTERY_STATUS",
                voltages=[4200, 4200, 65535, 0],
                voltages_ext=[4100, 4100],
                current_battery=1234,
                battery_remaining=120,
            ),
            telemetry,
        )

        self.assertEqual(telemetry["voltage"], 16.6)
        self.assertEqual(telemetry["current"], 12.34)
        self.assertEqual(telemetry["batteryRemaining"], 100)
        self.assertTrue(telemetry["battery_available"])
        self.assertEqual(build_osd_data(telemetry)["battery"]["voltage"], 16600)

    def test_mount_status_maps_legacy_gimbal_angles(self):
        telemetry = new_telemetry_state()

        apply_mavlink_message(
            SimpleNamespace(
                msg_type="MOUNT_STATUS",
                pointing_a=-1200,
                pointing_b=300,
                pointing_c=9050,
            ),
            telemetry,
        )

        osd = build_osd_data(telemetry)
        self.assertEqual(osd["gimbal_pitch"], -12.0)
        self.assertEqual(osd["gimbal_roll"], 3.0)
        self.assertEqual(osd["gimbal_yaw"], 90.5)

    def test_flight_time_accumulates_while_armed(self):
        telemetry = new_telemetry_state()

        apply_mavlink_message(
            SimpleNamespace(msg_type="HEARTBEAT", type=2, base_mode=0x80, custom_mode=4, autopilot=3),
            telemetry,
        )
        telemetry["_flight_start_time"] = time.time() - 3
        apply_mavlink_message(
            SimpleNamespace(msg_type="HEARTBEAT", type=2, base_mode=0x80, custom_mode=4, autopilot=3),
            telemetry,
        )

        self.assertGreaterEqual(telemetry["flight_time"], 2)
        apply_mavlink_message(
            SimpleNamespace(msg_type="HEARTBEAT", type=2, base_mode=0, custom_mode=4, autopilot=3),
            telemetry,
        )
        self.assertIsNone(telemetry["_flight_start_time"])
        self.assertGreaterEqual(build_osd_data(telemetry)["flight_time"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
