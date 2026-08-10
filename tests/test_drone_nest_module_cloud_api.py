#!/usr/bin/env python3
"""机巢 MAVLink 到上层 API 字段的流程测试。

只测试 MAVLink 模块解析和字段构造，不连接 MQTT。
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if "pymavlink" not in sys.modules:
    pymavlink = ModuleType("pymavlink")
    pymavlink.mavutil = SimpleNamespace()
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = pymavlink.mavutil

from src.modules.drone_nest_module import (  # noqa: E402
    HEARTBEAT_PULSE_GRACE_SECONDS,
    DroneNestModule,
    nest_raw_logger,
)


class DroneNestModuleCloudApiFlowTest(unittest.TestCase):
    def setUp(self):
        self.module = DroneNestModule()
        self.results = {"passed": 0, "failed": 0}

    def _nest_response(self, boolstate1: int, boolstate2: int = 0):
        return SimpleNamespace(
            ac_temp=250,
            nest_temperature=260,
            env_temperature=240,
            wind_direction=1800,
            wind_speed=30,
            atmospheric_pressure=1013,
            rainfall=30,
            charge_volt=2600,
            charge_current=50,
            error_code=0,
            boolstate1=boolstate1,
            boolstate2=boolstate2,
            clamp_state=2,
            gate_state=1,
            landing_light_state=1,
        )

    def _step(self, title: str):
        print("\n" + "=" * 60)
        print(title)

    def _check_mapping(self, title: str, actual: dict, expected: dict):
        print(f"\n--- {title} ---")
        print(f"输出字段：{actual}")
        mismatch = {
            key: {"actual": actual.get(key), "expected": expected_value}
            for key, expected_value in expected.items()
            if actual.get(key) != expected_value
        }
        if mismatch:
            self.results["failed"] += 1
            print(f"失败字段：{mismatch}")
            self.fail(f"{title} 字段不符合预期")
        self.results["passed"] += 1
        print(f"通过：{title}")

    def tearDown(self):
        total = self.results["passed"] + self.results["failed"]
        if total:
            print("\n" + "=" * 60)
            print("测试结果总结")
            print(f"通过步骤：{self.results['passed']}")
            print(f"失败步骤：{self.results['failed']}")
            print(f"通过率：{self.results['passed'] / total * 100:.1f}%")

    def test_mavlink_to_cloud_api_fields_flow(self):
        print("机巢 MAVLink 到上层 API 字段流程测试开始")
        self.module.set_protocol_version("v1.7")

        self._step("测试步骤1：DRONE_NEST_RESPONSE 主动上报字段构造")
        nest_response = SimpleNamespace(
            ac_temp=250,
            nest_temperature=260,
            env_temperature=240,
            wind_direction=1800,
            wind_speed=30,
            atmospheric_pressure=1013,
            rainfall=30,
            charge_volt=2600,
            charge_current=50,
            error_code=0,
            boolstate1=(1 << 0) | (1 << 1) | (1 << 2) | (1 << 4) | (1 << 5) | (1 << 7),
            boolstate2=(1 << 0) | (1 << 5),
            clamp_state=2,
            gate_state=1,
            landing_light_state=1,
        )
        self.module._handle_nest_response(nest_response)
        self._check_mapping(
            "主动上报转换为上层 API 属性字段",
            self.module.get_cloud_properties(),
            {
                "environment_temperature": 24.0,
                "temperature": 26.0,
                "nest_temperature": 26.0,
                "wind_speed": 3.0,
                "rainfall": 2,
                "working_voltage": 26000,
                "working_current": 500.0,
                "emergency_stop_state": 0,
                "charging_state": 1,
                "drone_charge_state": {"state": 1},
                "cover_state": 1,
                "putter_state": 0,
                "supplement_light_state": 1,
                "air_conditioner": {
                    "air_conditioner_state": 1,
                    "switch_time": 0,
                },
            },
        )

        self._step("测试步骤2：DRONE_NEST_ORDER_RESPONSE 成功执行回复字段构造")
        success_reply = SimpleNamespace(
            ordinary_order_response=0,
            takeoff_land_order_response=0,
        )
        self.module._handle_order_response(success_reply)
        success_fields = self.module.get_cloud_order_response()
        self._check_mapping(
            "成功执行回复转换为上层 API 结果字段",
            {
                "result": success_fields.get("result"),
                "status": success_fields.get("status"),
                "ordinary_raw": success_fields.get("ordinary_order_response", {}).get("raw"),
                "takeoff_raw": success_fields.get("takeoff_land_order_response", {}).get("raw"),
                "rejections": success_fields.get("rejections"),
            },
            {
                "result": 0,
                "status": "success",
                "ordinary_raw": 0,
                "takeoff_raw": 0,
                "rejections": [],
            },
        )

        self._step("测试步骤3：DRONE_NEST_ORDER_RESPONSE 拒绝执行回复字段构造")
        rejected_reply = SimpleNamespace(
            ordinary_order_response=(0x11 << 24) | (0x01 << 16) | (0x02 << 8) | 0x01,
            takeoff_land_order_response=0,
        )
        self.module._handle_order_response(rejected_reply)
        rejected_fields = self.module.get_cloud_order_response()
        ordinary = rejected_fields.get("ordinary_order_response", {})
        first_rejection = (rejected_fields.get("rejections") or [{}])[0]
        self._check_mapping(
            "拒绝执行回复转换为上层 API 结果字段",
            {
                "result": rejected_fields.get("result"),
                "status": rejected_fields.get("status"),
                "reply_status": ordinary.get("reply_status"),
                "reject_reason_code": ordinary.get("reject_reason_code"),
                "command_code": ordinary.get("command_code"),
                "device_code": ordinary.get("device_code"),
                "reason": first_rejection.get("reason"),
            },
            {
                "result": 2,
                "status": "rejected",
                "reply_status": 1,
                "reject_reason_code": 2,
                "command_code": 1,
                "device_code": 17,
                "reason": "PLC超时",
            },
        )

    def test_v17_heartbeat_zero_after_toggle_stays_online_for_all_pulse_bits(self):
        self.module.set_protocol_version("v1.7")

        self.module._handle_nest_response(self._nest_response(0))
        self.assertTrue(self.module.environment_data["nest_heartbeat"])
        self.assertTrue(self.module.environment_data["drone_heartbeat"])
        self.assertTrue(self.module.environment_data["plc_heartbeat"])
        self.assertFalse(self.module.environment_data["nest_heartbeat_raw"])
        self.assertFalse(self.module.environment_data["drone_heartbeat_raw"])
        self.assertFalse(self.module.environment_data["plc_heartbeat_raw"])

        heartbeat_bits = (1 << 0) | (1 << 1) | (1 << 2)
        self.module._handle_nest_response(self._nest_response(heartbeat_bits))
        self.assertTrue(self.module.environment_data["nest_heartbeat"])
        self.assertTrue(self.module.environment_data["drone_heartbeat"])
        self.assertTrue(self.module.environment_data["plc_heartbeat"])
        self.assertTrue(self.module.environment_data["nest_heartbeat_raw"])
        self.assertTrue(self.module.environment_data["drone_heartbeat_raw"])
        self.assertTrue(self.module.environment_data["plc_heartbeat_raw"])
        self.assertEqual(self.module.environment_data["nest_heartbeat_toggle_count"], 1)
        self.assertEqual(self.module.environment_data["drone_heartbeat_toggle_count"], 1)
        self.assertEqual(self.module.environment_data["plc_heartbeat_toggle_count"], 1)

        self.module._handle_nest_response(self._nest_response(0))
        self.assertTrue(self.module.environment_data["nest_heartbeat"])
        self.assertTrue(self.module.environment_data["drone_heartbeat"])
        self.assertTrue(self.module.environment_data["plc_heartbeat"])
        self.assertFalse(self.module.environment_data["nest_heartbeat_raw"])
        self.assertFalse(self.module.environment_data["drone_heartbeat_raw"])
        self.assertFalse(self.module.environment_data["plc_heartbeat_raw"])
        self.assertEqual(self.module.environment_data["nest_heartbeat_toggle_count"], 2)
        self.assertEqual(self.module.environment_data["drone_heartbeat_toggle_count"], 2)
        self.assertEqual(self.module.environment_data["plc_heartbeat_toggle_count"], 2)

        stale_toggle = time.time() - HEARTBEAT_PULSE_GRACE_SECONDS - 1
        self.module.environment_data["nest_heartbeat_last_toggle"] = stale_toggle
        self.module.environment_data["drone_heartbeat_last_toggle"] = stale_toggle
        self.module.environment_data["plc_heartbeat_last_toggle"] = stale_toggle
        self.module._handle_nest_response(self._nest_response(0))
        self.assertFalse(self.module.environment_data["nest_heartbeat"])
        self.assertFalse(self.module.environment_data["drone_heartbeat"])
        self.assertFalse(self.module.environment_data["plc_heartbeat"])

    def test_nest_raw_records_are_console_silent(self):
        with patch.object(nest_raw_logger, "info") as info:
            self.module._handle_nest_response(self._nest_response(0))

        self.assertTrue(info.call_args.kwargs["extra"]["console_silent"])

    def test_order_response_maps_resident_mode_rejection_reason(self):
        rejected_reply = SimpleNamespace(
            ordinary_order_response=0x0B010524,
            takeoff_land_order_response=0,
        )

        self.module._handle_order_response(rejected_reply)
        rejected_fields = self.module.get_cloud_order_response()
        ordinary = rejected_fields.get("ordinary_order_response", {})
        first_rejection = (rejected_fields.get("rejections") or [{}])[0]

        self.assertEqual(rejected_fields.get("status"), "rejected")
        self.assertEqual(rejected_fields.get("result"), 5)
        self.assertEqual(ordinary.get("reply_status"), 0x24)
        self.assertEqual(ordinary.get("reject_reason_code"), 5)
        self.assertEqual(ordinary.get("command_code"), 0x01)
        self.assertEqual(ordinary.get("device_code"), 0x0B)
        self.assertEqual(first_rejection.get("reason"), "驻留模式中-拒绝其他命令")

    def test_default_protocol_uses_v17_ready_bits(self):
        nest_response = SimpleNamespace(
            ac_temp=250,
            nest_temperature=260,
            env_temperature=240,
            wind_direction=1800,
            wind_speed=30,
            atmospheric_pressure=1013,
            rainfall=0,
            charge_volt=2600,
            charge_current=0,
            error_code=0,
            boolstate1=(1 << 0) | (1 << 1) | (1 << 2) | (1 << 7),
            boolstate2=(1 << 5),
            clamp_state=2,
            gate_state=2,
            landing_light_state=2,
        )

        self.module._handle_nest_response(nest_response)
        env = self.module.get_environment_data()

        self.assertEqual(self.module.protocol_version, "v1.7")
        self.assertTrue(env["ready_state"])
        self.assertFalse(env["initializing_state"])

    def test_disconnected_nest_link_skips_command_send(self):
        connection = SimpleNamespace(
            is_connected=lambda: False,
            get_connection_info=lambda: {"disconnect_reason": "TCP peer closed connection"},
            master=SimpleNamespace(mav=SimpleNamespace()),
        )
        self.module.dispatcher = SimpleNamespace(connection=connection)

        self.assertFalse(self.module.send_gcs_order_response(0, 0))

    def test_wait_for_order_response_returns_matching_12005_rejection(self):
        sequence = self.module.order_response_sequence()
        rejected_reply = SimpleNamespace(
            ordinary_order_response=(0x11 << 24) | (0x01 << 16) | (0x02 << 8) | 0x01,
            takeoff_land_order_response=0,
        )

        self.module._handle_order_response(rejected_reply)
        response = self.module.wait_for_order_response(
            after_sequence=sequence,
            expected_ordinary_order=0x1101,
            timeout=0.1,
        )

        self.assertIsNotNone(response)
        self.assertEqual(response["status"], "rejected")
        self.assertEqual(response["result"], 2)

    def test_wait_for_order_response_treats_empty_success_as_command_ack(self):
        sequence = self.module.order_response_sequence()
        success_reply = SimpleNamespace(
            ordinary_order_response=0,
            takeoff_land_order_response=0,
        )

        self.module._handle_order_response(success_reply)
        response = self.module.wait_for_order_response(
            after_sequence=sequence,
            expected_ordinary_order=0x1101,
            timeout=0.1,
        )

        self.assertIsNotNone(response)
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["result"], 0)

    def test_status_poll_can_be_disabled_for_tcp_links(self):
        calls = []
        self.module.status_poll_enabled = False
        self.module._last_status_poll_time = 0
        self.module.poll_nest_status = lambda: calls.append("poll")

        self.module.idle_task(SimpleNamespace())

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
