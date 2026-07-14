import unittest

from src.state_machine.command_admission import CommandAdmissionEngine
from src.state_machine.global_state import GatewayStateSnapshot


class StaticReducer:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


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
    def check(self, method, state, accepted, reason=""):
        engine = CommandAdmissionEngine(StaticReducer(state))
        decision = engine.check(method, {"method": method, "need_reply": 1}, "thing/product/EDGEGW001/services")
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

    def test_resume_requires_paused_mission(self):
        self.check("flighttask_resume", snapshot(mission="idle"), False, "invalid_state_transition")
        self.check("flighttask_resume", snapshot(mission="paused"), True)

    def test_pause_requires_running_mission(self):
        self.check("flighttask_pause", snapshot(mission="idle"), False, "invalid_state_transition")
        self.check("flighttask_pause", snapshot(mission="running"), True)


if __name__ == "__main__":
    unittest.main()
