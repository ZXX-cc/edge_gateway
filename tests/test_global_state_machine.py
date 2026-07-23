import unittest

from src.state_machine.command_admission import CommandAdmissionEngine
from src.state_machine.global_state import GatewayStateProvider, GatewayStateSnapshot


class StaticReducer:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


class ReadyNest:
    def get_environment_data(self):
        return {
            "data_valid": True,
            "nest_heartbeat": True,
            "plc_heartbeat": True,
            "takeoff_allowed": True,
            "estop_state": False,
            "fault_state": False,
            "system_busy": False,
            "error_code": 0,
        }

    def get_uav_nest_status(self):
        return {
            "data_valid": True,
            "drone_heartbeat": True,
        }


def snapshot(**overrides):
    data = {
        "gateway_phase": "READY",
        "cloud": "online",
        "device": "online",
        "dock": "ready",
        "aircraft": "online",
        "mission": "idle",
        "drc": "idle",
        "ota": "idle",
        "safety": "normal",
    }
    data.update(overrides)
    return GatewayStateSnapshot(**data)


class GlobalStateMachineAdmissionTest(unittest.TestCase):
    def check(self, method, state, accepted, reason="", data=None):
        engine = CommandAdmissionEngine(StaticReducer(state))
        decision = engine.check(
            method,
            {"method": method, "need_reply": 1, "data": data or {}},
            "thing/product/EDGEGW001/services",
        )
        self.assertEqual(decision.accepted, accepted)
        if reason:
            self.assertEqual(decision.reason, reason)

    def test_unbound_rejects_flight_task(self):
        self.check("flighttask_execute", snapshot(device="unbound"), False, "device_unbound")

    def test_aircraft_offline_rejects_drc_enter(self):
        self.check("drc_mode_enter", snapshot(aircraft="offline"), False, "aircraft_offline")

    def test_emergency_stop_blocks_normal_dock_control_but_allows_hms_clear(self):
        estop = snapshot(dock="emergency_stop", safety="emergency_stop")
        self.check("cover_open", estop, False, "emergency_stop_active")
        self.check("hms_clear", estop, True)

    def test_running_mission_blocks_ota(self):
        self.check("ota_create", snapshot(mission="running"), False, "mission_busy")

    def test_drc_active_blocks_new_mission(self):
        self.check("flighttask_execute", snapshot(drc="active"), False, "drc_active")

    def test_services_payload_screen_controls_do_not_require_drc_active(self):
        idle = snapshot(drc="idle")
        self.check("camera_screen_drag", idle, True)
        self.check("camera_aim", idle, True)
        self.check("camera_screen_split", idle, True)
        self.check("drc_gimbal_speed_push", idle, False, "invalid_state_transition")

    def test_prepare_upload_allows_online_dock_but_execute_still_requires_ready(self):
        self.check("flighttask_prepare", snapshot(dock="online"), True)
        self.check("flighttask_execute", snapshot(dock="online"), False, "dock_not_ready")

    def test_provider_marks_dock_ready_when_nest_allows_takeoff(self):
        provider = GatewayStateProvider(nest=ReadyNest())

        state = provider.snapshot()

        self.assertEqual(state.dock, "ready")

    def test_drc_point_flight_can_enter_drc_automatically_but_still_requires_aircraft_online(self):
        self.check("takeoff_to_point", snapshot(drc="idle"), True)
        self.check("fly_to_point_update", snapshot(drc="idle"), True)
        self.check("fly_to_point_update", snapshot(drc="idle", aircraft="offline"), False, "aircraft_offline")
        self.check("fly_to_point_stop", snapshot(drc="idle"), False, "invalid_state_transition")
        self.check("takeoff_to_point", snapshot(drc="active"), True)
        self.check("fly_to_point_update", snapshot(drc="active"), True)

    def test_resume_requires_paused_mission(self):
        self.check("flighttask_resume", snapshot(mission="idle"), False, "invalid_state_transition")
        self.check("flighttask_resume", snapshot(mission="paused"), True)
        self.check("resume_wayline_task", snapshot(mission="idle"), False, "invalid_state_transition")
        self.check("resume_wayline_task", snapshot(mission="paused"), True)

    def test_paused_same_task_can_prepare_or_execute_for_breakpoint_relaunch(self):
        state = snapshot(
            mission="paused",
            details={"mission": {"task_id": "flight-001"}},
        )

        self.check("flighttask_prepare", state, True, data={"flight_id": "flight-001"})
        self.check("flighttask_execute", state, True, data={"flight_id": "flight-001"})
        self.check("flighttask_prepare", state, False, "mission_busy", data={"flight_id": "flight-002"})

    def test_paused_new_task_can_take_over_only_with_breakpoint(self):
        state = snapshot(
            mission="paused",
            details={"mission": {"task_id": "flight-old"}},
        )

        self.check(
            "flighttask_prepare",
            state,
            True,
            data={"flight_id": "flight-new", "break_point": {"index": 2}},
        )
        self.check(
            "flighttask_prepare",
            state,
            False,
            "mission_busy",
            data={"flight_id": "flight-new"},
        )

    def test_pause_requires_running_mission(self):
        self.check("flighttask_pause", snapshot(mission="idle"), False, "invalid_state_transition")
        self.check("flighttask_pause", snapshot(mission="running"), True)


if __name__ == "__main__":
    unittest.main()
