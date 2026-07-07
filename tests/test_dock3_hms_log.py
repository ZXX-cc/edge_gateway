import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.cloud_api.dock3_hms_log import (
    TASK_STATUS_CANCELED,
    TASK_STATUS_IN_PROGRESS,
    normalize_fileupload_start_files,
    normalize_hms_alarm,
    scan_log_files,
    upload_progress_event_data,
)
from src.modules.log_manager import LogManager
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
            "level": "3",
            "content": "battery warning",
        }, "EDGEGW001")

        self.assertEqual(alarm["id"], "alarm-1")
        self.assertEqual(alarm["alarm_id"], "alarm-1")
        self.assertEqual(alarm["device_sn"], "EDGEGW001")
        self.assertEqual(alarm["level"], 3)
        self.assertTrue(alarm["hms_key"])

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
            self.assertEqual(listed["data"]["output"]["status"], "ok")
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

    def test_fileupload_start_rejects_missing_files(self):
        manager = LogManager()
        reply = manager.handle_fileupload_start(base_msg("fileupload_start", {}))

        self.assertEqual(reply["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(reply["data"]["output"]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
