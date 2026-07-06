#!/usr/bin/env python3
"""机巢 MAVLink 到上层 API 字段的流程测试。

只测试 MAVLink 模块解析和字段构造，不连接 MQTT。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.modules.drone_nest_module import DroneNestModule  # noqa: E402


class DroneNestModuleCloudApiFlowTest(unittest.TestCase):
    def setUp(self):
        self.module = DroneNestModule()
        self.results = {"passed": 0, "failed": 0}

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
            boolstate1=(1 << 0) | (1 << 1) | (1 << 2) | (1 << 4) | (1 << 6),
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
