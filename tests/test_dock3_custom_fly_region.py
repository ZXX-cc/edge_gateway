import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import src.modules.flight_area_manager as flight_area_module
from src.modules.flight_area_device_applier import FlightAreaDeviceApplier
from src.modules.flight_area_manager import FlightAreaManager


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "custom_fly_region"
    / "geofence_1df20749f4117e1120b6176eca56004f.json"
)


class Dock3CustomFlyRegionTest(unittest.TestCase):
    def test_customer_nfz_payload_matches_dji_md5_rule(self):
        document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        content = json.dumps(document, ensure_ascii=False).encode("utf-8")

        self.assertEqual(
            hashlib.md5(content).hexdigest(),
            "1df20749f4117e1120b6176eca56004f",
        )

    def test_customer_feature_level_nfz_parses_as_exclusion_polygon(self):
        document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        applier = FlightAreaDeviceApplier()

        polygons = applier._extract_polygons(document, FIXTURE_PATH.name)

        self.assertEqual(len(polygons), 1)
        self.assertEqual(polygons[0]["mode"], "exclusion")
        self.assertEqual(len(polygons[0]["points"]), 4)
        self.assertEqual(polygons[0]["points"][0]["longitude"], 120.595774)
        self.assertEqual(polygons[0]["points"][0]["latitude"], 28.754642)

    def test_official_geofence_type_dfence_parses_as_inclusion(self):
        self.assertEqual(
            FlightAreaDeviceApplier._area_mode({"geofence_type": "dfence"}),
            "inclusion",
        )

    def test_official_point_circle_converts_to_inclusion_polygon(self):
        document = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "id": "circle-area",
                "geometry": {
                    "type": "Point",
                    "coordinates": [120.600000, 28.750000],
                },
                "properties": {
                    "subType": "Circle",
                    "radius": 120.0,
                },
                "geofence_type": "dfence",
            }],
        }
        applier = FlightAreaDeviceApplier()

        polygons = applier._extract_polygons(document, "circle.json")

        self.assertEqual(len(polygons), 1)
        self.assertEqual(polygons[0]["mode"], "inclusion")
        self.assertEqual(len(polygons[0]["points"]), 36)
        self.assertTrue(all("latitude" in point and "longitude" in point for point in polygons[0]["points"]))

    def test_empty_files_reply_clears_cloud_cache_and_applies_empty_fence(self):
        class FakeApplier:
            def __init__(self):
                self.files = None

            def apply_files(self, files):
                self.files = list(files)
                return {"success": True, "applied": True, "fence_item_count": 0}

        with tempfile.TemporaryDirectory() as temp_dir:
            old_cache_dir = flight_area_module.FLIGHT_AREA_CACHE_DIR
            flight_area_module.FLIGHT_AREA_CACHE_DIR = temp_dir
            try:
                manager = FlightAreaManager()
            finally:
                flight_area_module.FLIGHT_AREA_CACHE_DIR = old_cache_dir
            manager._files = [{"name": "old.json", "checksum": "old"}]
            fake_applier = FakeApplier()
            manager.set_device_applier(fake_applier)
            events = []
            manager.set_event_callback(events.append)

            ok = manager._sync_from_cloud_reply({
                "method": "flight_areas_get",
                "data": {
                    "result": 0,
                    "output": {
                        "files": [],
                    },
                },
            })

            self.assertTrue(ok)
            self.assertEqual(manager._files, [])
            self.assertEqual(fake_applier.files, [])
            self.assertEqual(manager._last_sync["status"], "synchronized")
            self.assertEqual(events[-1]["method"], "flight_areas_sync_progress")
            self.assertEqual(events[-1]["data"]["status"], "synchronized")

    def test_drone_location_event_only_publishes_on_boundary_state_change(self):
        manager = FlightAreaManager()
        manager._monitored_areas = [{
            "area_id": "test-area",
            "mode": "exclusion",
            "points": [
                {"latitude": 0.0, "longitude": 0.0},
                {"latitude": 0.0, "longitude": 0.001},
                {"latitude": 0.001, "longitude": 0.001},
                {"latitude": 0.001, "longitude": 0.0},
            ],
        }]
        events = []
        manager.set_event_callback(events.append)

        manager.update_aircraft_location(0.0020, 0.0005)
        self.assertEqual(events, [])

        manager.update_aircraft_location(0.0013, 0.0005)
        self.assertEqual(len(events), 1)
        first_location = events[-1]["data"]["drone_locations"][0]
        self.assertEqual(first_location["area_id"], "test-area")
        self.assertEqual(first_location["state"], "near")
        self.assertEqual(first_location["event"], "near_boundary")
        self.assertFalse(first_location["is_in_area"])

        manager.update_aircraft_location(0.0013, 0.0005)
        self.assertEqual(len(events), 1)

        manager.update_aircraft_location(0.0005, 0.0005)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1]["data"]["drone_locations"][0]["state"], "inside")
        self.assertEqual(events[-1]["data"]["drone_locations"][0]["event"], "enter_area")
        self.assertTrue(events[-1]["data"]["drone_locations"][0]["is_in_area"])

        manager.update_aircraft_location(0.0013, 0.0005)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[-1]["data"]["drone_locations"][0]["event"], "exit_area")

        manager.update_aircraft_location(0.0020, 0.0005)
        self.assertEqual(len(events), 4)
        self.assertEqual(events[-1]["data"]["drone_locations"][0]["event"], "leave_boundary")


if __name__ == "__main__":
    unittest.main()
