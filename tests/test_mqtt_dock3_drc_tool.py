import os
import sys
import unittest
from types import SimpleNamespace
from threading import Event


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from mqtt_dock3_drc_test import (  # noqa: E402
    FALLBACK_TARGET_LATITUDE,
    FALLBACK_TARGET_LONGITUDE,
    _candidate_drone_sns,
    _osd_topics,
    has_cli_takeoff_target,
    is_fallback_target_allowed,
    resolve_takeoff_target,
    wait_for_drone_osd,
)


def args(**overrides):
    data = {
        "target_lat": None,
        "target_lon": None,
        "target_height": 50.0,
        "target_offset_meters": 30.0,
        "allow_fallback_target": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeObservedClient:
    def __init__(self, observed_by_topic):
        self.observed_by_topic = observed_by_topic
        self.observed_signal = Event()

    def observed_count(self, topic):
        return len(self.observed_by_topic.get(topic, []))


class Dock3DrcToolTargetTest(unittest.TestCase):
    def test_candidate_drone_sns_include_common_sim_names(self):
        self.assertEqual(_candidate_drone_sns("CUSTOM"), ["CUSTOM", "DRONESIM001", "DRONE-SIM-001"])
        self.assertEqual(_candidate_drone_sns("DRONESIM001"), ["DRONESIM001", "DRONE-SIM-001"])
        self.assertIn("thing/product/DRONE-SIM-001/osd", _osd_topics("DRONESIM001"))

    def test_resolve_takeoff_target_prefers_cli_target(self):
        target, source = resolve_takeoff_target(
            args(target_lat=31.2, target_lon=121.5),
            {"data": {"latitude": 1, "longitude": 2}},
            telemetry_source="property_get",
        )

        self.assertTrue(has_cli_takeoff_target(args(target_lat=31.2, target_lon=121.5)))
        self.assertEqual(source, "cli")
        self.assertEqual(target["target_latitude"], 31.2)
        self.assertEqual(target["target_longitude"], 121.5)
        self.assertFalse(has_cli_takeoff_target(args()))

    def test_resolve_takeoff_target_uses_property_get_before_osd(self):
        target, source = resolve_takeoff_target(
            args(target_offset_meters=30.0),
            {"data": {"latitude": 39.9042, "longitude": 116.3975, "result": 0}},
            telemetry_source="property_get",
        )

        self.assertEqual(source, "property_get")
        self.assertAlmostEqual(target["target_latitude"], 39.9042)
        self.assertGreater(target["target_longitude"], 116.3975)
        self.assertLess(target["target_longitude"] - 116.3975, 0.001)

    def test_resolve_takeoff_target_uses_osd_nearby_offset(self):
        target, source = resolve_takeoff_target(
            args(target_offset_meters=50.0),
            {"data": {"latitude": {"value": -35.3632622}, "longitude": {"value": 149.1652375}}},
        )

        self.assertEqual(source, "drone_osd")
        self.assertAlmostEqual(target["target_latitude"], -35.3632622)
        self.assertGreater(target["target_longitude"], 149.1652375)
        self.assertLess(target["target_longitude"] - 149.1652375, 0.001)

    def test_resolve_takeoff_target_falls_back_without_osd(self):
        target, source = resolve_takeoff_target(args(), {})

        self.assertEqual(source, "fallback")
        self.assertEqual(target["target_latitude"], FALLBACK_TARGET_LATITUDE)
        self.assertEqual(target["target_longitude"], FALLBACK_TARGET_LONGITUDE)

    def test_fallback_target_requires_explicit_allow(self):
        self.assertFalse(is_fallback_target_allowed(args(), "fallback"))
        self.assertTrue(is_fallback_target_allowed(args(allow_fallback_target=True), "fallback"))
        self.assertTrue(is_fallback_target_allowed(args(), "drone_osd"))
        self.assertTrue(is_fallback_target_allowed(args(), "cli"))

    def test_resolve_takeoff_target_rejects_partial_cli_target(self):
        with self.assertRaises(ValueError):
            resolve_takeoff_target(args(target_lat=31.2), {})

    def test_wait_for_drone_osd_returns_latest_existing_payload(self):
        payload = {"data": {"latitude": 31.2, "longitude": 121.5}}
        client = FakeObservedClient({"thing/product/DRONESIM001/osd": [payload]})

        topic, observed = wait_for_drone_osd(client, ["thing/product/DRONESIM001/osd"], timeout=0)

        self.assertEqual(topic, "thing/product/DRONESIM001/osd")
        self.assertEqual(observed, payload)


if __name__ == "__main__":
    unittest.main()
