import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if "src.database.connection" not in sys.modules:
    connection = types.ModuleType("src.database.connection")

    async def init_database_async():
        return None

    def get_async_session_local():
        return None

    connection.init_database_async = init_database_async
    connection.get_async_session_local = get_async_session_local
    sys.modules["src.database.connection"] = connection

if "src.database.device_db" not in sys.modules:
    device_db = types.ModuleType("src.database.device_db")
    device_db.DeviceDB = type("DeviceDB", (), {})
    sys.modules["src.database.device_db"] = device_db

if "src.database.alarm_db" not in sys.modules:
    alarm_db = types.ModuleType("src.database.alarm_db")
    alarm_db.AlarmDB = type("AlarmDB", (), {})
    sys.modules["src.database.alarm_db"] = alarm_db

if "src.database.operation_db" not in sys.modules:
    operation_db = types.ModuleType("src.database.operation_db")
    operation_db.OperationDB = type("OperationDB", (), {})
    sys.modules["src.database.operation_db"] = operation_db

from src.cloud_api.dock3_hms_log import (
    TASK_STATUS_CANCELED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_OK,
    group_fileupload_files,
    normalize_fileupload_start_files,
    normalize_hms_alarm,
    scan_log_files,
    scan_log_files_for_modules,
    upload_progress_event_data,
)
from src.device.manager import DeviceManager
from src.modules.log_manager import LogManager
from src.utils.config import Config
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT


def base_msg(method, data):
    return {
        "tid": f"tid-{method}",
        "bid": f"bid-{method}",
        "gateway": "EDGEGW001",
        "method": method,
        "data": data,
    }


class Dock3HmsLogHelperTest(unittest.TestCase):
    def test_normalize_hms_alarm_has_stable_contract_fields(self):
        alarm = normalize_hms_alarm({
            "id": "alarm-1",
            "level": "2",
            "code": "0x16100083",
            "content": "battery warning",
        }, "EDGEGW001")

        self.assertEqual(alarm["id"], "alarm-1")
        self.assertEqual(alarm["alarm_id"], "alarm-1")
        self.assertEqual(alarm["device_sn"], "EDGEGW001")
        self.assertEqual(alarm["level"], 2)
        self.assertTrue(alarm["hms_key"])
        self.assertIn("code", alarm)
        self.assertIn("device_type", alarm)
        self.assertIn("in_the_sky", alarm)
        self.assertIn("imminent", alarm)
        self.assertIn("component_index", alarm["args"])
        self.assertIn("sensor_index", alarm["args"])

    def test_scan_log_files_and_progress_event_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "edge_gateway_test.log"
            path.write_text("dock3 log test\n", encoding="utf-8")

            files = scan_log_files(tmp, "EDGEGW001")
            event = upload_progress_event_data(
                files,
                device_sn="EDGEGW001",
                status=TASK_STATUS_IN_PROGRESS,
                progress=10,
            )

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["name"], "edge_gateway_test.log")
        self.assertEqual(event["output"]["status"], TASK_STATUS_IN_PROGRESS)
        self.assertEqual(len(event["output"]["ext"]["files"]), 1)

    def test_scan_log_files_distinguishes_aircraft_and_dock_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            aircraft_dir = Path(tmp) / "flight_telemetry"
            dock_dir = Path(tmp) / "edge_gateway"
            aircraft_dir.mkdir()
            dock_dir.mkdir()
            (aircraft_dir / "mavlink_raw.log").write_text("aircraft log\n", encoding="utf-8")
            (dock_dir / "mqtt.log").write_text("dock log\n", encoding="utf-8")

            aircraft_logs = scan_log_files(tmp, "EDGEGW001", module=0)
            dock_logs = scan_log_files(tmp, "EDGEGW001", module=3)
            all_logs = scan_log_files_for_modules(tmp, "EDGEGW001", [0, 3])
            grouped = group_fileupload_files(all_logs, [0, 3], "EDGEGW001")

        self.assertEqual({item["module"] for item in aircraft_logs}, {"0"})
        self.assertEqual({item["module"] for item in dock_logs}, {"3"})
        self.assertEqual(len(all_logs), 2)
        self.assertEqual(len(grouped[0]["list"]), 1)
        self.assertEqual(len(grouped[1]["list"]), 1)
        self.assertEqual(grouped[0]["list"][0]["module"], "0")
        self.assertEqual(grouped[1]["list"][0]["module"], "3")

    def test_normalize_fileupload_start_rejects_empty_payload(self):
        files, errors = normalize_fileupload_start_files({})

        self.assertFalse(files)
        self.assertTrue(errors)


