#!/usr/bin/env python3
"""End-to-end payload photo and platform media upload validation.

Flow:
1. Trigger payload photo through the gateway.
2. Read a caller-provided local media file.
3. Request storage_config_get temporary upload credentials from platform.
4. Upload by either upload_url or MinIO/S3 compatible SigV4 credentials.
5. Optionally publish file_upload_callback for platform-side closure.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import sys
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from tool_config import add_config_argument, resolve_mqtt_settings


def data_node(message: Any) -> dict[str, Any]:
    data = message.get("data") if isinstance(message, dict) else {}
    return data if isinstance(data, dict) else {}


def output_node(message_or_data: Any) -> dict[str, Any]:
    data = data_node(message_or_data) if isinstance(message_or_data, dict) and "data" in message_or_data else message_or_data
    output = data.get("output") if isinstance(data, dict) else {}
    return output if isinstance(output, dict) else (data if isinstance(data, dict) else {})


def request_service(
    client: MqttRequestClient,
    method: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    allowed_results: set[int] | None = None,
) -> dict[str, Any]:
    rc, reply = client.request(
        method,
        payload,
        timeout,
        channel="services",
        allowed_results=allowed_results or {0},
    )
    if rc != 0 or not isinstance(reply, dict):
        raise RuntimeError(f"{method} failed rc={rc}")
    return reply


def trigger_photo(client: MqttRequestClient, args: argparse.Namespace) -> dict[str, Any] | None:
    if args.skip_photo:
        print("skip photo trigger by --skip-photo")
        return None

    if args.photo_method == "viewlink_take_photo":
        reply = request_service(client, "viewlink_take_photo", {}, args.timeout)
    else:
        payload = {
            "camera_index": args.camera_index,
            "camera_type": args.camera_type,
        }
        reply = request_service(client, "camera_photo_take", payload, args.timeout)

    print("photo trigger reply:")
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    time.sleep(args.photo_wait)
    return reply


def read_upload_file(args: argparse.Namespace) -> tuple[Path, bytes, str]:
    path = Path(args.file).expanduser()
    body = path.read_bytes()
    return path, body, path.name


def wait_requests_reply(
    client: MqttRequestClient,
    topic: str,
    previous_count: int,
    tid: str,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for message in client.observed_by_topic.get(topic, [])[previous_count:]:
            if isinstance(message, dict) and message.get("tid") == tid:
                return message

        remaining = deadline - time.time()
        client.observed_signal.wait(max(0.05, min(0.5, remaining)))
        client.observed_signal.clear()

    raise TimeoutError(f"timeout waiting storage_config_get requests_reply tid={tid}")


def request_storage_config(
    client: MqttRequestClient,
    args: argparse.Namespace,
    file_name: str,
    file_size: int,
) -> dict[str, Any]:
    request_topic = f"thing/product/{args.sn}/requests"
    reply_topic = f"thing/product/{args.sn}/requests_reply"
    previous = client.observed_count(reply_topic)
    tid = str(uuid.uuid4())

    message = {
        "tid": tid,
        "bid": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "gateway": args.sn,
        "method": "storage_config_get",
        "data": {
            "module": 0,
            "files": [{"name": file_name, "size": file_size}],
        },
        "need_reply": 1,
    }

    client.publish_json(request_topic, message)
    print(f"storage_config_get -> {request_topic}, tid={tid}, file={file_name}, size={file_size}")
    reply = wait_requests_reply(client, reply_topic, previous, tid, args.credential_timeout)
    if result_code(reply) != 0:
        raise RuntimeError("storage_config_get failed: " + json.dumps(reply, ensure_ascii=False))
    return reply


def redact_storage_config(config: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(config, ensure_ascii=False))
    output = clone.get("output") if isinstance(clone.get("output"), dict) else clone
    creds = output.get("credentials") if isinstance(output, dict) else None
    if isinstance(creds, dict):
        if creds.get("access_key_secret"):
            creds["access_key_secret"] = "***"
        if creds.get("security_token"):
            creds["security_token"] = "***"
    return clone


def quote_s3_path(path: str) -> str:
    return "/" + "/".join(urllib.parse.quote(part, safe="") for part in path.strip("/").split("/"))


def signing_key(secret: str, date_stamp: str, region: str) -> bytes:
    key = ("AWS4" + secret).encode("utf-8")
    for value in (date_stamp, region, "s3", "aws4_request"):
        key = hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()
    return key


def upload_by_url(upload_url: str, body: bytes, content_type: str, args: argparse.Namespace) -> str:
    headers = {"content-type": content_type}
    response = requests.put(
        upload_url,
        data=body,
        headers=headers,
        timeout=args.upload_timeout,
        verify=not args.no_verify_tls,
    )
    if response.status_code not in {200, 201, 204}:
        raise RuntimeError(f"upload_url PUT failed: status={response.status_code}, body={response.text[:500]}")
    return upload_url


def upload_by_s3_sigv4(
    storage: dict[str, Any],
    object_key: str,
    body: bytes,
    content_type: str,
    args: argparse.Namespace,
) -> str:
    endpoint = str(storage.get("endpoint") or "").rstrip("/")
    bucket = str(storage.get("bucket") or "")
    region = str(storage.get("region") or "us-east-1")
    credentials = storage.get("credentials") if isinstance(storage.get("credentials"), dict) else {}
    access_key = str(credentials.get("access_key_id") or "")
    secret_key = str(credentials.get("access_key_secret") or "")
    token = str(credentials.get("security_token") or "")

    if not endpoint or not bucket or not access_key or not secret_key:
        raise RuntimeError("missing endpoint/bucket/access_key_id/access_key_secret for MinIO/S3 upload")

    parsed = urllib.parse.urlparse(endpoint)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(f"invalid storage endpoint: {endpoint}")

    object_path = f"{bucket.strip('/')}/{object_key.strip('/')}"
    canonical_uri = quote_s3_path(object_path)
    url = f"{parsed.scheme}://{parsed.netloc}{canonical_uri}"
    payload_hash = hashlib.sha256(body).hexdigest()
    now = dt.datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    headers = {
        "host": parsed.netloc,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "content-type": content_type,
    }
    if token:
        headers["x-amz-security-token"] = token

    signed_header_names = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in signed_header_names)
    signed_headers = ";".join(signed_header_names)
    canonical_request = "\n".join([
        "PUT",
        canonical_uri,
        "",
        canonical_headers,
        signed_headers,
        payload_hash,
    ])
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(signing_key(secret_key, date_stamp, region), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    )

    response = requests.put(url, data=body, headers=headers, timeout=args.upload_timeout, verify=not args.no_verify_tls)
    if response.status_code not in {200, 201, 204}:
        raise RuntimeError(f"MinIO/S3 PUT failed: status={response.status_code}, body={response.text[:500]}")
    return url


def object_key_from_storage(storage: dict[str, Any], file_name: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    prefix = str(storage.get("object_key_prefix") or "").strip("/")
    safe_name = Path(file_name).name
    return f"{prefix}/{safe_name}" if prefix else safe_name


def upload_media(
    storage_reply: dict[str, Any],
    file_name: str,
    body: bytes,
    args: argparse.Namespace,
) -> tuple[str, str]:
    storage = output_node(storage_reply)
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    object_key = object_key_from_storage(storage, file_name, args.object_key)

    upload_url = str(storage.get("upload_url") or storage.get("url") or "")
    if upload_url:
        return object_key, upload_by_url(upload_url, body, content_type, args)

    return object_key, upload_by_s3_sigv4(storage, object_key, body, content_type, args)


def publish_file_upload_callback(
    client: MqttRequestClient,
    args: argparse.Namespace,
    media_item: dict[str, Any],
    file_name: str,
    object_key: str,
    uploaded_url: str,
) -> None:
    topic = f"thing/product/{args.sn}/events"
    event = {
        "tid": str(uuid.uuid4()),
        "bid": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "gateway": args.sn,
        "method": "file_upload_callback",
        "data": {
            "file": {
                "object_key": object_key,
                "path": uploaded_url,
                "name": file_name,
                "ext": {
                    "flight_id": args.flight_id,
                    "drone_model_key": str(media_item.get("drone_model_key", "")),
                    "payload_model_key": str(media_item.get("payload_model_key", "")),
                    "is_original": True,
                },
                "metadata": {
                    "gimbal_yaw_degree": media_item.get("gimbal_yaw_degree"),
                    "absolute_altitude": media_item.get("absolute_altitude"),
                    "relative_altitude": media_item.get("relative_altitude"),
                    "create_time": media_item.get("create_time", ""),
                    "shoot_position": media_item.get("shoot_position", {"lat": None, "lng": None}),
                },
                "cloud_to_cloud_id": str(media_item.get("cloud_to_cloud_id", "DEFAULT")),
            }
        },
    }
    client.publish_json(topic, event)
    print(f"file_upload_callback -> {topic}")
    print(json.dumps(event, ensure_ascii=False, indent=2))


def run(args: argparse.Namespace) -> int:
    reply_topic = f"thing/product/{args.sn}/requests_reply"
    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_photo_upload_chain",
        subscribe_events=True,
        observe_topics={reply_topic},
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        trigger_photo(client, args)

        media_item: dict[str, Any] = {}
        source_path, body, file_name = read_upload_file(args)
        if not body:
            raise RuntimeError(f"empty media file: {source_path}")
        print(f"upload source: {source_path} ({len(body)} bytes)")

        storage_reply = request_storage_config(client, args, file_name, len(body))
        print("temporary storage config:")
        print(json.dumps(redact_storage_config(output_node(storage_reply)), ensure_ascii=False, indent=2))

        object_key, uploaded_url = upload_media(storage_reply, file_name, body, args)
        print("upload ok:")
        print(json.dumps({
            "object_key": object_key,
            "url": uploaded_url,
            "bytes": len(body),
        }, ensure_ascii=False, indent=2))

        if args.publish_callback:
            publish_file_upload_callback(client, args, media_item, file_name, object_key, uploaded_url)

        return 0
    finally:
        client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Test payload photo -> platform temporary credential -> OSS/MinIO upload chain.")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--credential-timeout", type=float, default=30.0)
    parser.add_argument("--upload-timeout", type=float, default=120.0)
    parser.add_argument("--photo-method", choices=["camera_photo_take", "viewlink_take_photo"], default="camera_photo_take")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-type", default="normal")
    parser.add_argument("--photo-wait", type=float, default=3.0)
    parser.add_argument("--file", required=True, help="Local media file to upload through the official storage_config_get upload chain.")
    parser.add_argument("--skip-photo", action="store_true", help="Skip photo trigger, normally used together with --file.")
    parser.add_argument("--object-key", default="", help="Override object key. Default is object_key_prefix/file_name.")
    parser.add_argument("--flight-id", default="", help="Optional flight_id written into file_upload_callback.")
    parser.add_argument("--publish-callback", action="store_true", help="Publish file_upload_callback after upload succeeds.")
    parser.add_argument("--no-verify-tls", action="store_true", help="Disable HTTPS certificate validation for download/upload.")
    parser.add_argument("--quiet-stale", action="store_true")

    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
