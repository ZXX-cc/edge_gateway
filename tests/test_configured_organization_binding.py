import unittest
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _MavlinkConstants:
    def __getattr__(self, _name):
        return 0


if "pymavlink" not in sys.modules:
    pymavlink = ModuleType("pymavlink")
    mavutil = ModuleType("pymavlink.mavutil")
    mavwp = ModuleType("pymavlink.mavwp")
    mavutil.mavlink = _MavlinkConstants()
    mavutil.mavlink_connection = lambda *_args, **_kwargs: None
    mavwp.MAVWPLoader = lambda *_args, **_kwargs: None
    pymavlink.mavutil = mavutil
    pymavlink.mavwp = mavwp
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = mavutil
    sys.modules["pymavlink.mavwp"] = mavwp

if "src.database.connection" not in sys.modules:
    connection = ModuleType("src.database.connection")
    connection.SessionLocal = None
    connection.close_database = lambda: None
    connection.configure_database = lambda *_args, **_kwargs: None
    connection.get_async_session_local = lambda: None
    connection.get_database_backend = lambda: "memory"
    connection.get_mongo_database = lambda: None
    connection.init_database = lambda: None
    connection.init_database_async = lambda: None
    sys.modules["src.database.connection"] = connection

if "src.database.retention_manager" not in sys.modules:
    retention_manager = ModuleType("src.database.retention_manager")
    retention_manager.DataRetentionManager = type("DataRetentionManager", (), {})
    sys.modules["src.database.retention_manager"] = retention_manager

if "src.database.cloud_sync_manager" not in sys.modules:
    cloud_sync_manager = ModuleType("src.database.cloud_sync_manager")
    cloud_sync_manager.CloudSyncManager = type("CloudSyncManager", (), {})
    sys.modules["src.database.cloud_sync_manager"] = cloud_sync_manager

if "src.database.telemetry_db" not in sys.modules:
    telemetry_db = ModuleType("src.database.telemetry_db")
    telemetry_db.TelemetryDB = type("TelemetryDB", (), {})
    sys.modules["src.database.telemetry_db"] = telemetry_db

if "src.database.mission_platform_snapshot_db" not in sys.modules:
    mission_snapshot_db = ModuleType("src.database.mission_platform_snapshot_db")
    mission_snapshot_db.MissionPlatformSnapshotDB = type("MissionPlatformSnapshotDB", (), {})
    sys.modules["src.database.mission_platform_snapshot_db"] = mission_snapshot_db

if "src.database.mission_db" not in sys.modules:
    mission_db = ModuleType("src.database.mission_db")
    mission_db.MissionDB = type("MissionDB", (), {})
    sys.modules["src.database.mission_db"] = mission_db

if "src.database.waypoint_db" not in sys.modules:
    waypoint_db = ModuleType("src.database.waypoint_db")
    waypoint_db.WaypointDB = type("WaypointDB", (), {})
    sys.modules["src.database.waypoint_db"] = waypoint_db

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

if "src.database.media_db" not in sys.modules:
    media_db = ModuleType("src.database.media_db")
    media_db.MediaDB = type("MediaDB", (), {})
    sys.modules["src.database.media_db"] = media_db

from src.main import EdgeGateway


class FakeCloudConnection:
    is_cloud_connected = True


class FakeDevice:
    def __init__(self, bind_status_bound=True):
        self.bind_status_bound = bind_status_bound
        self.queried_sns = []
        self.organization_get_calls = []
        self.organization_bind_calls = []
        self.topology_reports = 0

    def get_all_sub_devices(self):
        return []

    def query_bind_status_from_cloud(self, device_sns, callback=None, timeout=30):
        self.queried_sns = list(device_sns)
        if callback:
            callback(
                "airport_bind_status",
                {
                    "tid": "status-tid",
                    "data": {
                        "result": 0,
                        "output": {
                            "bind_status": [
                                {
                                    "sn": sn,
                                    "is_device_bind_organization": self.bind_status_bound,
                                    "organization_id": "org-001" if self.bind_status_bound else "",
                                    "organization_name": "Grid Org" if self.bind_status_bound else "",
                                }
                                for sn in device_sns
                            ]
                        },
                    },
                },
            )
        return "status-tid"

    def query_organization_from_cloud(self, binding_code, org_id, callback=None, timeout=30):
        self.organization_get_calls.append((binding_code, org_id, timeout))
        if callback:
            callback(
                "airport_organization_get",
                {
                    "tid": "get-tid",
                    "data": {"result": 0, "output": {"organization_name": "Grid Org"}},
                },
            )
        return "get-tid"

    def bind_to_organization(self, bind_devices, callback=None, timeout=30):
        self.organization_bind_calls.append((bind_devices, timeout))
        if callback:
            callback(
                "airport_organization_bind",
                {"tid": "bind-tid", "data": {"result": 0, "output": {"err_infos": []}}},
            )
        return "bind-tid"

    def report_update_topo(self):
        self.topology_reports += 1


class ConfiguredOrganizationBindingTest(unittest.TestCase):
    def gateway_with_device(self, device):
        gateway = EdgeGateway.__new__(EdgeGateway)
        gateway.device = device
        gateway.cloud_conn = FakeCloudConnection()
        gateway.running = True
        gateway._organization_binding_retry_timer = None
        gateway._organization_binding_retry_attempt = 0
        gateway._organization_binding_pending_tid = ""
        gateway._organization_binding_pending_stage = ""
        return gateway

    def config_get(self, path, default=None):
        if path == "device.organization_binding":
            return {
                "enabled": True,
                "device_binding_code": "BIND-CODE",
                "organization_id": "org-001",
                "device_callsign": "Gateway",
                "device_model_key": "3-119-0",
                "bind_sub_devices": True,
                "request_timeout_seconds": 30,
            }
        return default

    def test_bound_status_skips_configured_organization_bind(self):
        device = FakeDevice(bind_status_bound=True)
        gateway = self.gateway_with_device(device)

        with (
            patch("src.main.Config.get", side_effect=self.config_get),
            patch("src.main.Config.get_gateway_sn", return_value="EDGEGW001"),
        ):
            self.assertTrue(gateway._start_configured_organization_binding())

        self.assertEqual(device.queried_sns, ["EDGEGW001"])
        self.assertEqual(device.organization_get_calls, [])
        self.assertEqual(device.organization_bind_calls, [])
        self.assertEqual(device.topology_reports, 1)

    def test_unbound_status_allows_configured_organization_bind(self):
        device = FakeDevice(bind_status_bound=False)
        gateway = self.gateway_with_device(device)

        with (
            patch("src.main.Config.get", side_effect=self.config_get),
            patch("src.main.Config.get_gateway_sn", return_value="EDGEGW001"),
        ):
            self.assertTrue(gateway._start_configured_organization_binding())

        self.assertEqual(len(device.organization_get_calls), 1)
        self.assertEqual(len(device.organization_bind_calls), 1)
        bind_devices, _timeout = device.organization_bind_calls[0]
        self.assertEqual(bind_devices[0]["sn"], "EDGEGW001")
        self.assertEqual(bind_devices[0]["device_binding_code"], "BIND-CODE")


if __name__ == "__main__":
    unittest.main()
