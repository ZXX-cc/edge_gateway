#!/usr/bin/env python3
"""DeviceManager nest state/HMS synchronization tests."""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if "src.database.connection" not in sys.modules:
    connection = ModuleType("src.database.connection")

    async def init_database_async():
        return None

    def get_async_session_local():
        return None

    def is_mongo_session(_session):
        return False

    def mongo_expired_filter(*_args, **_kwargs):
        return {}

    def mongo_model_from_document(_model, document):
        return document

    connection.init_database_async = init_database_async
    connection.get_async_session_local = get_async_session_local
    connection.is_mongo_session = is_mongo_session
    connection.mongo_expired_filter = mongo_expired_filter
    connection.mongo_model_from_document = mongo_model_from_document
    sys.modules["src.database.connection"] = connection

if "src.database.device_db" not in sys.modules:
    device_db = ModuleType("src.database.device_db")
    device_db.DeviceDB = type("DeviceDB", (), {})
    sys.modules["src.database.device_db"] = device_db

if "src.database.alarm_db" not in sys.modules:
    alarm_db = ModuleType("src.database.alarm_db")
    alarm_db.AlarmDB = type("AlarmDB", (), {})
    sys.modules["src.database.alarm_db"] = alarm_db

if "src.database.operation_db" not in sys.modules:
    operation_db = ModuleType("src.database.operation_db")
    operation_db.OperationDB = type("OperationDB", (), {})
    sys.modules["src.database.operation_db"] = operation_db

from src.device.manager import DeviceManager  # noqa: E402
from src.device.nest_hms_mapper import build_nest_hms_alarms, is_nest_hms_alarm  # noqa: E402
from src.utils.dji_error_codes import DEVICE_COMMAND_TIMEOUT, DOCK_UNABLE_TO_RESPOND  # noqa: E402


class FakeNestModule:
    def __init__(self, send_result=True, ack=None, auto_ack=True):
        self.send_result = send_result
        self.ack = ack if ack is not None else {"result": 0, "status": "success", "rejections": []}
        self.auto_ack = auto_ack
        self.commands = []
        self._condition = threading.Condition()
        self._sequence = 0

    def order_response_sequence(self):
        with self._condition:
            return self._sequence

    def send_gcs_command(self, command):
        self.commands.append(command)
        if self.send_result is False:
            return False
        if not self.auto_ack:
            return True
        with self._condition:
            self._sequence += 1
            self._condition.notify_all()
        return True

    def wait_for_order_response(self, after_sequence, **_kwargs):
        timeout = float(_kwargs.get("timeout", 0.1) or 0.1)
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._sequence <= after_sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return dict(self.ack)

    def get_cloud_order_response(self):
        return dict(self.ack)


def nest_snapshot(**overrides):
    data = {
        "data_valid": True,
        "ac_temp": 25.0,
        "nest_temperature": 26.0,
        "env_temperature": 24.0,
        "wind_speed": 3.0,
        "rainfall": 0.0,
        "charge_volt": 26.0,
        "charge_current": 0.5,
        "error_code": 0,
        "nest_heartbeat": True,
        "drone_heartbeat": True,
        "plc_heartbeat": True,
        "estop_state": False,
        "charging_state": True,
        "ready_state": True,
        "system_busy": False,
        "fault_state": False,
        "r_axis_zero": True,
        "takeoff_in_progress": False,
        "landing_in_progress": False,
        "emergency_landing": False,
        "takeoff_allowed": True,
        "clamp_state": 2,
        "gate_state": 1,
        "landing_light_state": 1,
    }
    data.update(overrides)
    return data