class Dock3LogManagerTest(unittest.TestCase):
    def test_fileupload_list_start_and_cancel(self):
        events = []
        manager = LogManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)

        with tempfile.TemporaryDirectory() as tmp:
            manager.log_dir = tmp
            path = Path(tmp) / f"edge_gateway_{int(time.time() * 1000)}.log"
            path.write_text("dock3 log manager test\n", encoding="utf-8")

            listed = manager.handle_fileupload_list(base_msg("fileupload_list", {"module_list": [3]}))
            self.assertEqual(listed["data"]["result"], 0)
            self.assertNotIn("output", listed["data"])
            self.assertNotIn("total", listed["data"])
            grouped = listed["data"]["files"]
            self.assertEqual(len(grouped), 1)
            self.assertEqual(len(grouped[0]["list"]), 1)

            start_payload = {
                "provider": "mock",
                "bucket": "dock3-test",
                "params": {
                    "files": [{
                        "module": "3",
                        "object_key": "logs/edge_gateway_test.log",
                        "list": [{
                            "boot_index": grouped[0]["list"][0]["boot_index"],
                            "size": grouped[0]["list"][0]["size"],
                        }],
                    }],
                },
            }
            started = manager.handle_fileupload_start(base_msg("fileupload_start", start_payload))
            self.assertEqual(started["data"]["result"], 0)
            self.assertEqual(started["data"]["output"]["status"], TASK_STATUS_IN_PROGRESS)
            self.assertTrue(any(event.get("method") == "fileupload_progress" for event in events))

            canceled = manager.handle_fileupload_update(base_msg("fileupload_update", {
                "status": "cancel",
                "module_list": [3],
            }))
            self.assertEqual(canceled["data"]["result"], 0)
            self.assertEqual(canceled["data"]["status"], "cancel")
            self.assertTrue(any(
                event.get("data", {}).get("output", {}).get("status") == TASK_STATUS_CANCELED
                for event in events
            ))

            paused = manager.handle_fileupload_update(base_msg("fileupload_update", {
                "status": "pause",
                "module_list": [3],
            }))
            self.assertEqual(paused["data"]["result"], INVALID_COMMAND_FORMAT)
            self.assertEqual(paused["data"]["output"]["status"], "rejected")

    def test_fileupload_start_puts_real_log_bytes_to_endpoint(self):
        uploaded = {}
        events = []
        manager = LogManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)

        with tempfile.TemporaryDirectory() as tmp:
            manager.log_dir = tmp
            payload = b"dock3 real log upload bytes\n"
            path = Path(tmp) / "edge_gateway_real_upload.log"
            path.write_bytes(payload)

            listed = manager.handle_fileupload_list(base_msg("fileupload_list", {"module_list": [3]}))
            upload_item = listed["data"]["files"][0]["list"][0]

            def capture_upload(filepath, target_url, *args, **kwargs):
                uploaded["target_url"] = target_url
                uploaded["body"] = Path(filepath).read_bytes()
                uploaded["credentials"] = args[0] if args else kwargs.get("credentials")
                return True

            with patch.object(manager, "_upload_file_http", side_effect=capture_upload):
                started = manager.handle_fileupload_start(base_msg("fileupload_start", {
                    "provider": "minio",
                    "bucket": "dock3-test",
                    "region": "local",
                    "endpoint": "http://127.0.0.1:18080/dock3-log-upload",
                    "credentials": {
                        "access_key_id": "test-access-key",
                        "access_key_secret": "test-secret-key",
                        "security_token": "test-token",
                        "expire": int(time.time()) + 3600,
                    },
                    "params": {
                        "files": [{
                            "module": "3",
                            "object_key": "logs/EDGEGW001/edge_gateway_real_upload.log",
                            "list": [{"boot_index": upload_item["boot_index"]}],
                        }],
                    },
                }))

                deadline = time.time() + 3
                while time.time() < deadline and "body" not in uploaded:
                    time.sleep(0.05)

        self.assertEqual(started["data"]["result"], 0)
        self.assertEqual(uploaded.get("body"), payload)
        self.assertTrue(uploaded.get("target_url", "").endswith("/logs/EDGEGW001/edge_gateway_real_upload.log"))
        self.assertEqual(uploaded.get("credentials", {}).get("security_token"), "test-token")
        self.assertTrue(any(
            event.get("data", {}).get("output", {}).get("status") == TASK_STATUS_OK
            for event in events
        ))

    def test_fileupload_start_uses_distinct_object_key_for_each_file(self):
        uploaded_urls = []
        manager = LogManager()
        manager.gateway_sn = "EDGEGW001"

        with tempfile.TemporaryDirectory() as tmp:
            manager.log_dir = tmp
            first = Path(tmp) / "edge_gateway_first.log"
            second = Path(tmp) / "edge_gateway_second.log"
            first.write_text("first\n", encoding="utf-8")
            second.write_text("second\n", encoding="utf-8")

            listed = manager.handle_fileupload_list(base_msg("fileupload_list", {"module_list": [3]}))
            upload_items = listed["data"]["files"][0]["list"]
            boot_indexes = [item["boot_index"] for item in upload_items[:2]]

            def capture_upload(filepath, target_url, *args, **kwargs):
                uploaded_urls.append(target_url)
                return True

            with patch.object(manager, "_upload_file_http", side_effect=capture_upload):
                manager.handle_fileupload_start(base_msg("fileupload_start", {
                    "provider": "minio",
                    "endpoint": "http://127.0.0.1:18080/dock3-log-upload",
                    "credentials": {"expire": int(time.time()) + 3600},
                    "params": {
                        "files": [{
                            "module": "3",
                            "object_key": "logs/EDGEGW001/batch",
                            "list": [{"boot_index": item} for item in boot_indexes],
                        }],
                    },
                }))

                deadline = time.time() + 3
                while time.time() < deadline and len(uploaded_urls) < 2:
                    time.sleep(0.05)

        self.assertEqual(len(uploaded_urls), 2)
        self.assertEqual(len(set(uploaded_urls)), 2)
        self.assertTrue(all("/logs/EDGEGW001/batch/" in url for url in uploaded_urls))

    def test_fileupload_list_and_start_support_aircraft_module(self):
        events = []
        manager = LogManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)

        with tempfile.TemporaryDirectory() as tmp:
            manager.log_dir = tmp
            flight_dir = Path(tmp) / "flight_telemetry"
            flight_dir.mkdir()
            (flight_dir / "mavlink.log").write_text("aircraft telemetry\n", encoding="utf-8")

            listed = manager.handle_fileupload_list(base_msg("fileupload_list", {"module_list": [0, 3]}))
            grouped = {item["module"]: item["list"] for item in listed["data"]["files"]}
            self.assertEqual(len(grouped["0"]), 1)
            self.assertEqual(len(grouped["3"]), 0)

            started = manager.handle_fileupload_start(base_msg("fileupload_start", {
                "provider": "mock",
                "params": {
                    "files": [{
                        "module": "0",
                        "object_key": "logs/EDGEGW001/aircraft",
                        "list": [{"boot_index": grouped["0"][0]["boot_index"]}],
                    }],
                },
            }))

        self.assertEqual(started["data"]["result"], 0)
        self.assertTrue(any(
            file_item.get("module") == "0"
            for event in events
            for file_item in event.get("data", {}).get("output", {}).get("ext", {}).get("files", [])
        ))


