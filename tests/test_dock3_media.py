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
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT


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

    def handle_camera_photo_take(self, msg):
        self.calls.append(("camera_photo_take", msg))
        return {
            "tid": msg.get("tid"),
            "bid": msg.get("bid"),
            "gateway": msg.get("gateway"),
            "method": "camera_photo_take",
            "data": {"result": 0},
        }


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

    def test_live_mock_lifecycle_emits_status_events(self):
        events = []
        manager = MediaManager()
        manager.gateway_sn = "EDGEGW001"
        manager.set_event_callback(events.append)
        video_id = "EDGEGW001/0/normal"

        start = manager.handle_live_start_push(base_msg("live_start_push", {
            "url": "mock://dock3/live",
            "url_type": 4,
            "video_id": video_id,
            "video_quality": 2,
        }))
        self.assertEqual(start["data"]["result"], 0)
        self.assertEqual(start["data"]["output"]["status"], "in_progress")
        self.assertTrue(manager._live_streaming)

        quality = manager.handle_live_set_quality(base_msg("live_set_quality", {
            "video_id": video_id,
            "video_quality": 3,
        }))
        self.assertEqual(quality["data"]["result"], 0)
        self.assertEqual(manager._live_quality, 3)

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
        self.assertGreaterEqual(
            len([event for event in events if event.get("method") == "live_status_notify"]),
            4,
        )

    def test_live_lens_change_takes_photo_directly_without_camera_mode_switch(self):
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
        self.assertEqual(lens["data"]["photo"]["status"], "ok")
        self.assertEqual([call[0] for call in camera.calls], ["camera_photo_take"])
        photo_data = camera.calls[0][1]["data"]
        self.assertEqual(photo_data["trigger"], "live_lens_change")
        self.assertEqual(photo_data["camera_type"], "zoom")
        self.assertEqual(photo_data["video_id"], video_id)

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
        self.assertEqual(reply["data"]["output"]["status"], "rejected")

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
