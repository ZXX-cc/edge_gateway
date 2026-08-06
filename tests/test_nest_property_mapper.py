#!/usr/bin/env python3
"""机巢属性映射流程测试。

风格保持和 tests/test3.py / web/mavlink/nest_flow_test.py 一致：
按业务步骤测试，每一步覆盖一组字段，而不是把每个属性拆成独立测试用例。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.device.nest_property_mapper import (  # noqa: E402
    filter_dock_status_fields,
    map_clamp_state,
    map_gate_state,
    map_nest_to_cloud_properties,
    map_rainfall,
)
from web.mavlink.nest.property_mapping import (  # noqa: E402
    build_dock3_properties_from_nest,
    response_to_nest_state,
)


def build_nest_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "data_valid": True,
        "ac_temp": 25.0,
        "nest_temperature": 26.0,
        "env_temperature": 24.0,
        "wind_speed": 3.0,
        "rainfall": 3.0,
        "charge_volt": 26.0,
        "charge_current": 0.5,
        "estop_state": False,
        "charging_state": True,
        "gate_state": 1,
        "clamp_state": 2,
        "landing_light_state": 1,
    }
    data.update(overrides)
    return data


class NestPropertyMapperFlowTest(unittest.TestCase):
    def setUp(self):
        self.results = {"passed": 0, "failed": 0}

    def _step(self, title: str):
        print("\n" + "=" * 60)
        print(title)

    def _pass(self, title: str):
        self.results["passed"] += 1
        print(f"通过：{title}")

    def _check_mapping(self, title: str, actual: dict[str, Any], expected: dict[str, Any]):
        print(f"\n--- {title} ---")
        print(f"输入映射结果：{actual}")
        missing_or_wrong = {
            key: {"actual": actual.get(key), "expected": expected_value}
            for key, expected_value in expected.items()
            if actual.get(key) != expected_value
        }
        if missing_or_wrong:
            self.results["failed"] += 1
            print(f"失败字段：{missing_or_wrong}")
            self.fail(f"{title} 映射不符合预期")
        self._pass(title)

    def tearDown(self):
        total = self.results["passed"] + self.results["failed"]
        if total:
            pass_rate = self.results["passed"] / total * 100
            print("\n" + "=" * 60)
            print("测试结果总结")
            print(f"通过步骤：{self.results['passed']}")
            print(f"失败步骤：{self.results['failed']}")
            print(f"通过率：{pass_rate:.1f}%")

    def test_nest_property_mapper_flow(self):
        print("机巢属性映射流程测试开始")

        self._step("测试步骤1：基础机巢状态映射")
        base_updates = map_nest_to_cloud_properties(build_nest_data())
        self._check_mapping(
            "环境、电气、机构、急停、充电、灯光、空调字段",
            base_updates,
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

        self._step("测试步骤2：状态枚举边界映射")
        rainfall_result = [map_rainfall(value) for value in (-1.0, 0.0, 1.0, 2.5, 7.9, 8.0)]
        gate_result = [map_gate_state(value) for value in (0, 1, 2, 3, 4, 5, 6)]
        clamp_result = [map_clamp_state(value) for value in (0, 1, 2, 3, 4, 5, 6)]
        boundary_actual = {
            "rainfall": rainfall_result,
            "gate_state": gate_result,
            "clamp_state": clamp_result,
        }
        self._check_mapping(
            "降雨、舱门、推杆边界值",
            boundary_actual,
            {
                "rainfall": [0, 0, 1, 2, 2, 3],
                "gate_state": [0, 1, 0, 2, 2, 2, 3],
                "clamp_state": [0, 1, 0, 2, 2, 2, 3],
            },
        )

        self._step("测试步骤3：无效数据和可选字段映射")
        invalid_updates = map_nest_to_cloud_properties({"data_valid": False})
        optional_updates = map_nest_to_cloud_properties(
            build_nest_data(
                humidity=58.5,
                electric_supply_voltage=220000,
                estop_state=True,
                charging_state=False,
                landing_light_state=6,
                ac_temp=0.0,
            )
        )
        self._check_mapping(
            "无效数据、湿度、供电、急停、充电关闭、灯光故障、空调关闭",
            {
                "invalid_updates": invalid_updates,
                "humidity": optional_updates.get("humidity"),
                "electric_supply_voltage": optional_updates.get("electric_supply_voltage"),
                "emergency_stop_state": optional_updates.get("emergency_stop_state"),
                "charging_state": optional_updates.get("charging_state"),
                "supplement_light_state": optional_updates.get("supplement_light_state"),
                "air_conditioner": optional_updates.get("air_conditioner"),
            },
            {
                "invalid_updates": {},
                "humidity": 58.5,
                "electric_supply_voltage": 220000,
                "emergency_stop_state": 1,
                "charging_state": 0,
                "supplement_light_state": 0,
                "air_conditioner": {
                    "air_conditioner_state": 0,
                    "switch_time": 0,
                },
            },
        )

        self._step("测试步骤4：DRONE_NEST_RESPONSE 位域展开")
        response_state = response_to_nest_state(
            type(
                "NestResponse",
                (),
                {
                    "ac_temp": 250,
                    "nest_temperature": 260,
                    "env_temperature": 240,
                    "wind_direction": 1800,
                    "wind_speed": 30,
                    "atmospheric_pressure": 1013,
                    "rainfall": 30,
                    "charge_volt": 2600,
                    "charge_current": 50,
                    "error_code": 0,
                    "boolstate1": (
                        (1 << 0)
                        | (1 << 1)
                        | (1 << 2)
                        | (1 << 4)
                        | (1 << 5)
                        | (1 << 7)
                        | (1 << 8)
                        | (1 << 9)
                        | (1 << 10)
                        | (1 << 11)
                        | (1 << 12)
                    ),
                    "boolstate2": (1 << 0) | (1 << 2) | (1 << 4) | (1 << 5),
                    "clamp_state": 2,
                    "gate_state": 1,
                    "landing_light_state": 1,
                },
            )()
        )
        self._check_mapping(
            "心跳、充电、初始化、busy、故障、R轴、大灯、驻留、起飞流程、终止流程、备降、允许起飞状态",
            response_state,
            {
                "nest_heartbeat": True,
                "drone_heartbeat": True,
                "plc_heartbeat": True,
                "charging_state": True,
                "calibrate_state": True,
                "initializing_state": False,
                "ready_state": True,
                "init_state": True,
                "system_busy": True,
                "fault_state": True,
                "r_axis_zero": True,
                "headlight_off": True,
                "resident_mode": True,
                "takeoff_in_progress": True,
                "landing_in_progress": False,
                "takeoff_abort_progress": True,
                "landing_abort_progress": False,
                "alternate_landing_state": True,
                "emergency_landing": True,
                "takeoff_allowed": True,
            },
        )

        self._check_mapping(
            "故障状态位映射为告警",
            map_nest_to_cloud_properties(response_state),
            {
                "alarm_state": 1,
            },
        )

        self._step("测试步骤5：真实机巢位置字段映射")
        dock_position = build_dock3_properties_from_nest(
            build_nest_data(
                latitude=43.817,
                longitude=125.3235,
                height=250.5,
                heading=270.0,
                position_state={
                    "is_calibration": 1,
                    "is_fixed": 2,
                    "quality": 10,
                    "gps_number": 18,
                    "rtk_number": 14,
                },
            )
        )
        self._check_mapping(
            "机场固定坐标、自收敛坐标、航向和定位状态",
            {
                "latitude": dock_position.get("latitude"),
                "longitude": dock_position.get("longitude"),
                "height": dock_position.get("height"),
                "heading": dock_position.get("heading"),
                "home_position_is_valid": dock_position.get("home_position_is_valid"),
                "self_converge_coordinate": dock_position.get("self_converge_coordinate"),
                "position_state": dock_position.get("position_state"),
            },
            {
                "latitude": 43.817,
                "longitude": 125.3235,
                "height": 250.5,
                "heading": -90.0,
                "home_position_is_valid": 1,
                "self_converge_coordinate": {
                    "longitude": 125.3235,
                    "latitude": 43.817,
                    "height": 250.5,
                },
                "position_state": {
                    "is_calibration": 1,
                    "is_fixed": 2,
                    "quality": 10,
                    "gps_number": 18,
                    "rtk_number": 14,
                },
            },
        )

        self._step("测试步骤6：Dock 状态快照字段过滤")
        dock_updates = filter_dock_status_fields(
            {
                "cover_state": 1,
                "putter_state": 0,
                "charging_state": 1,
                "environment_temperature": 24.0,
                "nest_temperature": 26.0,
                "wind_speed": 3.0,
                "rainfall": 2,
                "humidity": 58.5,
                "supplement_light_state": 1,
                "alarm_state": 0,
                "working_voltage": 26000,
                "working_current": 500.0,
                "air_conditioner": {"air_conditioner_state": 1},
            }
        )
        self._check_mapping(
            "只保留 DockManager 支持的状态字段",
            dock_updates,
            {
                "cover_state": 1,
                "putter_state": 0,
                "charging_state": 1,
                "environment_temperature": 24.0,
                "nest_temperature": 26.0,
                "wind_speed": 3.0,
                "rainfall": 2,
                "humidity": 58.5,
                "supplement_light_state": 1,
                "alarm_state": 0,
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
