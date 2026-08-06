#!/usr/bin/env python3
"""Validate payload photo upload from the platform side.

This script intentionally does not call file_list_get.  The platform-side
contract under test is:
1. Trigger payload photo through services, unless --skip-photo is used.
2. Wait for the gateway to request storage_config_get on requests.
3. Reply with either an upload_url or MinIO/S3 credentials.
4. Verify the object upload, then wait for file_upload_callback.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, build_client
from tool_config import add_config_argument, resolve_mqtt_credentials, resolve_mqtt_settings


class UploadCapture:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.condition = threading.Condition()
        self.records: list[dict[str, Any]] = []

    def add(self, path: str, headers: dict[str, str], body: bytes) -> None:
        parsed = urllib.parse.urlparse(path)
        name = urllib.parse.unquote(Path(parsed.path).name) or f"upload-{int(time.time())}"
        target = self.output_dir / f"{int(time.time() * 1000)}_{Path(name).name}"
        target.write_bytes(body)
        with self.condition:
            self.records.append({
                "path": path,
                "headers": headers,
                "size": len(body),
                "saved_to": str(target),
            })
            self.condition.notify_all()

    def wait(self, previous_count: int, timeout: float) -> dict[str, Any]:
        deadline = time.time() + timeout
        with self.condition:
            while time.time() < deadline:
                if len(self.records) > previous_count:
                    return self.records[-1]
                self.condition.wait(max(0.05, min(0.5, deadline - time.time())))
        raise TimeoutError("timeout waiting upload_url PUT")


def make_upload_handler(capture: UploadCapture):
    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length)
            capture.add(self.path, {k.lower(): v for k, v in self.headers.items()}, body)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def start_upload_receiver(args: argparse.Namespace) -> tuple[ThreadingHTTPServer, UploadCapture, str]:
    capture = UploadCapture(Path(args.output_dir))
    server = ThreadingHTTPServer((args.upload_bind, args.upload_port), make_upload_handler(capture))
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, name="upload-url-receiver", daemon=True)
    thread.start()

    advertised_host = args.upload_host or args.upload_bind
    if advertised_host in {"", "0.0.0.0", "::"}:
        raise RuntimeError("set --upload-host to an address reachable by the gateway")
    base_url = f"http://{advertised_host}:{port}"
    return server, capture, base_url


def resolve_media_storage_path(args: argparse.Namespace) -> Path:
    try:
        from tool_config import load_config
    except Exception:
        return PROJECT_ROOT / "data" / "media"

    config = load_config(args.config)
    media = config.get("media") if isinstance(config, dict) else {}
    configured = str((media or {}).get("storage_path") or "./data/media")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def prepare_local_media_file(args: argparse.Namespace) -> Path | None:
    if not args.prepare_local_file:
        return None
    storage = resolve_media_storage_path(args)
    storage.mkdir(parents=True, exist_ok=True)
    name = args.prepare_file_name or f"manual-upload-probe-{int(time.time() * 1000)}.jpg"
    target = storage / Path(name).name
    if args.prepare_file:
        source = Path(args.prepare_file).expanduser()
        target.write_bytes(source.read_bytes())
    else:
        # Tiny JPEG-like payload. The upload chain only needs bytes on disk.
        target.write_bytes(b"\xff\xd8\xff\xe0edge-gateway-minio-upload-test\xff\xd9")
    print(f"prepared local media file: {target}")
    return target


class FastPlatformResponder:
    """Reply to device-originated requests immediately from the MQTT callback."""

    def __init__(self, args: argparse.Namespace, upload_base_url: str):
        try:
            import paho.mqtt.client as mqtt_module
        except ImportError as exc:
            raise SystemExit("paho-mqtt is required. Install requirements-ubuntu.txt first.") from exc

        self.args = args
        self.upload_base_url = upload_base_url
        self.request_topic = f"thing/product/{args.sn}/requests"
        self.reply_topic = f"thing/product/{args.sn}/requests_reply"
        self.connected = threading.Event()
        self.storage_replied = threading.Event()
        self.storage_requests: list[dict[str, Any]] = []
        self.storage_outputs: list[dict[str, Any]] = []
        self.handled_tids: set[str] = set()
        self.client = build_client(mqtt_module, f"edge_gateway_fast_platform_{uuid.uuid4().hex[:8]}")
        if args.username:
            self.client.username_pw_set(args.username, args.password)

    def connect(self, timeout: float) -> bool:
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.args.host, self.args.port, keepalive=30)
        self.client.loop_start()
        return self.connected.wait(timeout)

    def disconnect(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(self.request_topic, qos=self.args.qos)
            print(f"fast platform subscribed -> {self.request_topic}")
        else:
            print(f"fast platform connect failed: rc={rc}", file=sys.stderr)
        self.connected.set()

    def _on_message(self, client, userdata, msg):
        try:
            request = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        if not isinstance(request, dict):
            return
        tid = str(request.get("tid") or "")
        if not tid or tid in self.handled_tids:
            return

        method = str(request.get("method") or "")
        data = platform_reply_data(request, self.args)
        if method == "storage_config_get":
            output, _object_key = build_storage_output(self.args, request, self.upload_base_url)
            data = {
                "result": 0,
                "output": output,
                **output,
            }
            self.storage_requests.append(request)
            self.storage_outputs.append(output)
            self.storage_replied.set()
        if data is None:
            return

        self.handled_tids.add(tid)
        reply = {
            "tid": tid,
            "bid": request.get("bid") or str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "gateway": request.get("gateway") or self.args.sn,
            "method": method,
            "data": data,
        }
        client.publish(self.reply_topic, json.dumps(reply, ensure_ascii=False), qos=self.args.qos)
        if method == "storage_config_get":
            print(f"fast replied storage_config_get with {self.args.storage_mode} credential, tid={tid}")
        else:
            print(f"fast replied {method}, tid={tid}")


def data_node(message: Any) -> dict[str, Any]:
    data = message.get("data") if isinstance(message, dict) else {}
    return data if isinstance(data, dict) else {}


def result_code(message: Any) -> int | None:
    data = data_node(message)
    value = data.get("result", message.get("result") if isinstance(message, dict) else None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def request_service(client: MqttRequestClient, method: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    rc, reply = client.request(method, payload, timeout, channel="services", allowed_results={0})
    if rc != 0 or not isinstance(reply, dict):
        raise RuntimeError(f"{method} failed rc={rc}")
    return reply


def trigger_photo(client: MqttRequestClient, args: argparse.Namespace) -> None:
    if args.skip_photo:
        print("skip photo trigger by --skip-photo")
        return
    if args.photo_method == "viewlink_take_photo":
        reply = request_service(client, "viewlink_take_photo", {}, args.timeout)
    else:
        reply = request_service(
            client,
            "camera_photo_take",
            {"camera_index": args.camera_index, "camera_type": args.camera_type},
            args.timeout,
        )
    print("photo command accepted:")
    print(json.dumps(reply, ensure_ascii=False, indent=2))


def wait_method(
    client: MqttRequestClient,
    args: argparse.Namespace,
    topic: str,
    method: str,
    previous_count: int,
    timeout: float,
) -> dict[str, Any]:
    handled_requests: set[str] = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for message in client.observed_by_topic.get(topic, [])[previous_count:]:
            if not isinstance(message, dict):
                continue
            maybe_reply_platform_request(client, args, message, handled_requests)
            if message.get("method") == method:
                return message
        remaining = deadline - time.time()
        client.observed_signal.wait(max(0.05, min(0.5, remaining)))
        client.observed_signal.clear()
    raise TimeoutError(f"timeout waiting {method} on {topic}")


def platform_reply_data(request: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
    method = request.get("method", "")
    if method == "airport_bind_status":
        devices = data_node(request).get("devices") or [{"sn": args.sn}]
        statuses = []
        for device in devices:
            sn = device.get("sn") if isinstance(device, dict) else args.sn
            statuses.append({
                "sn": sn or args.sn,
                "is_device_bind_organization": True,
                "organization_id": args.organization_id,
                "organization_name": args.organization_name,
                "device_callsign": sn or args.sn,
            })
        return {"result": 0, "output": {"bind_status": statuses}}

    if method == "airport_organization_get":
        return {"result": 0, "output": {"organization_name": args.organization_name}}

    if method == "airport_organization_bind":
        return {"result": 0, "output": {"err_infos": []}}

    return None


def maybe_reply_platform_request(
    client: MqttRequestClient,
    args: argparse.Namespace,
    request: dict[str, Any],
    handled_tids: set[str],
) -> None:
    tid = str(request.get("tid") or "")
    method = str(request.get("method") or "")
    if not tid or tid in handled_tids:
        return
    data = platform_reply_data(request, args)
    if data is None:
        return

    handled_tids.add(tid)
    reply_topic = f"thing/product/{args.sn}/requests_reply"
    reply = {
        "tid": tid,
        "bid": request.get("bid") or str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "gateway": request.get("gateway") or args.sn,
        "method": method,
        "data": data,
    }
    client.publish_json(reply_topic, reply)
    print(f"auto replied {method} -> {reply_topic}")


def request_file_name(request: dict[str, Any]) -> str:
    files = data_node(request).get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict) and item.get("name"):
                return Path(str(item["name"])).name
    return f"payload-photo-{int(time.time())}.jpg"


def request_file_size(request: dict[str, Any]) -> int:
    files = data_node(request).get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict):
                try:
                    return int(item.get("size") or 0)
                except (TypeError, ValueError):
                    return 0
    return 0


def build_storage_output(
    args: argparse.Namespace,
    request: dict[str, Any],
    upload_base_url: str,
) -> tuple[dict[str, Any], str]:
    file_name = request_file_name(request)
    object_key = args.object_key or f"{args.object_prefix.strip('/')}/{file_name}".strip("/")
    if args.storage_mode == "upload-url":
        upload_url = f"{upload_base_url}/upload/{urllib.parse.quote(object_key, safe='')}"
        return {
            "provider": "upload_url",
            "bucket": args.bucket,
            "region": args.region,
            "object_key_prefix": args.object_prefix,
            "object_key": object_key,
            "upload_url": upload_url,
            "mime_type": mimetypes.guess_type(file_name)[0] or "application/octet-stream",
        }, object_key

    if args.storage_mode == "fake-minio":
        return {
            "provider": "minio",
            "endpoint": upload_base_url,
            "bucket": args.bucket,
            "region": args.region,
            "object_key_prefix": args.object_prefix,
            "object_key": object_key,
            "credentials": {
                "access_key_id": args.s3_access_key or "fake-access-key",
                "access_key_secret": args.s3_secret_key or "fake-secret-key",
                "security_token": args.s3_session_token,
                "expire": int(time.time()) + args.credential_ttl,
            },
        }, object_key

    return {
        "provider": args.s3_provider,
        "endpoint": args.s3_endpoint.rstrip("/"),
        "bucket": args.bucket,
        "region": args.region,
        "object_key_prefix": args.object_prefix,
        "object_key": object_key,
        "credentials": {
            "access_key_id": args.s3_access_key,
            "access_key_secret": args.s3_secret_key,
            "security_token": args.s3_session_token,
            "expire": int(time.time()) + args.credential_ttl,
        },
    }, object_key


def redact(value: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(value, ensure_ascii=False))
    credentials = clone.get("credentials")
    if isinstance(credentials, dict):
        if credentials.get("access_key_secret"):
            credentials["access_key_secret"] = "***"
        if credentials.get("security_token"):
            credentials["security_token"] = "***"
    return clone


def callback_object_key(callback: dict[str, Any]) -> str:
    data = data_node(callback)
    file_node = data.get("file") if isinstance(data.get("file"), dict) else {}
    return str(file_node.get("object_key") or "")


def verify_minio_object(args: argparse.Namespace, object_key: str) -> dict[str, Any]:
    if not object_key:
        raise RuntimeError("missing object_key for MinIO verification")
    try:
        from minio import Minio
    except ImportError as exc:
        raise RuntimeError("minio SDK is required: python -m pip install minio") from exc

    parsed = urllib.parse.urlparse(args.s3_endpoint)
    if parsed.scheme:
        endpoint = parsed.netloc
        secure = parsed.scheme == "https"
    else:
        endpoint = args.s3_endpoint
        secure = False
    if not endpoint:
        raise RuntimeError("missing --s3-endpoint for MinIO verification")

    client = Minio(
        endpoint,
        access_key=args.s3_access_key,
        secret_key=args.s3_secret_key,
        session_token=args.s3_session_token or None,
        secure=secure,
        region=args.region or None,
    )

    deadline = time.time() + max(0.0, args.minio_verify_timeout)
    last_error: Exception | None = None
    while time.time() <= deadline:
        try:
            stat = client.stat_object(args.bucket, object_key)
            return {
                "bucket": args.bucket,
                "object_key": object_key,
                "size": getattr(stat, "size", None),
                "etag": getattr(stat, "etag", ""),
                "last_modified": str(getattr(stat, "last_modified", "")),
            }
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"MinIO object verification failed: {last_error}")


def reply_storage_config(
    client: MqttRequestClient,
    args: argparse.Namespace,
    request: dict[str, Any],
    output: dict[str, Any],
) -> None:
    reply_topic = f"thing/product/{args.sn}/requests_reply"
    data = {
        "result": 0,
        "output": output,
    }
    # Compatibility for the current gateway upload worker, which reads
    # credential fields directly from data while newer API replies nest them
    # under data.output.
    data.update(output)
    message = {
        "tid": request.get("tid") or str(uuid.uuid4()),
        "bid": request.get("bid") or str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "gateway": args.sn,
        "method": "storage_config_get",
        "data": data,
    }
    client.publish_json(reply_topic, message)
    print("storage_config_get reply:")
    print(json.dumps({"topic": reply_topic, "data": redact(output)}, ensure_ascii=False, indent=2))


def wait_callback(client: MqttRequestClient, args: argparse.Namespace, previous_count: int) -> dict[str, Any]:
    topic = f"thing/product/{args.sn}/events"
    return wait_method(client, args, topic, "file_upload_callback", previous_count, args.callback_timeout)


def trigger_upload_queue(client: MqttRequestClient, args: argparse.Namespace) -> None:
    if args.skip_upload_trigger:
        print("skip upload trigger by --skip-upload-trigger")
        return
    payload = {"flight_id": args.flight_id}
    reply = request_service(client, args.upload_trigger_method, payload, args.timeout)
    print("upload trigger accepted:")
    print(json.dumps(reply, ensure_ascii=False, indent=2))


def run(args: argparse.Namespace) -> int:
    if args.storage_mode == "both":
        for mode in ("upload-url", "s3"):
            print(f"========== run storage_mode={mode} ==========")
            mode_args = argparse.Namespace(**{**vars(args), "storage_mode": mode})
            rc = run(mode_args)
            if rc != 0:
                return rc
        return 0

    request_topic = f"thing/product/{args.sn}/requests"
    event_topic = f"thing/product/{args.sn}/events"
    server = None
    capture = None
    upload_base_url = ""
    if args.storage_mode in {"upload-url", "fake-minio"}:
        server, capture, upload_base_url = start_upload_receiver(args)
        print(f"{args.storage_mode} receiver listening at {upload_base_url}")
    responder = FastPlatformResponder(args, upload_base_url)
    if not responder.connect(args.timeout):
        print("fast platform responder connect timeout", file=sys.stderr)
        if server is not None:
            server.shutdown()
            server.server_close()
        return 2

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_platform_upload_test",
        subscribe_events=True,
        observe_topics={event_topic},
        username=args.username,
        password=args.password,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        previous_events = client.observed_count(event_topic)
        previous_puts = len(capture.records) if capture else 0

        prepare_local_media_file(args)
        trigger_photo(client, args)
        if args.photo_wait > 0:
            time.sleep(args.photo_wait)
        trigger_upload_queue(client, args)
        print(f"waiting gateway storage_config_get on {request_topic}")
        if not responder.storage_replied.wait(args.credential_timeout):
            raise TimeoutError(f"timeout waiting storage_config_get on {request_topic}")
        request = responder.storage_requests[-1]
        output = responder.storage_outputs[-1]
        object_key = str(output.get("object_key") or "")
        print("storage_config_get request/reply:")
        print(json.dumps({
            "tid": request.get("tid"),
            "file_name": request_file_name(request),
            "file_size": request_file_size(request),
            "reply": redact(output),
        }, ensure_ascii=False, indent=2))

        upload_record = None
        if args.storage_mode in {"upload-url", "fake-minio"}:
            assert capture is not None
            upload_record = capture.wait(previous_puts, args.upload_timeout)
            print(f"{args.storage_mode} PUT received:")
            print(json.dumps(upload_record, ensure_ascii=False, indent=2))
        else:
            print("s3/minio mode: object upload must be verified in object storage or by gateway logs")

        callback = wait_callback(client, args, previous_events)
        if result_code(callback) not in {None, 0}:
            raise RuntimeError("file_upload_callback result is not ok: " + json.dumps(callback, ensure_ascii=False))
        print("file_upload_callback received:")
        print(json.dumps(callback, ensure_ascii=False, indent=2))

        minio_verified = None
        if args.verify_minio_sdk:
            verify_key = callback_object_key(callback) or object_key
            minio_verified = verify_minio_object(args, verify_key)
            print("MinIO object verified:")
            print(json.dumps(minio_verified, ensure_ascii=False, indent=2))

        print(json.dumps({
            "status": "ok",
            "storage_mode": args.storage_mode,
            "object_key": object_key,
            "uploaded": upload_record,
            "minio_verified": minio_verified,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.disconnect()
        responder.disconnect()
        if server is not None:
            server.shutdown()
            server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Platform-side payload photo upload chain test without file_list_get."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--credential-timeout", type=float, default=90.0)
    parser.add_argument("--upload-timeout", type=float, default=180.0)
    parser.add_argument("--callback-timeout", type=float, default=90.0)
    parser.add_argument("--photo-method", choices=["camera_photo_take", "viewlink_take_photo"], default="camera_photo_take")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-type", default="normal")
    parser.add_argument("--photo-wait", type=float, default=3.0)
    parser.add_argument("--skip-photo", action="store_true", help="Only wait for gateway upload requests/callbacks.")
    parser.add_argument("--upload-trigger-method", default="upload_flighttask_media_prioritize")
    parser.add_argument("--skip-upload-trigger", action="store_true")
    parser.add_argument("--flight-id", default="payload-photo-chain-test")
    parser.add_argument("--organization-id", default="org_sim_001")
    parser.add_argument("--organization-name", default="模拟组织")
    parser.add_argument("--storage-mode", choices=["upload-url", "s3", "fake-minio", "both"], default="upload-url")
    parser.add_argument("--upload-bind", default="0.0.0.0")
    parser.add_argument("--upload-host", default="", help="Address in upload_url that the gateway can reach.")
    parser.add_argument("--upload-port", type=int, default=0)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "logs" / "platform_upload_receiver"))
    parser.add_argument("--prepare-local-file", action="store_true", help="Create a local media file before triggering upload.")
    parser.add_argument("--prepare-file", default="", help="Existing file to copy into media.storage_path before upload.")
    parser.add_argument("--prepare-file-name", default="", help="Name for the generated/copied media file.")
    parser.add_argument("--bucket", default="payload-media-test")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--object-prefix", default="payload-photo-chain")
    parser.add_argument("--object-key", default="")
    parser.add_argument("--s3-provider", default="minio")
    parser.add_argument("--s3-endpoint", default="")
    parser.add_argument("--s3-access-key", default="")
    parser.add_argument("--s3-secret-key", default="")
    parser.add_argument("--s3-session-token", default="")
    parser.add_argument("--credential-ttl", type=int, default=3600)
    parser.add_argument("--verify-minio-sdk", action="store_true", help="Verify uploaded object with the minio SDK after callback.")
    parser.add_argument("--minio-verify-timeout", type=float, default=30.0)
    parser.add_argument("--quiet-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.username, args.password = resolve_mqtt_credentials(args)
    if args.storage_mode in {"s3", "both"}:
        missing = [
            name for name in ("s3_endpoint", "s3_access_key", "s3_secret_key")
            if not getattr(args, name)
        ]
        if missing:
            parser.error("--storage-mode s3 requires " + ", ".join("--" + name.replace("_", "-") for name in missing))
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