class DeviceManagerNestSyncTest(unittest.TestCase):
    def setUp(self):
        self.manager = DeviceManager()
        self.manager._nest_command_ack_timeout = 0.1
        self.manager._nest_command_state_timeout = 0.4
        self.state_events = []
        self.events = []
        self.manager._state_callback = self.state_events.append
        self.manager._event_callback = self.events.append

    @staticmethod
    def _cover_msg(method="cover_open"):
        return {
            "tid": f"tid-{method}",
            "bid": f"bid-{method}",
            "gateway": "EDGEGW001",
            "method": method,
            "data": {},
        }

    def test_nest_status_change_updates_osd_properties_and_hms_only(self):
        with (
            patch.object(self.manager, "_save_alarm_to_db", new=lambda *args, **kwargs: None),
            patch.object(self.manager, "_clear_alarms_from_db", new=lambda *args, **kwargs: None),
            patch.object(self.manager, "_schedule_async"),
        ):
            self.manager.update_from_nest(nest_snapshot())

            self.assertEqual(self.state_events, [])
            self.assertEqual(self.manager.get_property("cover_state"), 1)
            self.assertEqual(self.manager.get_property("charging_state"), 1)
            self.assertEqual(self.manager.get_property("drone_charge_state")["state"], 1)
            self.assertFalse([event for event in self.events if event["method"] == "hms"])

            self.manager.update_from_nest(nest_snapshot(
                estop_state=True,
                fault_state=True,
                error_code=12004,
                emergency_landing=True,
                nest_heartbeat=False,
            ))

            hms_events = [event for event in self.events if event["method"] == "hms"]
            self.assertTrue(hms_events)
            hms_alarms = hms_events[-1]["data"]["alarms"]
            hms_keys = {alarm["hms_key"] for alarm in hms_alarms}
            self.assertIn("dock3.nest.estop.active", hms_keys)
            self.assertIn("dock3.nest.fault.active", hms_keys)
            self.assertIn("dock3.nest.emergency_landing.active", hms_keys)
            self.assertIn("dock3.nest.error_code.active", hms_keys)
            self.assertIn("dock3.nest.nest_heartbeat.lost", hms_keys)

            non_standard_operation_events = [
                event for event in self.events
                if event["method"] == "dock_operation"
            ]
            self.assertFalse(non_standard_operation_events)

            self.manager.update_from_nest(nest_snapshot())
            cleared_hms = [event for event in self.events if event["method"] == "hms"][-1]
            self.assertEqual(cleared_hms["data"]["total"], 0)
            self.assertFalse([alarm for alarm in self.manager._hms_alarms if is_nest_hms_alarm(alarm)])

    def test_state_updates_follow_official_dock3_push_mode(self):
        updates = self.manager._extract_state_updates({
            "cover_state": 1,
            "alarm_state": 1,
            "drone_charge_state": {"state": 1, "capacity_percent": 90},
            "silent_mode": 1,
            "acc_time": 10,
            "wireless_link_topo": {"leaf_nodes": []},
        })

        self.assertEqual(set(updates), {"silent_mode", "acc_time", "wireless_link_topo"})

    def test_build_nest_hms_alarms_uses_stable_ids(self):
        alarms = build_nest_hms_alarms(
            nest_snapshot(fault_state=True, error_code=7, plc_heartbeat=False),
            "EDGEGW001",
        )
        by_key = {alarm["hms_key"]: alarm for alarm in alarms}
        self.assertEqual(by_key["dock3.nest.fault.active"]["alarm_id"], "nest-fault-active-7")
        self.assertEqual(by_key["dock3.nest.error_code.active"]["alarm_id"], "nest-error-code-7")
        self.assertEqual(by_key["dock3.nest.plc_heartbeat.lost"]["alarm_id"], "nest-plc-heartbeat-lost")
        self.assertTrue(all(is_nest_hms_alarm(alarm) for alarm in alarms))

    def test_startup_drone_heartbeat_false_does_not_report_hms_until_seen_online(self):
        with (
            patch.object(self.manager, "_save_alarm_to_db", new=lambda *args, **kwargs: None),
            patch.object(self.manager, "_clear_alarms_from_db", new=lambda *args, **kwargs: None),
            patch.object(self.manager, "_schedule_async"),
        ):
            self.manager.update_from_nest(nest_snapshot(drone_heartbeat=False))

            hms_events = [event for event in self.events if event["method"] == "hms"]
            self.assertFalse(hms_events)
            self.assertFalse(self.manager._nest_drone_heartbeat_alarm_enabled)

            self.manager.update_from_nest(nest_snapshot(drone_heartbeat=True))
            self.assertTrue(self.manager._nest_drone_heartbeat_alarm_enabled)

            self.manager.update_from_nest(nest_snapshot(drone_heartbeat=False))
            hms_events = [event for event in self.events if event["method"] == "hms"]
            self.assertTrue(hms_events)
            hms_keys = {
                alarm["hms_key"]
                for alarm in hms_events[-1]["data"]["alarms"]
            }
            self.assertIn("dock3.nest.drone_heartbeat.lost", hms_keys)

    def test_startup_nest_and_plc_heartbeat_false_do_not_report_hms_until_seen_online(self):
        with (
            patch.object(self.manager, "_save_alarm_to_db", new=lambda *args, **kwargs: None),
            patch.object(self.manager, "_clear_alarms_from_db", new=lambda *args, **kwargs: None),
            patch.object(self.manager, "_schedule_async"),
        ):
            self.manager.update_from_nest(nest_snapshot(
                nest_heartbeat=False,
                plc_heartbeat=False,
                drone_heartbeat=False,
            ))

            hms_events = [event for event in self.events if event["method"] == "hms"]
            self.assertFalse(hms_events)
            self.assertFalse(self.manager._nest_heartbeat_alarm_enabled)
            self.assertFalse(self.manager._nest_plc_heartbeat_alarm_enabled)

            self.manager.update_from_nest(nest_snapshot(
                nest_heartbeat=True,
                plc_heartbeat=True,
                drone_heartbeat=True,
            ))
            self.assertTrue(self.manager._nest_heartbeat_alarm_enabled)
            self.assertTrue(self.manager._nest_plc_heartbeat_alarm_enabled)

            self.manager.update_from_nest(nest_snapshot(
                nest_heartbeat=False,
                plc_heartbeat=False,
                drone_heartbeat=True,
            ))
            hms_events = [event for event in self.events if event["method"] == "hms"]
            self.assertTrue(hms_events)
            hms_keys = {
                alarm["hms_key"]
                for alarm in hms_events[-1]["data"]["alarms"]
            }
            self.assertIn("dock3.nest.nest_heartbeat.lost", hms_keys)
            self.assertIn("dock3.nest.plc_heartbeat.lost", hms_keys)

    def test_recent_nest_command_target_ignores_stale_nest_snapshot(self):
        self.manager._nest_module = FakeNestModule()
        self.manager.update_from_nest(nest_snapshot(gate_state=1))
        self.assertEqual(self.manager.get_property("cover_state"), 1)

        result = {}
        worker = threading.Thread(
            target=lambda: result.setdefault("reply", self.manager.handle_cover_close(self._cover_msg("cover_close")))
        )
        worker.start()
        time.sleep(0.05)

        self.assertTrue(self.manager._nest_module.commands)
        self.assertEqual(self.manager.get_property("cover_state"), 1)
        self.assertIn("cover_state", self.manager._pending_nest_command_targets)
        self.assertNotIn("reply", result)

        self.manager.update_from_nest(nest_snapshot(gate_state=1))
        self.assertEqual(self.manager.get_property("cover_state"), 1)
        self.assertIn("cover_state", self.manager._pending_nest_command_targets)
        self.assertNotIn("reply", result)

        self.manager.update_from_nest(nest_snapshot(gate_state=0))
        worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result["reply"]["data"]["result"], 0)
        self.assertEqual(result["reply"]["data"]["output"]["status"], "ok")
        self.assertEqual(self.manager.get_property("cover_state"), 0)
        self.assertNotIn("cover_state", self.manager._pending_nest_command_targets)

    def test_cover_open_reports_failure_when_nest_send_fails(self):
        self.manager._nest_module = FakeNestModule(send_result=False)

        reply = self.manager.handle_cover_open(self._cover_msg())

        self.assertNotEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["status"], "failed")
        self.assertNotIn("cover_state", self.manager._pending_nest_command_targets)

    def test_cover_open_reports_rejected_when_12005_rejects(self):
        self.manager._nest_module = FakeNestModule(ack={
            "result": 2,
            "status": "rejected",
            "rejections": [{"reason_code": 2, "reason": "PLC超时"}],
        })

        reply = self.manager.handle_cover_open(self._cover_msg())

        self.assertEqual(reply["data"]["result"], DOCK_UNABLE_TO_RESPOND)
        self.assertEqual(reply["data"]["output"]["status"], "rejected")
        self.assertIn("PLC超时", reply.get("msg", ""))
        self.assertNotIn("ack", reply["data"])
        self.assertNotIn("action", reply["data"])
        self.assertNotIn("cover_state", self.manager._pending_nest_command_targets)

    def test_cover_open_times_out_when_12005_ack_missing(self):
        self.manager._nest_command_ack_timeout = 0.05
        self.manager._nest_module = FakeNestModule(auto_ack=False)

        reply = self.manager.handle_cover_open(self._cover_msg())

        self.assertEqual(reply["data"]["result"], DEVICE_COMMAND_TIMEOUT)
        self.assertEqual(reply["data"]["output"]["status"], "timeout")
        self.assertIn("timeout waiting for 12005 ACK", reply.get("msg", ""))
        self.assertNotIn("ack", reply["data"])
        self.assertNotIn("action", reply["data"])
        self.assertNotIn("cover_state", self.manager._pending_nest_command_targets)

    def test_cover_open_times_out_when_12004_target_state_never_arrives(self):
        self.manager._nest_command_state_timeout = 0.05
        self.manager._nest_module = FakeNestModule()
        self.manager.update_from_nest(nest_snapshot(gate_state=0))

        reply = self.manager.handle_cover_open(self._cover_msg())

        self.assertEqual(reply["data"]["result"], DEVICE_COMMAND_TIMEOUT)
        self.assertEqual(reply["data"]["output"]["status"], "timeout")
        self.assertIn("timeout waiting for cover_state=1 from 12004", reply.get("msg", ""))
        self.assertNotIn("ack", reply["data"])
        self.assertNotIn("action", reply["data"])
        self.assertEqual(self.manager.get_property("cover_state"), 0)
        self.assertNotIn("cover_state", self.manager._pending_nest_command_targets)

    def test_cover_open_non_strict_mode_returns_ok_after_12005_when_12004_target_state_never_arrives(self):
        self.manager._nest_command_require_final_state = False
        self.manager._nest_command_state_timeout = 0.05
        self.manager._nest_module = FakeNestModule()
        self.manager.update_from_nest(nest_snapshot(gate_state=0))

        reply = self.manager.handle_cover_open(self._cover_msg())

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["status"], "ok")
        self.assertNotIn("ack", reply["data"])
        self.assertNotIn("action", reply["data"])
        self.assertEqual(self.manager.get_property("cover_state"), 0)
        self.assertIn("cover_state", self.manager._pending_nest_command_targets)

    def test_cover_open_returns_ok_only_after_12005_and_12004_target_state(self):
        self.manager._nest_module = FakeNestModule()
        self.manager.update_from_nest(nest_snapshot(gate_state=0))

        result = {}
        worker = threading.Thread(
            target=lambda: result.setdefault("reply", self.manager.handle_cover_open(self._cover_msg()))
        )
        worker.start()
        time.sleep(0.05)
        self.assertNotIn("reply", result)

        self.manager.update_from_nest(nest_snapshot(gate_state=1))
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        reply = result["reply"]
        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["status"], "ok")
        self.assertNotIn("ack", reply["data"])
        self.assertNotIn("action", reply["data"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