class Dock3HmsManagerTest(unittest.TestCase):
    def test_hms_get_injection_is_disabled_in_production(self):
        Config.merge({"runtime": {"mode": "production"}})
        try:
            manager = DeviceManager()
            reply = manager.handle_hms_get(base_msg("hms_get", {
                "simulate": True,
                "alarms": [{"id": "alarm-1", "level": 2, "code": "0x16100083"}],
            }))
        finally:
            Config.merge({"runtime": {"mode": "test"}})

        self.assertEqual(reply["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(reply["data"]["output"]["status"], "rejected")

    def test_hms_event_uses_official_alarm_fields(self):
        events = []
        manager = DeviceManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_callbacks(events.append, lambda event: None)

        reply = manager.handle_hms_get(base_msg("hms_get", {
            "simulate": True,
            "alarms": [{
                "id": "alarm-1",
                "level": 2,
                "module": 3,
                "code": "0x16100083",
                "device_type": "0-67-0",
                "in_the_sky": 0,
                "imminent": 1,
                "args": {"component_index": 0, "sensor_index": 1},
            }],
        }))

        self.assertEqual(reply["data"]["result"], 0)
        alarm = events[0]["data"]["list"][0]
        self.assertEqual(alarm["code"], "0x16100083")
        self.assertEqual(alarm["device_type"], "0-67-0")
        self.assertEqual(alarm["module"], 3)
        self.assertEqual(alarm["in_the_sky"], 0)
        self.assertEqual(alarm["imminent"], 1)
        self.assertEqual(alarm["args"]["sensor_index"], 1)

    def test_device_log_legacy_entry_supports_aircraft_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            flight_dir = Path(tmp) / "flight_telemetry"
            flight_dir.mkdir()
            (flight_dir / "mavlink.log").write_text("legacy aircraft telemetry\n", encoding="utf-8")
            original_log_dir = Config.get("logging.dir", "./logs")
            Config.merge({"logging": {"dir": tmp}})
            try:
                manager = DeviceManager()
                manager.gateway_sn = "EDGEGW001"
                reply = manager.handle_device_log_list_get(base_msg("device_log_list_get", {
                    "module_list": [0],
                }))
            finally:
                Config.merge({"logging": {"dir": original_log_dir}})

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["module_list"], [0])
        self.assertEqual(len(reply["data"]["list"]), 1)
        self.assertEqual(reply["data"]["list"][0]["module"], "0")

    def test_fileupload_start_rejects_missing_files(self):
        manager = LogManager()
        reply = manager.handle_fileupload_start(base_msg("fileupload_start", {}))

        self.assertEqual(reply["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(reply["data"]["output"]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
