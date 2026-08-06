#!/usr/bin/env python3
"""Photograph with payload, request upload credentials, and upload media.

Flow:
1. Trigger ViewLink payload photo over Dock3 services.
2. Try to find a new media item via file_list_get.
3. Request storage_config_get temporary credentials on requests/requests_reply.
4. Upload the media bytes to the returned MinIO/S3 endpoint with AWS SigV4.

If the payload stores photos only on its SD card and the gateway cannot expose
a download URL, pass --file to upload a local file after the photo command.
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

from mqtt_test_client import MqttRequestClient
from tool_config import add_config_argument, resolve_mqtt_settings


def data(reply: Any) -> dict[str, Any]:
    node = reply.get("data") if isinstance(reply, dict) else {}
    return node if isinstance(node, dict) else {}


def output(reply_data: dict[str, Any]) -> dict[str, Any]:
    node = reply_data.get("output")
    return node if isinstance(node, dict) else reply_data


def viewlink_result(reply: Any) -> dict[str, Any]:
    node = data(reply).get("viewlink_result")
    return node if isinstance(node, dict) else {}


def request_service(
    client: MqttRequestClient,
    method: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    check_viewlink: bool = False,
) -> dict[str, Any]:
    rc, reply = client.request(
        method,
        payload,
        timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0 or not isinstance(reply, dict):
        raise RuntimeError(f"{method} failed rc={rc}")
    if check_viewlink:
        result = viewlink_result(reply)
        if result.get("success") is not True:
            raise RuntimeError(f"{method} SDK result is not success: {result}")
    return reply


def trigger_viewlink_photo(client: MqttRequestClient, args) -> None:
    state_reply = request_service(client, "gateway_state_query", {"compact": True}, args.timeout)
    state = data(state_reply).get("state", {})
    if not isinstance(state, dict) or state.get("device") != "bound":
        raise RuntimeError(
            "gateway is unbound; complete the official organization requests flow before media upload checks"
        )
    status = request_service(client, "payload_status_query", {}, args.timeout)

    control = {}
    for payload in data(status).get("payloads", []):
        if isinstance(payload, dict) and payload.get("id") == "gimbal":
            channels = payload.get("channels") if isinstance(payload.get("channels"), dict) else {}
            control = channels.get("control") if isinstance(channels.get("control"), dict) else {}
            break
    if not control:
        raise RuntimeError("payload_status_query did not return gimbal control config")

    sdk_dir = args.sdk_dir or str(control.get("sdk_dir") or "")
    host = args.gimbal_ip or str(control.get("host") or "")
    port = args.gimbal_port or int(control.get("port") or 0)
    if not sdk_dir or not host or not port:
        raise RuntimeError("incomplete ViewLink config: sdk_dir/host/port required")

    request_service(client, "viewlink_initialize", {"sdk_dir": sdk_dir}, args.timeout, check_viewlink=True)
    request_service(
        client,
        "viewlink_connect",
        {"ip": host, "port": port, "conn_type": "tcp"},
        args.timeout,
        check_viewlink=True,
    )
    connected = request_service(client, "viewlink_is_connected", {}, args.timeout, check_viewlink=True)
    if viewlink_result(connected).get("connected") is not True:
        raise RuntimeError("ViewLink reports connected=false")

    request_service(client, "viewlink_set_record_mode", {"mode": "PHOTO"}, args.timeout, check_viewlink=True)
    time.sleep(args.photo_settle)
    photo = request_service(client, "viewlink_take_photo", {}, args.timeout, check_viewlink=True)
    print("payload photo ok: " + json.dumps(viewlink_result(photo), ensure_ascii=False))
    time.sleep(args.photo_wait)


def list_media(client: MqttRequestClient, timeout: float, page_size: int) -> list[dict[str, Any]]:
    reply = request_service(
        client,
        "file_list_get",
        {"media_type": "photo", "page": 1, "page_size": page_size},
        timeout,
    )
    files = data(reply).get("files") or data(reply).get("list") or []
    return [item for item in files if isinstance(item, dict)]


def media_identity(item: dict[str, Any]) -> str:
    return str(item.get("file_id") or item.get("fingerprint") or item.get("download_url") or item.get("path") or item.get("name") or "")


def find_new_media(
    client: MqttRequestClient,
    before_ids: set[str],
    args,
) -> dict[str, Any] | None:
    deadline = time.time() + args.media_wait
    latest: dict[str, Any] | None = None
    while time.time() < deadline:
        files = list_media(client, args.file_list_timeout, args.page_size)
        for item in files:
            if latest is None or int(item.get("create_time", 0) or 0) > int(latest.get("create_time", 0) or 0):
                latest = item
            identity = media_identity(item)
            if identity and identity not in before_ids:
                return item
        time.sleep(args.media_poll_interval)
    if args.allow_existing_media:
        return latest
    return None


def download_or_read_media(item: dict[str, Any], args) -> tuple[Path, bytes, str]:
    if args.file:
        path = Path(args.file).expanduser()
        blob = path.read_bytes()
        return path, blob, path.name

    local = item.get("file_path")
    if local and Path(str(local)).is_file():
        path = Path(str(local))
        return path, path.read_bytes(), path.name

    url = item.get("download_url") or item.get("path")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise RuntimeError(
            "media item has no local file_path/download_url; pass --file to upload a local file"
        )

    cache_dir = PROJECT_ROOT / "logs" / "media_upload_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name) or f"media-{int(time.time())}.jpg"
    path = cache_dir / name
    response = requests.get(url, timeout=args.download_timeout, verify=not args.no_verify_tls)
    response.raise_for_status()
    path.write_bytes(response.content)
    return path, response.content, name


def wait_requests_reply(
    client: MqttRequestClient,
    topic: str,
    previous_count: int,
    tid: str,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        messages = client.observed_by_topic.get(topic, [])
        for message in messages[previous_count:]:
            if isinstance(message, dict) and message.get("tid") == tid:
                return message
        remaining = deadline - time.time()
        client.observed_signal.wait(max(0.05, min(0.5, remaining)))
        client.observed_signal.clear()
    raise TimeoutError(f"timeout waiting storage_config_get requests_reply tid={tid}")


def request_storage_config(
    client: MqttRequestClient,
    args,
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
    print(f"storage_config_get published: tid={tid}, file={file_name}, size={file_size}")
    reply = wait_requests_reply(client, reply_topic, previous, tid, args.credential_timeout)
    reply_data = data(reply)
    if reply_data.get("result") != 0:
        raise RuntimeError("storage_config_get failed: " + json.dumps(reply, ensure_ascii=False))
    return reply_data


def redact_storage_config(config: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(config, ensure_ascii=False))
    creds = clone.get("credentials")
    if isinstance(creds, dict) and creds.get("access_key_secret"):
        creds["access_key_secret"] = "***"
    return clone


def quote_s3_path(path: str) -> str:
    return "/" + "/".join(urllib.parse.quote(part, safe="") for part in path.strip("/").split("/"))


def signing_key(secret: str, date_stamp: str, region: str) -> bytes:
    key = ("AWS4" + secret).encode("utf-8")
    for value in (date_stamp, region, "s3", "aws4_request"):
        key = hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()
    return key


def put_s3_object(
    config_data: dict[str, Any],
    object_key: str,
    body: bytes,
    content_type: str,
    *,
    verify_tls: bool,
    timeout: float,
) -> str:
    cfg = output(config_data)
    endpoint = str(cfg.get("endpoint") or "").rstrip("/")
    bucket = str(cfg.get("bucket") or "")
    region = str(cfg.get("region") or "us-east-1")
    creds = cfg.get("credentials") if isinstance(cfg.get("credentials"), dict) else {}
    access_key = str(creds.get("access_key_id") or "")
    secret_key = str(creds.get("access_key_secret") or "")
    token = str(creds.get("security_token") or "")
    if not endpoint or not bucket or not access_key or not secret_key:
        raise RuntimeError("storage_config_get missing endpoint/bucket/access_key/secret")

    parsed = urllib.parse.urlparse(endpoint)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(f"invalid endpoint: {endpoint}")

    object_path = f"{bucket.strip('/')}/{object_key.strip('/')}"
    canonical_uri = quote_s3_path(object_path)
    url = f"{parsed.scheme}://{parsed.netloc}{canonical_uri}"
    payload_hash = hashlib.sha256(body).hexdigest()
    now = dt.datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    host = parsed.netloc

    headers = {
        "host": host,
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

    response = requests.put(url, data=body, headers=headers, timeout=timeout, verify=verify_tls)
    if response.status_code not in {200, 201, 204}:
        raise RuntimeError(f"upload failed: status={response.status_code}, body={response.text[:500]}")
    return url


def object_key_from_config(config_data: dict[str, Any], file_name: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    prefix = str(output(config_data).get("object_key_prefix") or "").strip("/")
    safe_name = Path(file_name).name
    return f"{prefix}/{safe_name}" if prefix else safe_name


def run(args) -> int:
    reply_topic = f"thing/product/{args.sn}/requests_reply"
    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_payload_photo_upload",
        subscribe_events=True,
        observe_topics={reply_topic},
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        before_ids: set[str] = set()
        if not args.file:
            try:
                before_ids = {media_identity(item) for item in list_media(client, args.file_list_timeout, args.page_size)}
            except Exception as exc:
                print(f"pre-photo file_list_get skipped: {exc}", file=sys.stderr)

        if not args.skip_photo:
            trigger_viewlink_photo(client, args)
        else:
            print("skip payload photo by --skip-photo")

        item: dict[str, Any] = {}
        if not args.file:
            item = find_new_media(client, before_ids, args) or {}
            if not item:
                raise RuntimeError("no new media found after photo; pass --file to upload a local file")
            print("selected media: " + json.dumps({
                "name": item.get("name"),
                "file_id": item.get("file_id"),
                "download_url": item.get("download_url"),
                "source": item.get("source"),
            }, ensure_ascii=False))

        path, blob, file_name = download_or_read_media(item, args)
        if not blob:
            raise RuntimeError(f"empty upload file: {path}")
        print(f"upload source: {path} ({len(blob)} bytes)")

        config_data = request_storage_config(client, args, file_name, len(blob))
        print("temporary credentials: " + json.dumps(redact_storage_config(output(config_data)), ensure_ascii=False, indent=2))

        content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        key = object_key_from_config(config_data, file_name, args.object_key)
        uploaded_url = put_s3_object(
            config_data,
            key,
            blob,
            content_type,
            verify_tls=not args.no_verify_tls,
            timeout=args.upload_timeout,
        )
        print("platform upload ok")
        print(json.dumps({
            "bucket": output(config_data).get("bucket"),
            "object_key": key,
            "url": uploaded_url,
            "bytes": len(blob),
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Take payload photo, request temporary credentials, upload media to platform")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--file-list-timeout", type=float, default=60.0)
    parser.add_argument("--credential-timeout", type=float, default=30.0)
    parser.add_argument("--upload-timeout", type=float, default=120.0)
    parser.add_argument("--download-timeout", type=float, default=60.0)
    parser.add_argument("--media-wait", type=float, default=30.0)
    parser.add_argument("--media-poll-interval", type=float, default=3.0)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--file", default="", help="Local file to upload after photo command, used when payload media is not downloadable.")
    parser.add_argument("--object-key", default="", help="Override object key. Default: object_key_prefix/file_name.")
    parser.add_argument("--skip-photo", action="store_true")
    parser.add_argument("--allow-existing-media", action="store_true", help="Upload newest existing media if no new file appears.")
    parser.add_argument("--sdk-dir", default="")
    parser.add_argument("--gimbal-ip", default="")
    parser.add_argument("--gimbal-port", type=int, default=0)
    parser.add_argument("--photo-settle", type=float, default=0.5)
    parser.add_argument("--photo-wait", type=float, default=2.0)
    parser.add_argument("--no-verify-tls", action="store_true", help="Disable HTTPS certificate verification for download/upload.")
    parser.add_argument("--quiet-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    if args.page_size < 1:
        parser.error("--page-size must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
