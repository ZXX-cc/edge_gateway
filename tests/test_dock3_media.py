import os
import requests
import sys
import tempfile
import time
import types
import unittest
from copy import deepcopy
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
    build_remote_media_file,
    file_list_data,
    filter_media_files,
    media_type_for_file,
    normalize_delete_ids,
    normalize_priority_items,
    paginate,
    storage_config_output,
)
from src.cloud_api.dock3_live import validate_live_start_payload
from src.media.manager import MediaManager
from src.utils.config import Config
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED, DOCK_SYSTEM_ERROR, INVALID_COMMAND_FORMAT


def base_msg(method, data):
    return {
        "tid": f"tid-{method}",
        "bid": f"bid-{method}",
        "gateway": "EDGEGW001",
        "method": method,
        "data": data,
    }


class FakeCameraController:
    def __init__(self):
        self.calls = []
        self.sync_calls = []

    def handle_camera_photo_take(self, msg):
        self.calls.append(("camera_photo_take", msg))
        return {
            "tid": msg.get("tid"),
            "bid": msg.get("bid"),
            "gateway": msg.get("gateway"),
            "method": "camera_photo_take",
            "data": {"result": 0},
        }

    def sync_mapping_camera_media(self, **kwargs):
        self.sync_calls.append(kwargs)
        return []


