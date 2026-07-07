import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path


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

if "src.database.media_db" not in sys.modules:
    media_db = types.ModuleType("src.database.media_db")

    class _MediaDB:
        @staticmethod
        async def update_upload_status(*args, **kwargs):
            return True

    media_db.MediaDB = _MediaDB
    sys.modules["src.database.media_db"] = media_db

from src.cloud_api.dock3_media import (
    build_media_file,
    file_list_data,
    filter_media_files,
    media_type_for_file,
    normalize_delete_ids,
    normalize_priority_items,
    paginate,
)
from src.media.manager import MediaManager
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT


def base_msg(method, data):
    return {
        "tid": f"tid-{method}",
        "bid": f"bid-{method}",
        "gateway": "EDGEGW001",
        "method": method,
        "data": data,
    }


class Dock3MediaHelperTest(unittest.TestCase):
    def test_media_type_for_common_extensions(self):
        self.assertEqual(media_type_for_file("a.JPG"), "photo")
        self.assertEqual(media_type_for_file("a.mp4"), "video")
        self.assertEqual(media_type_for_file("a.txt"), "unknown")

    def test_file_list_filter_and_pagination_shape(self):
        now = int(time.time() * 1000)
        files = [
            {"name": "a.jpg", "media_type": "photo", "create_time": now - 1000},
            {"name": "b.mp4", "media_type": "video", "create_time": now},
        ]

        filtered = filter_media_files(files, {"media_type": "video"})
        selected = paginate(filtered, 1, 20)
        data = file_list_data(selected, 1, 20, len(filtered))

        self.assertEqual(data["total"], 1)
        self.assertEqual(data["files"][0]["name"], "b.mp4")
        self.assertEqual(data["pagination"]["page_size"], 20)

    def test_delete_and_priority_payload_validation(self):
        ids, errors = normalize_delete_ids({"files": [{"file_id": "a"}, {"fingerprint": "b"}]})
        self.assertFalse(errors)
        self.assertEqual(ids, ["a", "b"])

        files, errors = normalize_priority_items({"files": [{"file_id": "a", "priority": 10}]})
        self.assertFalse(errors)
        self.assertEqual(files[0]["priority"], 10)

        _, errors = normalize_priority_items({"files": [{"file_id": "a", "priority": "bad"}]})
        self.assertTrue(errors)


class Dock3MediaManagerTest(unittest.TestCase):
    def test_file_list_delete_and_priority_event(self):
        events = []
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.jpg"
            path.write_bytes(b"fake-jpeg")
            manager.storage_path = tmp

            listed = manager.handle_file_list_get(base_msg("file_list_get", {
                "media_type": "photo",
                "page": 1,
                "page_size": 10,
            }))

            self.assertEqual(listed["data"]["result"], 0)
            files = listed["data"]["files"]
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0]["name"], "sample.jpg")

            priority = manager.handle_adjust_upload_priority(base_msg("adjust_upload_priority", {
                "files": [{"file_id": files[0]["file_id"], "priority": 9}],
            }))
            self.assertEqual(priority["data"]["result"], 0)
            self.assertTrue(any(event.get("method") == "fileupload_progress" for event in events))

            deleted = manager.handle_file_delete(base_msg("file_delete", {
                "file_ids": [files[0]["file_id"]],
            }))
            self.assertEqual(deleted["data"]["result"], 0)
            self.assertFalse(path.exists())

    def test_file_delete_rejects_missing_ids(self):
        manager = MediaManager()
        reply = manager.handle_file_delete(base_msg("file_delete", {}))

        self.assertEqual(reply["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(reply["data"]["output"]["status"], "rejected")

    def test_build_media_file_has_stable_dock3_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.mp4"
            path.write_bytes(b"fake-video")
            item = build_media_file(str(path), tmp, "EDGEGW001")

        self.assertEqual(item["device_sn"], "EDGEGW001")
        self.assertEqual(item["media_type"], "video")
        self.assertEqual(item["name"], "sample.mp4")
        self.assertTrue(item["file_id"])
        self.assertEqual(item["fingerprint"], item["file_id"])


if __name__ == "__main__":
    unittest.main()
