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

from web.mavlink.telemetry.mapping import (  # noqa: E402
    aircraft_mode_code,
    apply_mavlink_message,
    build_osd_data,
    derive_aircraft_mode_code_from_real_state,
    new_telemetry_state,
)
from web.mavlink.properties.mapping import build_properties_from_osd  # noqa: E402


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
        self.assertEqual(osd["position_state"]["is_fixed"], 2)
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
        self.assertNotIn("mode_code", osd)
        self.assertEqual(osd["gear"], 1)

        apply_mavlink_message(
            SimpleNamespace(msg_type="HEARTBEAT", type=1, base_mode=0, custom_mode=15, autopilot=3),
            telemetry,
        )

        self.assertEqual(telemetry["flight_mode_profile"], "plane")
        self.assertEqual(telemetry["flight_mode"], "GUIDED")

    def test_aircraft_mode_code_only_uses_explicit_dock3_source(self):
        self.assertIsNone(aircraft_mode_code({
            "connection_status": "connected",
            "gps_fixed": 1,
            "armed": True,
            "flight_mode": "AUTO",
        }))
        self.assertEqual(aircraft_mode_code({"aircraft_mode_code": 5}), 5)
        self.assertEqual(aircraft_mode_code({"mode_code": "3"}), 3)
        self.assertIsNone(aircraft_mode_code({"mode_code": 99}))

    def test_derive_aircraft_mode_code_from_real_runtime_state(self):
        self.assertEqual(derive_aircraft_mode_code_from_real_state({
            "connection_status": "disconnected",
        }), 0)
        self.assertEqual(derive_aircraft_mode_code_from_real_state({
            "connection_status": "connected",
            "gps_fixed": 0,
            "armed": False,
        }), 1)
        self.assertEqual(derive_aircraft_mode_code_from_real_state({
            "connection_status": "connected",
            "gps_fixed": 1,
            "armed": False,
        }), 2)
        self.assertEqual(derive_aircraft_mode_code_from_real_state({
            "connection_status": "connected",
            "gps_fixed": 1,
            "armed": True,
        }), 3)
        self.assertEqual(derive_aircraft_mode_code_from_real_state({
            "connection_status": "connected",
            "gps_fixed": 1,
            "armed": True,
        }, auto_takeoff_state="EXECUTING"), 4)
        self.assertEqual(derive_aircraft_mode_code_from_real_state({
            "connection_status": "connected",
            "gps_fixed": 1,
            "armed": True,
        }, mission_status="running", wayline_mission_state=6), 5)

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

    def test_osd_includes_aircraft_limit_fields_when_available(self):
        telemetry = new_telemetry_state()
        telemetry.update({
            "height_limit": 120,
            "distance_limit": 10000,
        })

        osd = build_osd_data(telemetry)

        self.assertEqual(osd["height_limit"], 120.0)
        self.assertEqual(osd["distance_limit"], 10000.0)

    def test_zero_battery_without_electrical_measurement_stays_unavailable(self):
        telemetry = new_telemetry_state()

        apply_mavlink_message(
            SimpleNamespace(
                msg_type="SYS_STATUS",
                voltage_battery=0,
                current_battery=0,
                battery_remaining=0,
            ),
            telemetry,
        )

        self.assertFalse(telemetry["battery_available"])
        self.assertEqual(telemetry["batteryRemaining"], 0)

    def test_zero_battery_with_valid_voltage_is_available_for_safety(self):
        telemetry = new_telemetry_state()

        apply_mavlink_message(
            SimpleNamespace(
                msg_type="SYS_STATUS",
                voltage_battery=14500,
                current_battery=0,
                battery_remaining=0,
            ),
            telemetry,
        )

        self.assertTrue(telemetry["battery_available"])
        self.assertEqual(telemetry["batteryRemaining"], 0)

    def test_osd_clamps_protocol_ranges_and_rejects_non_finite_values(self):
        telemetry = new_telemetry_state()
        telemetry.update({
            "latitude": 100.0,
            "longitude": -200.0,
            "yaw": float("nan"),
            "course": 725.0,
            "groundSpeed": -1.0,
            "batteryRemaining": 120,
            "gps_signal_level": 9,
        })

        osd = build_osd_data(telemetry)

        self.assertEqual(osd["latitude"], 90.0)
        self.assertEqual(osd["longitude"], -180.0)
        self.assertEqual(osd["attitude_head"], 0.0)
        self.assertEqual(osd["course"], 5.0)
        self.assertEqual(osd["horizontal_speed"], 0.0)
        self.assertEqual(osd["battery"]["capacity_percent"], 100)
        self.assertEqual(osd["position_state"]["quality"], 5)

    def test_aircraft_mode_does_not_override_dock_mode(self):
        properties = build_properties_from_osd({
            "mode_code": 9,
            "battery": {"capacity_percent": 80},
            "position_state": {
                "is_fixed": 2,
                "quality": 4,
                "gps_number": 16,
                "rtk_number": 12,
            },
        })

        self.assertNotIn("mode_code", properties)
        self.assertEqual(properties["position_state"]["is_fixed"], 2)
        self.assertEqual(properties["position_state"]["quality"], 4)

    def test_simulated_aircraft_position_populates_missing_dock3_position_fields(self):
        properties = build_properties_from_osd({
            "latitude": 43.817,
            "longitude": 125.3235,
            "height": 250.5,
            "attitude_head": 270.0,
            "position_state": {
                "is_fixed": 2,
                "quality": 10,
                "gps_number": 18,
                "rtk_number": 14,
            },
        })

        self.assertEqual(properties["heading"], -90.0)
        self.assertEqual(properties["home_position_is_valid"], 1)
        self.assertEqual(properties["position_state"]["quality"], 10)
        self.assertEqual(properties["self_converge_coordinate"], {
            "latitude": 43.817,
            "longitude": 125.3235,
            "height": 250.5,
        })

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