class Dock3MediaHelperTest(unittest.TestCase):
    def test_media_type_for_common_extensions(self):
        self.assertEqual(media_type_for_file("a.JPG"), "photo")
        self.assertEqual(media_type_for_file("a.mp4"), "video")
        self.assertEqual(media_type_for_file("a.txt"), "unknown")

    def test_build_remote_media_file_has_download_url(self):
        item = build_remote_media_file("http://192.168.1.119:8554/download/photo/a.jpg", "EDGEGW001")

        self.assertEqual(item["device_sn"], "EDGEGW001")
        self.assertEqual(item["media_type"], "photo")
        self.assertEqual(item["name"], "a.jpg")
        self.assertEqual(item["download_url"], "http://192.168.1.119:8554/download/photo/a.jpg")
        self.assertEqual(item["upload_status"], "remote")

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
    def setUp(self):
        self._original_config = deepcopy(Config.all())
        Config.merge({
            "gateway": {"sn": "EDGEGW001"},
            "payload": {
                "devices": {
                    "gimbal": {
                        "enabled": True,
                        "cloud_api": {
                            "type": 52,
                            "subtype": 0,
                            "gimbal_index": 0,
                            "video_index": "normal-0",
                            "video_type": "normal",
                            "switchable_video_types": ["normal", "wide", "zoom", "infrared"],
                        },
                    },
                    "mapping_camera": {"enabled": True},
                    "spectral_camera": {"enabled": True},
                }
            },
        })

    def tearDown(self):
        Config._data = self._original_config

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

    def test_file_list_can_include_configured_remote_http_media(self):
        class FakeResponse:
            def __init__(self, text):
                self.text = text

            def raise_for_status(self):
                return None

        pages = {
            "http://media.local/download/": '<a href="photo/">photo/</a><a href="video/">video/</a>',
            "http://media.local/download/photo/": '<a href="../">../</a><a href="a.jpg">a.jpg</a>',
            "http://media.local/download/video/": '<a href="../">../</a><a href="b.mp4">b.mp4</a>',
        }

        def fake_get(url, timeout):
            return FakeResponse(pages[url])

        old_get = requests.get
        requests.get = fake_get
        Config.merge({
            "media": {
                "remote_media": {
                    "enabled": True,
                    "base_url": "http://media.local/download/",
                    "max_depth": 2,
                    "timeout_seconds": 1,
                }
            }
        })
        try:
            manager = MediaManager()
            with tempfile.TemporaryDirectory() as tmp:
                manager.storage_path = tmp
                listed = manager.handle_file_list_get(base_msg("file_list_get", {
                    "media_type": "all",
                    "page": 1,
                    "page_size": 10,
                }))
        finally:
            requests.get = old_get
            Config.merge({"media": {"remote_media": {"enabled": False}}})

        files = listed["data"]["files"]
        self.assertEqual({item["name"] for item in files}, {"a.jpg", "b.mp4"})
        self.assertTrue(all(item["source"] == "remote_http" for item in files))

    def test_file_list_syncs_mapping_camera_before_scan(self):
        manager = MediaManager()
        camera = FakeCameraController()
        manager.set_camera_controller(camera)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Riebo000001.jpg"
            path.write_bytes(b"mapping-photo")
            manager.storage_path = tmp

            listed = manager.handle_file_list_get(base_msg("file_list_get", {
                "media_type": "photo",
                "page": 1,
                "page_size": 10,
            }))

        self.assertEqual(camera.sync_calls, [{"download_all": True}])
        self.assertEqual(listed["data"]["result"], 0)
        self.assertEqual(listed["data"]["files"][0]["name"], "Riebo000001.jpg")

    def test_file_list_scans_mapping_camera_nested_photo_folders(self):
        manager = MediaManager()

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "DCIM" / "flight-001"
            folder.mkdir(parents=True)
            path = folder / "Riebo000002.jpg"
            path.write_bytes(b"mapping-photo")
            manager.storage_path = tmp

            listed = manager.handle_file_list_get(base_msg("file_list_get", {
                "media_type": "photo",
                "page": 1,
                "page_size": 10,
            }))

        self.assertEqual(listed["data"]["result"], 0)
        files = listed["data"]["files"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["name"], "Riebo000002.jpg")
        self.assertEqual(files[0]["object_key"], "DCIM/flight-001/Riebo000002.jpg")

    def test_remote_media_is_cached_before_upload(self):
        class FakeResponse:
            def __init__(self, content):
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield self.content

        old_get = requests.get
        requests.get = lambda url, stream, timeout: FakeResponse(b"remote-jpeg")
        try:
            manager = MediaManager()
            with tempfile.TemporaryDirectory() as tmp:
                manager.storage_path = tmp
                remote = build_remote_media_file("http://media.local/download/photo/a.jpg", "EDGEGW001")
                uploaded = []
                manager._request_upload_credentials = lambda files: {"upload_url": "http://oss.local/a.jpg"}
                manager._upload_file_http = lambda filepath, upload_url: uploaded.append((filepath, upload_url)) or True
                manager._upload_queue = [remote]
                manager._uploading = True

                manager._upload_files()
                self.assertEqual(len(uploaded), 1)
                self.assertEqual(uploaded[0][1], "http://oss.local/a.jpg")
                self.assertTrue(Path(uploaded[0][0]).exists())
                self.assertEqual(Path(uploaded[0][0]).read_bytes(), b"remote-jpeg")
        finally:
            requests.get = old_get

    def test_s3_upload_builds_path_style_url_and_puts_file_bytes(self):
        class FakePutResponse:
            status_code = 200
            text = "ok"

        calls = []

        def fake_put(url, data, headers, timeout):
            calls.append({
                "url": url,
                "data": data,
                "headers": headers,
                "timeout": timeout,
            })
            return FakePutResponse()

        old_put = requests.put
        requests.put = fake_put
        try:
            manager = MediaManager()
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "sample.jpg"
                path.write_bytes(b"jpeg-bytes")
                ok, url, key = manager._upload_file_s3(str(path), {
                    "endpoint": "https://minio.example:9000",
                    "bucket": "media",
                    "region": "cn-test-1",
                    "object_key_prefix": "djidock",
                    "credentials": {
                        "access_key_id": "ak",
                        "access_key_secret": "sk",
                        "security_token": "token",
                    },
                })
        finally:
            requests.put = old_put

        self.assertTrue(ok)
        self.assertEqual(key, "djidock/sample.jpg")
        self.assertEqual(url, "https://minio.example:9000/media/djidock/sample.jpg")
        self.assertEqual(calls[0]["data"], b"jpeg-bytes")
        self.assertEqual(calls[0]["headers"]["x-amz-security-token"], "token")
        self.assertIn("AWS4-HMAC-SHA256", calls[0]["headers"]["Authorization"])

    def test_upload_worker_uses_s3_credentials_when_upload_url_missing(self):
        manager = MediaManager()
        events = []
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.jpg"
            path.write_bytes(b"jpeg")
            manager._request_upload_credentials = lambda files: {
                "output": {
                    "endpoint": "https://minio.example:9000",
                    "bucket": "media",
                    "region": "cn-test-1",
                    "object_key_prefix": "djidock",
                    "credentials": {
                        "access_key_id": "ak",
                        "access_key_secret": "sk",
                    },
                }
            }
            uploaded = []
            manager._upload_file_s3 = (
                lambda filepath, storage, object_key="": uploaded.append((filepath, storage, object_key))
                or (True, "https://minio.example:9000/media/djidock/sample.jpg", object_key)
            )
            manager._upload_queue = [{
                "file_path": str(path),
                "file_name": "sample.jpg",
                "file_size": 4,
                "file_type": "jpg",
            }]
            manager._uploading = True

            manager._upload_files()

        self.assertEqual(len(uploaded), 1)
        self.assertEqual(uploaded[0][2], "djidock/sample.jpg")
        callbacks = [event for event in events if event.get("method") == "file_upload_callback"]
        self.assertEqual(callbacks[0]["data"]["file"]["object_key"], "djidock/sample.jpg")

    def test_http_upload_callback_does_not_use_upload_url_as_object_key(self):
        manager = MediaManager()
        events = []
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.jpg"
            path.write_bytes(b"jpeg")
            manager._request_upload_credentials = lambda files: {
                "upload_url": "https://oss.example/bucket/media/sample.jpg?signature=abc",
            }
            manager._upload_file_http = lambda filepath, upload_url: True
            manager._upload_queue = [{
                "file_id": "media-id",
                "fingerprint": "media-id",
                "file_path": str(path),
                "file_name": "sample.jpg",
                "file_size": 4,
                "file_type": "jpg",
            }]
            manager._uploading = True

            manager._upload_files()

        callback = next(event for event in events if event.get("method") == "file_upload_callback")
        file_data = callback["data"]["file"]
        self.assertEqual(file_data["object_key"], "bucket/media/sample.jpg")
        self.assertEqual(file_data["path"], "https://oss.example/bucket/media/sample.jpg?signature=abc")
        self.assertNotIn("signature", file_data["object_key"])

    def test_upload_worker_uses_database_id_for_completed_status(self):
        manager = MediaManager()
        updates = []

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.jpg"
            path.write_bytes(b"jpeg")
            manager._request_upload_credentials = lambda files: {
                "upload_url": "https://oss.example/bucket/media/sample.jpg",
            }
            manager._upload_file_http = lambda filepath, upload_url: True
            manager.report_upload_complete = lambda file_info: None
            manager._update_upload_progress = (
                lambda file_id, progress, status, **kwargs:
                updates.append((file_id, progress, status, kwargs))
            )
            manager._upload_queue = [{
                "id": "db-media-id",
                "file_id": "business-file-id",
                "fingerprint": "business-file-id",
                "file_path": str(path),
                "file_name": "sample.jpg",
                "file_size": 4,
                "file_type": "jpg",
            }]
            manager._uploading = True

            manager._upload_files()

        self.assertEqual(updates[0][0], "business-file-id")
        self.assertEqual(updates[0][2], "completed")
        self.assertEqual(updates[0][3]["db_id"], "db-media-id")

    def test_upload_worker_uses_database_id_for_failed_status(self):
        manager = MediaManager()
        updates = []

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.jpg"
            path.write_bytes(b"jpeg")
            manager._request_upload_credentials = lambda files: None
            manager._update_upload_progress = (
                lambda file_id, progress, status, **kwargs:
                updates.append((file_id, progress, status, kwargs))
            )
            manager._upload_queue = [{
                "id": "db-media-id",
                "file_id": "business-file-id",
                "fingerprint": "business-file-id",
                "file_path": str(path),
                "file_name": "sample.jpg",
                "file_size": 4,
                "file_type": "jpg",
            }]
            manager._uploading = True

            manager._upload_files()

        self.assertEqual(updates[0][0], "business-file-id")
        self.assertEqual(updates[0][2], "failed")
        self.assertEqual(updates[0][3]["db_id"], "db-media-id")

    def test_upload_queue_uses_priority_order(self):
        manager = MediaManager()
        uploaded = []
        manager._request_upload_credentials = lambda files: {
            "upload_url": f"https://oss.example/bucket/{files[0]['file_name']}",
        }
        manager._upload_file_http = lambda filepath, upload_url: uploaded.append(Path(filepath).name) or True

        with tempfile.TemporaryDirectory() as tmp:
            low = Path(tmp) / "low.jpg"
            high = Path(tmp) / "high.jpg"
            low.write_bytes(b"low")
            high.write_bytes(b"high")
            manager._upload_queue = [
                {"file_id": "low", "file_path": str(low), "file_name": "low.jpg", "file_size": 3, "priority": 0},
                {"file_id": "high", "file_path": str(high), "file_name": "high.jpg", "file_size": 4, "priority": 9},
            ]
            manager._uploading = True

            manager._upload_files()

        self.assertEqual(uploaded, ["high.jpg", "low.jpg"])

    def test_file_download_caches_remote_media_without_upload(self):
        class FakeResponse:
            def __init__(self, content):
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield self.content

        old_get = requests.get
        requests.get = lambda url, stream, timeout: FakeResponse(b"download-only")
        try:
            manager = MediaManager()
            with tempfile.TemporaryDirectory() as tmp:
                manager.storage_path = tmp
                reply = manager.handle_file_download(base_msg("file_download", {
                    "download_url": "http://media.local/download/photo/a.jpg",
                }))

                self.assertEqual(reply["data"]["result"], 0)
                files = reply["data"]["files"]
                self.assertEqual(len(files), 1)
                cache_path = Path(files[0]["cache_path"])
                self.assertTrue(cache_path.exists())
                self.assertEqual(cache_path.read_bytes(), b"download-only")
        finally:
            requests.get = old_get

    def test_file_delete_rejects_missing_ids(self):
        manager = MediaManager()
        reply = manager.handle_file_delete(base_msg("file_delete", {}))

        self.assertEqual(reply["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(reply["data"]["output"]["status"], "rejected")

    def test_file_delete_reports_remote_media_unsupported(self):
        manager = MediaManager()
        remote = build_remote_media_file("http://media.local/download/photo/a.jpg", "EDGEGW001")
        manager._scan_media_files = lambda: [remote]

        reply = manager.handle_file_delete(base_msg("file_delete", {
            "file_ids": [remote["file_id"]],
        }))

        item = reply["data"]["files"][0]
        self.assertEqual(item["result"], DEVICE_STATE_UNSUPPORTED)
        self.assertIn("remote media delete unsupported", item["msg"])

    def test_scan_local_files_is_recursive(self):
        manager = MediaManager()
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "flight-a" / "photo"
            nested.mkdir(parents=True)
            path = nested / "sample.jpg"
            path.write_bytes(b"jpeg")
            manager.storage_path = tmp

            files = manager._scan_local_files()

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["name"], "sample.jpg")
        self.assertEqual(files[0]["path"], "flight-a/photo/sample.jpg")

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

    def test_storage_config_output_uses_official_credentials_shape(self):
        output = storage_config_output({
            "provider": "minio",
            "endpoint": "http://minio.local",
            "bucket": "media",
            "region": "local",
            "object_key_prefix": "dock3/",
            "credentials": {
                "access_key_id": "ak",
                "access_key_secret": "sk",
                "expire": 3600,
                "security_token": "token",
            },
        })

        self.assertEqual(output["bucket"], "media")
        self.assertEqual(output["credentials"]["access_key_id"], "ak")
        self.assertEqual(output["credentials"]["access_key_secret"], "sk")
        self.assertEqual(output["credentials"]["expire"], 3600)
        self.assertEqual(output["object_key_prefix"], "dock3/")

    def test_upload_credentials_expiration_accepts_ttl_and_rejects_expired_epoch(self):
        self.assertFalse(MediaManager._credential_expired({
            "credentials": {"expire": 3600},
        }))
        self.assertTrue(MediaManager._credential_expired({
            "credentials": {"expire": int(time.time()) - 1},
        }))
        self.assertTrue(MediaManager._credential_expired({
            "output": {"credentials": {"expire": int(time.time() * 1000) - 1000}},
        }))

    def test_live_mock_lifecycle_emits_status_events(self):
        events = []
        states = []
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)
        manager.set_state_callback(states.append)
        video_id = "EDGEGW001/0/normal"

        start = manager.handle_live_start_push(base_msg("live_start_push", {
            "url": "mock://dock3/live",
            "url_type": 4,
            "video_id": video_id,
            "video_quality": 2,
        }))
        self.assertEqual(start["data"]["result"], 0)
        self.assertEqual(start["data"]["output"]["status"], "in_progress")
        self.assertNotIn("software_pipeline", start["data"])
        self.assertTrue(manager._live_streaming)
        live_status = states[-1]["property"]["live_status"]
        self.assertEqual(live_status[0]["video_id"], video_id)
        self.assertEqual(live_status[0]["status"], 1)
        self.assertEqual(live_status[0]["error_status"], 0)
        self.assertEqual(live_status[0]["video_quality"], 2)

        quality = manager.handle_live_set_quality(base_msg("live_set_quality", {
            "video_id": video_id,
            "video_quality": 3,
        }))
        self.assertEqual(quality["data"]["result"], 0)
        self.assertEqual(manager._live_quality, 3)
        self.assertEqual(states[-1]["property"]["live_status"][0]["video_quality"], 3)
        self.assertEqual(states[-1]["property"]["live_status"][0]["status"], 1)

        lens = manager.handle_live_lens_change(base_msg("live_lens_change", {
            "video_type": "zoom",
        }))
        self.assertEqual(lens["data"]["result"], 0)
        self.assertEqual(lens["data"]["video_id"], video_id)
        self.assertEqual(manager._live_video_type, "zoom")

        ir_lens = manager.handle_live_lens_change(base_msg("live_lens_change", {
            "video_type": "ir",
        }))
        self.assertEqual(ir_lens["data"]["result"], 0)
        self.assertEqual(manager._live_video_type, "ir")
        self.assertEqual(manager._live_source_key, "ir")
        self.assertEqual(events[-1]["data"]["source"], "/dev/video1")

        camera = manager.handle_live_camera_change(base_msg("live_camera_change", {
            "video_id": video_id,
            "camera_position": 1,
        }))
        self.assertEqual(camera["data"]["result"], 0)
        self.assertEqual(manager._live_camera_position, 1)

        stop = manager.handle_live_stop_push(base_msg("live_stop_push", {"video_id": video_id}))
        self.assertEqual(stop["data"]["result"], 0)
        self.assertFalse(manager._live_streaming)
        self.assertEqual(states[-1]["property"]["live_status"][0]["status"], 0)
        self.assertEqual(states[-1]["property"]["live_status"][0]["error_status"], 0)
        self.assertGreaterEqual(
            len([event for event in events if event.get("method") == "live_status_notify"]),
            4,
        )

    def test_live_lens_change_does_not_embed_photo_reply(self):
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        camera = FakeCameraController()
        manager.set_camera_controller(camera)
        video_id = "EDGEGW001/0/normal"

        start = manager.handle_live_start_push(base_msg("live_start_push", {
            "url": "mock://dock3/live",
            "url_type": 4,
            "video_id": video_id,
            "video_quality": 2,
        }))
        self.assertEqual(start["data"]["result"], 0)

        lens = manager.handle_live_lens_change(base_msg("live_lens_change", {
            "video_type": "zoom",
        }))

        self.assertEqual(lens["data"]["result"], 0)
        self.assertNotIn("photo", lens["data"])
        self.assertEqual(camera.calls, [])

    def test_photo_after_lens_change_does_not_pass_video_type_as_camera_type(self):
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        camera = FakeCameraController()
        manager.set_camera_controller(camera)

        result = manager._take_photo_after_lens_change(
            base_msg("live_lens_change", {"video_type": "gimbal"}),
            "EDGEGW001/0/gimbal",
            "gimbal",
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(camera.calls), 1)
        photo_data = camera.calls[0][1]["data"]
        self.assertNotIn("camera_type", photo_data)
        self.assertEqual(photo_data["video_type"], "gimbal")
        self.assertEqual(photo_data["live_lens_type"], "gimbal")

    def test_live_quality_change_reports_failure_when_ffmpeg_restart_fails(self):
        events = []
        states = []
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)
        manager.set_state_callback(states.append)
        manager._live_streaming = True
        manager._live_session_id = "live-session"
        manager._live_video_id = "EDGEGW001/0/normal"
        manager._live_url = "rtmp://cloud.example/live/stream"
        manager._live_protocol = 1
        manager._live_quality = 2
        manager._live_source_key = "normal"
        manager._live_video_type = "normal"

        def fail_start(*args, **kwargs):
            manager._live_streaming = False
            manager._live_pipeline.append({
                "step": "start_ffmpeg_process",
                "status": "failed",
                "reason": "ffmpeg not found",
            })
            return False

        manager._start_ffmpeg = fail_start

        reply = manager.handle_live_set_quality(base_msg("live_set_quality", {
            "video_id": "EDGEGW001/0/normal",
            "video_quality": 3,
        }))

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertEqual(reply["data"]["output"]["status"], "failed")
        self.assertFalse(manager._live_streaming)
        self.assertEqual(manager._live_session_id, "")
        self.assertEqual(events[-1]["method"], "live_status_notify")
        self.assertEqual(events[-1]["data"]["output"]["status"], "failed")
        self.assertEqual(states[-1]["property"]["live_status"][0]["status"], 0)
        self.assertEqual(states[-1]["property"]["live_status"][0]["error_status"], 1)

    def test_live_lens_change_reports_failure_when_ffmpeg_restart_fails(self):
        events = []
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)
        manager._live_streaming = True
        manager._live_session_id = "live-session"
        manager._live_video_id = "EDGEGW001/0/normal"
        manager._live_url = "rtmp://cloud.example/live/stream"
        manager._live_protocol = 1
        manager._live_quality = 2
        manager._live_source_key = "normal"
        manager._live_video_type = "normal"
        manager._start_ffmpeg = lambda *args, **kwargs: False

        reply = manager.handle_live_lens_change(base_msg("live_lens_change", {
            "video_type": "zoom",
        }))

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertEqual(reply["data"]["output"]["status"], "failed")
        self.assertFalse(manager._live_streaming)
        self.assertEqual(manager._live_session_id, "")
        self.assertEqual(events[-1]["data"]["output"]["status"], "failed")
        self.assertEqual(events[-1]["data"]["video_type"], "zoom")

    def test_live_capability_includes_official_camera_index(self):
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"

        reply = manager.handle_live_capability_query(base_msg("live_capability_query", {}))

        capacity = reply["data"]["live_capacity"]
        camera = capacity["device_list"][0]["camera_list"][0]
        video = camera["video_list"][0]
        self.assertEqual(camera["camera_index"], "52-0-0")
        self.assertEqual(video["video_index"], "normal-0")
        self.assertIn("zoom", video["switchable_video_types"])

    def test_live_capability_uses_configured_online_drone_sn(self):
        original = deepcopy(Config.all())
        Config.merge({
            "gateway": {"sn": "EDGEGW001"},
            "device": {
                "sub_devices": [
                    {"sn": "DRONE-A", "online": True},
                ],
            },
        })
        try:
            manager = MediaManager()
            manager.gateway_sn = "EDGEGW001"
            reply = manager.handle_live_capability_query(base_msg("live_capability_query", {}))
        finally:
            Config._data = original

        device = reply["data"]["live_capacity"]["device_list"][0]
        self.assertEqual(device["sn"], "DRONE-A")
        self.assertEqual(device["camera_list"][0]["camera_index"], "52-0-0")

    def test_live_capability_filters_unavailable_payload_states(self):
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_payload_state_provider(lambda: {
            "gimbal": {"connection_state": "disconnected"},
            "mapping_camera": {"connection_state": "configured", "video": {"reachable": False}},
        })

        reply = manager.handle_live_capability_query(base_msg("live_capability_query", {}))

        capacity = reply["data"]["live_capacity"]
        self.assertEqual(capacity["available_video_number"], 0)
        self.assertEqual(capacity["device_list"], [])

    def test_live_capability_includes_connected_payload_states(self):
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_payload_state_provider(lambda: {
            "gimbal": {"connection_state": "connected"},
            "mapping_camera": {"connection_state": "configured", "video": {"reachable": False}},
        })

        reply = manager.handle_live_capability_query(base_msg("live_capability_query", {}))

        capacity = reply["data"]["live_capacity"]
        self.assertEqual(capacity["available_video_number"], 1)
        self.assertEqual(len(capacity["device_list"][0]["camera_list"]), 1)

    def test_live_capability_includes_mapping_camera(self):
        original = deepcopy(Config.all())
        Config.merge({
            "payload": {
                "devices": {
                    "mapping_camera": {
                        "cloud_api": {
                            "type": 52,
                            "subtype": 0,
                            "gimbal_index": 1,
                            "video_index": "mapping-0",
                            "video_type": "normal",
                            "source_key": "mapping_camera",
                            "switchable_video_types": ["normal"],
                        },
                        "channels": {
                            "video": {
                                "transport": "rtsp",
                                "endpoint": "rtsp://192.168.5.74:8554/LIVE",
                            }
                        },
                    }
                }
            }
        })
        try:
            manager = MediaManager()
            manager.gateway_sn = "EDGEGW001"
            reply = manager.handle_live_capability_query(base_msg("live_capability_query", {}))
        finally:
            Config._data = original

        cameras = reply["data"]["live_capacity"]["device_list"][0]["camera_list"]
        mapping = next(item for item in cameras if item["camera_index"] == "52-0-1")
        self.assertEqual(mapping["device_id"], "mapping_camera")
        self.assertEqual(mapping["video_list"][0]["video_index"], "mapping-0")
        self.assertEqual(mapping["video_list"][0]["source_key"], "mapping_camera")
        self.assertEqual(mapping["video_list"][0]["endpoint"], "rtsp://192.168.5.74:8554/LIVE")

    def test_live_capability_includes_spectral_camera(self):
        original = deepcopy(Config.all())
        Config.merge({
            "payload": {
                "devices": {
                    "spectral_camera": {
                        "cloud_api": {
                            "type": 52,
                            "subtype": 0,
                            "gimbal_index": 2,
                            "video_index": "spectral-0",
                            "video_type": "normal",
                            "source_key": "spectral_camera",
                            "switchable_video_types": ["normal", "multispectral"],
                        },
                        "channels": {
                            "video": {
                                "transport": "rtp",
                                "endpoint": "rtp://0.0.0.0:23008",
                            }
                        },
                    }
                }
            }
        })
        try:
            manager = MediaManager()
            manager.gateway_sn = "EDGEGW001"
            reply = manager.handle_live_capability_query(base_msg("live_capability_query", {}))
        finally:
            Config._data = original

        cameras = reply["data"]["live_capacity"]["device_list"][0]["camera_list"]
        spectral = next(item for item in cameras if item["camera_index"] == "52-0-2")
        self.assertEqual(spectral["device_id"], "spectral_camera")
        self.assertEqual(spectral["video_list"][0]["video_index"], "spectral-0")
        self.assertEqual(spectral["video_list"][0]["source_key"], "spectral_camera")
        self.assertEqual(spectral["video_list"][0]["endpoint"], "rtp://0.0.0.0:23008")

    def test_gimbal_live_source_builds_rtsp_to_rtmp_ffmpeg_command(self):
        original = deepcopy(Config.all())
        Config.merge({
            "livestream": {"default_video_type": "gimbal"},
            "media": {
                "video_sources": {
                    "gimbal": "rtsp://192.168.1.119:554/main",
                }
            },
        })
        try:
            manager = MediaManager()
            command = manager._build_ffmpeg_cmd(
                "rtmp://cloud.example/live/stream",
                1,
                3,
                "gimbal",
            )
        finally:
            Config._data = original

        self.assertIn("-rtsp_transport", command)
        self.assertIn("rtsp://192.168.1.119:554/main", command)
        self.assertIn("-f", command)
        self.assertIn("flv", command)
        self.assertEqual(command[-1], "rtmp://cloud.example/live/stream")

    def test_webrtc_url_type_requires_dedicated_adapter(self):
        original = deepcopy(Config.all())
        Config.merge({
            "media": {
                "video_sources": {
                    "gimbal": "rtsp://192.168.1.119:554/main",
                }
            },
        })
        try:
            manager = MediaManager()
            with self.assertRaises(ValueError) as error:
                manager._build_ffmpeg_cmd(
                    "https://cloud.example/live/webrtc",
                    4,
                    3,
                    "gimbal",
                )
        finally:
            Config._data = original

        self.assertIn("WebRTC", str(error.exception))
        self.assertIn("专用直播适配器", str(error.exception))

    def test_live_start_rejects_real_webrtc_and_resets_state(self):
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"

        reply = manager.handle_live_start_push(base_msg("live_start_push", {
            "url": "https://cloud.example/live/webrtc",
            "url_type": 4,
            "video_id": "EDGEGW001/0/gimbal",
            "video_quality": 3,
        }))

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertEqual(reply["data"]["output"]["status"], "failed")
        self.assertIn("url_type=4", reply["data"]["reason"])
        self.assertFalse(manager._live_streaming)
        self.assertEqual(manager._live_session_id, "")
        self.assertEqual(manager._live_video_id, "")
        self.assertEqual(manager._live_url, "")

    def test_spectral_camera_live_source_builds_rtp_to_rtmp_ffmpeg_command(self):
        original = deepcopy(Config.all())
        Config.merge({
            "media": {
                "video_sources": {
                    "spectral_camera": "rtp://0.0.0.0:23008",
                }
            },
        })
        try:
            manager = MediaManager()
            command = manager._build_ffmpeg_cmd(
                "rtmp://cloud.example/live/spectral",
                1,
                3,
                "spectral_camera",
            )
        finally:
            Config._data = original

        self.assertIn("-protocol_whitelist", command)
        self.assertIn("rtp://0.0.0.0:23008", command)
        self.assertEqual(command[-1], "rtmp://cloud.example/live/spectral")

    def test_mapping_camera_video_id_selects_mapping_rtsp_source(self):
        original = deepcopy(Config.all())
        Config.merge({
            "media": {
                "video_sources": {
                    "mapping_camera": "rtsp://192.168.5.74:8554/LIVE",
                }
            },
            "payload": {
                "devices": {
                    "mapping_camera": {
                        "cloud_api": {
                            "type": 52,
                            "subtype": 0,
                            "gimbal_index": 1,
                            "video_index": "mapping-0",
                            "source_key": "mapping_camera",
                        }
                    }
                }
            },
        })
        try:
            manager = MediaManager()
            source_key = manager._live_source_key_from_video_id("EDGEGW001/52-0-1/mapping-0")
            command = manager._build_ffmpeg_cmd(
                "rtmp://cloud.example/live/mapping",
                1,
                3,
                source_key,
            )
        finally:
            Config._data = original

        self.assertEqual(source_key, "mapping_camera")
        self.assertIn("rtsp://192.168.5.74:8554/LIVE", command)
        self.assertEqual(command[-1], "rtmp://cloud.example/live/mapping")

    def test_live_capability_uses_configured_protocols(self):
        original = deepcopy(Config.all())
        Config.merge({
            "livestream": {
                "protocols": ["webrtc", "rtmp"],
                "adapters": {
                    "webrtc": {"enabled": False},
                },
            }
        })
        try:
            manager = MediaManager()
            reply = manager.handle_live_capability_query(base_msg("live_capability_query", {}))
        finally:
            Config._data = original

        self.assertEqual(reply["data"]["supported_protocols"], ["rtmp"])
        self.assertEqual(reply["data"]["supported_url_types"], [1])

    def test_old_ffmpeg_monitor_does_not_clear_restarted_stream(self):
        class FinishedProcess:
            returncode = 0

            def communicate(self):
                return b"", b""

        manager = MediaManager()
        old_process = FinishedProcess()
        new_process = FinishedProcess()
        manager._ffmpeg_process = new_process
        manager._live_streaming = True
        manager._live_session_id = "new-live"

        manager._ffmpeg_monitor(old_process)

        self.assertIs(manager._ffmpeg_process, new_process)
        self.assertTrue(manager._live_streaming)
        self.assertEqual(manager._live_session_id, "new-live")

    def test_live_start_accepts_gimbal_video_type(self):
        ok, normalized, errors = validate_live_start_payload({
            "url": "rtmp://cloud.example/live/stream",
            "url_type": 1,
            "video_id": "EDGEGW001/0/gimbal",
            "video_quality": 3,
            "video_type": "gimbal",
        })

        self.assertTrue(ok)
        self.assertEqual(errors, [])
        self.assertEqual(normalized["video_type"], "gimbal")

    def test_live_start_ffmpeg_failure_resets_request_state(self):
        Config.merge({"livestream": {"default_video_type": "gimbal"}})
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        manager._start_ffmpeg = lambda *args, **kwargs: False

        reply = manager.handle_live_start_push(base_msg("live_start_push", {
            "url": "rtmp://cloud.example/live/stream",
            "url_type": 1,
            "video_id": "EDGEGW001/0/ir",
            "video_quality": 3,
        }))

        self.assertEqual(reply["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertFalse(manager._live_streaming)
        self.assertEqual(manager._live_session_id, "")
        self.assertEqual(manager._live_video_id, "")
        self.assertEqual(manager._live_url, "")
        self.assertEqual(manager._live_protocol, 4)
        self.assertEqual(manager._live_quality, 0)
        self.assertEqual(manager._live_video_type, "gimbal")
        self.assertEqual(manager._live_source_key, "gimbal")

    def test_production_rejects_mock_live_url(self):
        Config.merge({"runtime": {"mode": "production"}})
        try:
            manager = MediaManager()
            reply = manager.handle_live_start_push(base_msg("live_start_push", {
                "url": "mock://dock3/live",
                "url_type": 4,
                "video_id": "EDGEGW001/0/normal",
                "video_quality": 2,
            }))
        finally:
            Config.merge({"runtime": {"mode": "test"}})

        self.assertEqual(reply["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(reply["data"]["output"]["status"], "failed")

    def test_live_start_unsupported_protocol_reports_failure_and_clears_state(self):
        events = []
        states = []
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)
        manager.set_state_callback(states.append)

        reply = manager.handle_live_start_push(base_msg("live_start_push", {
            "url": "https://cloud.example/webrtc/whip",
            "url_type": 4,
            "video_id": "EDGEGW001/0/normal",
            "video_quality": 2,
        }))

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertEqual(reply["data"]["output"]["status"], "failed")
        self.assertFalse(manager._live_streaming)
        self.assertEqual(manager._live_session_id, "")
        self.assertEqual(manager._live_video_id, "")
        self.assertEqual(events[-1]["method"], "live_status_notify")
        self.assertEqual(events[-1]["data"]["output"]["status"], "failed")
        self.assertEqual(states[-1]["property"]["live_status"][0]["status"], 0)
        self.assertEqual(states[-1]["property"]["live_status"][0]["error_status"], 1)

    def test_upload_callback_keeps_official_nested_fields(self):
        events = []
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)

        manager.report_upload_complete({
            "object_key": "dock3/sample.jpg",
            "path": "sample.jpg",
            "name": "sample.jpg",
        })

        file_data = events[0]["data"]["file"]
        self.assertEqual(file_data["object_key"], "dock3/sample.jpg")
        self.assertIn("flight_id", file_data["ext"])
        self.assertIn("drone_model_key", file_data["ext"])
        self.assertIn("payload_model_key", file_data["ext"])
        self.assertIn("is_original", file_data["ext"])
        self.assertIn("gimbal_yaw_degree", file_data["metadata"])
        self.assertIn("absolute_altitude", file_data["metadata"])
        self.assertIn("relative_altitude", file_data["metadata"])
        self.assertIn("create_time", file_data["metadata"])
        self.assertIn("lat", file_data["metadata"]["shoot_position"])
        self.assertIn("lng", file_data["metadata"]["shoot_position"])

    def test_report_upload_complete_normalizes_url_object_key(self):
        events = []
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)

        manager.report_upload_complete({
            "object_key": "http://oss.local/bucket/path/sample.jpg",
            "name": "sample.jpg",
        })

        file_data = events[0]["data"]["file"]
        self.assertEqual(file_data["object_key"], "bucket/path/sample.jpg")
        self.assertEqual(file_data["path"], "http://oss.local/bucket/path/sample.jpg")

    def test_live_start_rejects_invalid_payload(self):
        ok, _, errors = validate_live_start_payload({"url": "", "video_id": ""})
        self.assertFalse(ok)
        self.assertTrue(errors)

        manager = MediaManager()
        reply = manager.handle_live_start(base_msg("live_start", {}))

        self.assertEqual(reply["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(reply["data"]["output"]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
