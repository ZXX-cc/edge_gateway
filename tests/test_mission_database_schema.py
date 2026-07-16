import unittest
import uuid

from src.database.schemas import MissionCreate


class MissionDatabaseSchemaTest(unittest.TestCase):
    def test_mission_create_accepts_gateway_sn_as_device_id(self):
        mission = MissionCreate(
            name="flight-001",
            device_id="EDGEGW001",
            status="uploaded",
            execution_conditions={},
        )

        self.assertEqual(mission.device_id, "EDGEGW001")

    def test_mission_create_still_accepts_uuid_device_id(self):
        device_id = str(uuid.uuid4())

        mission = MissionCreate(name="flight-002", device_id=device_id)

        self.assertEqual(mission.device_id, device_id)

    def test_mission_create_rejects_empty_device_id(self):
        with self.assertRaises(ValueError):
            MissionCreate(name="flight-003", device_id="  ")


if __name__ == "__main__":
    unittest.main()
