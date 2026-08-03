"""JZ speaker direct serial protocol adapter.

Protocol source: ``喊话器数据直连说明.docx`` and ``喊话器协议表2.4.xlsx``.
The device uses 115200 8N1 UART and frames shaped as:

``5A 5A 77 + length(2) + ack + seq + func1 + func2 + data + 00 23``.
"""
from __future__ import annotations

import time
import socket
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlparse

from src.utils.logger import get_logger

try:
    import serial  # type: ignore
except Exception:  # pragma: no cover - exercised on hosts without pyserial.
    serial = None


logger = get_logger("payload.jz_speaker")

FRAME_HEAD = b"\x5A\x5A\x77"
REPLY_HEAD = b"\x5B\x5B\x77"
FRAME_TAIL = b"\x00\x23"
MAX_TEXT_BYTES = 1000
MAX_CHUNK_BYTES = 400
OPUS_FRAME_BYTES = 80
MAX_OPUS_FRAME_BYTES = 400

MODEL_NAMES = {
    0x11: "H1E",
    0x12: "H10",
    0x13: "H150s",
    0x14: "H10T",
    0x15: "H150T",
    0x16: "H1T",
    0x17: "U3",
}

PLAY_STATE_NAMES = {
    0x00: "idle",
    0x10: "music_playing",
    0x20: "tts_playing",
    0x30: "opus_playing",
    0x40: "realtime_speaking",
}

VOICE_TYPES = {
    "female": 0x01,
    "woman": 0x01,
    "default": 0x01,
    "male": 0x02,
    "man": 0x02,
    "english_female": 0x11,
    "english_male": 0x12,
    "dongbei": 0x31,
    "sichuan": 0x32,
    "henan": 0x33,
    "hunan": 0x34,
    "shaanxi": 0x35,
    "cantonese": 0x36,
    "russian": 0x41,
    "french": 0x42,
    "german": 0x43,
    "korean": 0x44,
    "italian": 0x45,
    "polish": 0x46,
    "spanish": 0x47,
    "portuguese": 0x48,
}


@dataclass
class JzSpeakerResult:
    ok: bool
    simulated: bool = False
    message: str = ""
    frame_hex: str = ""
    reply_hex: str = ""
    output: Optional[Dict[str, Any]] = None

    def as_output(self) -> Dict[str, Any]:
        data = dict(self.output or {})
        data.update({
            "status": "ok" if self.ok else "failed",
            "simulated": self.simulated,
        })
        if self.message:
            data["message"] = self.message
        if self.frame_hex:
            data["frame_hex"] = self.frame_hex
        if self.reply_hex:
            data["reply_hex"] = self.reply_hex
        return data


def build_frame(func1: int, func2: int, data: bytes = b"\x00", *, ack: int = 0, seq: int = 0) -> bytes:
    payload = data if data else b"\x00"
    total_length = len(FRAME_HEAD) + 2 + 1 + 1 + 2 + len(payload) + len(FRAME_TAIL)
    return (
        FRAME_HEAD
        + total_length.to_bytes(2, "big")
        + bytes([ack & 0x01, seq & 0xFF, func1 & 0xFF, func2 & 0xFF])
        + payload
        + FRAME_TAIL
    )


def parse_frame(raw: bytes) -> Dict[str, Any]:
    if len(raw) < 12:
        return {"valid": False, "error": "reply too short", "raw_hex": raw.hex(" ")}
    if not raw.startswith(REPLY_HEAD) or not raw.endswith(FRAME_TAIL):
        return {"valid": False, "error": "invalid reply envelope", "raw_hex": raw.hex(" ")}
    length = int.from_bytes(raw[3:5], "big")
    if length != len(raw):
        return {
            "valid": False,
            "error": f"reply length mismatch: header={length} actual={len(raw)}",
            "raw_hex": raw.hex(" "),
        }
    data = raw[9:-2]
    return {
        "valid": True,
        "length": length,
        "ack": raw[5],
        "seq": raw[6],
        "func1": raw[7],
        "func2": raw[8],
        "data": data,
        "data_hex": data.hex(" "),
        "raw_hex": raw.hex(" "),
    }


class JzSpeakerAdapter:
    """Small synchronous adapter for the JZ UART protocol."""

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.config = dict(config or {})
        self.port = str(self.config.get("port") or "")
        self.host = str(self.config.get("host") or "")
        self.tcp_port = _as_int(self.config.get("tcp_port", self.config.get("port_tcp", self.config.get("network_port"))), 0)
        endpoint = str(self.config.get("endpoint") or "")
        if endpoint:
            parsed_host, parsed_port = _parse_tcp_endpoint(endpoint)
            self.host = self.host or parsed_host
            self.tcp_port = self.tcp_port or parsed_port
        self.baudrate = int(self.config.get("baudrate") or 115200)
        self.timeout = float(self.config.get("timeout_seconds") or 1.0)
        self.write_timeout = float(self.config.get("write_timeout_seconds") or self.timeout)
        self.wait_reply = bool(self.config.get("wait_reply", False))
        self.auto_connect = bool(self.config.get("auto_connect", False))
        self.text_encoding = str(self.config.get("text_encoding") or "utf-8")
        self.transport = str(self.config.get("transport") or "").lower()
        self._serial = None
        self._socket: Optional[socket.socket] = None
        self._seq = 0
        self._last_reply: Dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False) and (self._tcp_configured or self.port))

    @property
    def _tcp_configured(self) -> bool:
        return bool(self.host and self.tcp_port > 0)

    @property
    def _use_tcp(self) -> bool:
        if self.transport in {"tcp", "network", "ethernet", "socket"}:
            return True
        if self.transport in {"serial", "uart"}:
            return False
        return self._tcp_configured

    @property
    def connected(self) -> bool:
        if self._use_tcp:
            return bool(self._socket)
        return bool(self._serial and getattr(self._serial, "is_open", False))

    def connection_state(self) -> str:
        if not self.enabled:
            return "simulated"
        if self._use_tcp:
            return "connected" if self.connected else "tcp_configured"
        if serial is None:
            return "pyserial_missing"
        if self.connected:
            return "connected"
        return "configured"

    def connect(self) -> JzSpeakerResult:
        if not self.enabled:
            return JzSpeakerResult(True, simulated=True, message="speaker link is not configured")
        if self._use_tcp:
            if not self.connected:
                try:
                    sock = socket.create_connection((self.host, self.tcp_port), timeout=self.timeout)
                    sock.settimeout(self.timeout)
                    self._socket = sock
                except Exception as exc:
                    logger.error("JZ speaker TCP connect failed: %s", exc)
                    return JzSpeakerResult(False, message=str(exc))
        elif serial is None:
            return JzSpeakerResult(False, message="pyserial is not installed")
        elif not self.connected:
            try:
                self._serial = serial.Serial(
                    self.port,
                    self.baudrate,
                    bytesize=8,
                    parity="N",
                    stopbits=1,
                    timeout=self.timeout,
                    write_timeout=self.write_timeout,
                )
            except Exception as exc:
                logger.error("JZ speaker serial open failed: %s", exc)
                return JzSpeakerResult(False, message=str(exc))
        result = self.send_command(0x50, 0x51, b"\x00", ack=True)
        if result.ok and result.output and "model_code" in result.output:
            result.output["model"] = MODEL_NAMES.get(result.output["model_code"], "unknown")
        return result

    def close(self) -> None:
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                logger.debug("JZ speaker TCP close failed", exc_info=True)
            self._socket = None
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                logger.debug("JZ speaker serial close failed", exc_info=True)
            self._serial = None

    def set_volume(self, volume: Any) -> JzSpeakerResult:
        value = max(0, min(100, _as_int(volume, 80)))
        return self.send_command(0x52, 0x10, bytes([value]))

    def set_play_mode(self, play_mode: Any) -> JzSpeakerResult:
        value = 1 if _as_int(play_mode, 0) else 0
        return self.send_command(0x55, 0x00, bytes([value]))

    def play_audio(self, filename: str) -> JzSpeakerResult:
        payload = self._encode_text(str(filename or ""))
        if not payload:
            return JzSpeakerResult(False, message="audio filename is required")
        return self.send_command(0x52, 0x02, payload)

    def pause_audio(self) -> JzSpeakerResult:
        return self.send_command(0x52, 0x05, b"\x00")

    def resume_audio(self) -> JzSpeakerResult:
        return self.send_command(0x52, 0x04, b"\x00")

    def stop_audio(self) -> JzSpeakerResult:
        return self.send_command(0x52, 0x06, b"\x00")

    def replay(self) -> JzSpeakerResult:
        return self.resume_audio()

    def next_audio(self) -> JzSpeakerResult:
        return self.send_command(0x52, 0x08, b"\x00")

    def start_realtime_speaking(self) -> JzSpeakerResult:
        return self.send_command(0x59, 0xE1, b"\x00", ack=False)

    def stop_realtime_speaking(self) -> JzSpeakerResult:
        return self.send_command(0x59, 0xE2, b"\x00", ack=False)

    def send_opus_frame(self, frame: bytes) -> JzSpeakerResult:
        payload = bytes(frame or b"")
        if not payload or len(payload) > MAX_OPUS_FRAME_BYTES:
            return JzSpeakerResult(False, message=f"opus frame must be 1-{MAX_OPUS_FRAME_BYTES} bytes")
        return self.send_command(0x59, 0xE3, payload, ack=False)

    def query_audio_files(self) -> JzSpeakerResult:
        if not self.enabled:
            return JzSpeakerResult(
                True,
                simulated=True,
                message="speaker serial is not configured",
                frame_hex=build_frame(0x58, 0x01, b"\x00", ack=1, seq=self._next_seq()).hex(" "),
                output={"count": 0, "files": [], "query_supported": True},
            )
        result = self.send_command(0x58, 0x01, b"\x00", ack=True, collect_list=True)
        if not result.ok:
            return result
        frames = result.output.get("frames", []) if result.output else []
        return JzSpeakerResult(
            True,
            frame_hex=result.frame_hex,
            reply_hex=result.reply_hex,
            output=parse_audio_file_list_frames(frames),
        )

    def query_current_audio_file(self) -> JzSpeakerResult:
        result = self.send_command(0x53, 0x04, b"\x00", ack=True)
        if result.ok and result.output and isinstance(result.output.get("data"), bytes):
            result.output["filename"] = _decode_name(result.output["data"], self.text_encoding)
        return result

    def play_tts(self, text: str, *, speed: Any = None, volume: Any = None, voice_type: Any = None) -> JzSpeakerResult:
        encoded = self._encode_text(str(text or ""))[:MAX_TEXT_BYTES]
        if not encoded:
            return JzSpeakerResult(False, message="tts text is required")
        results: List[JzSpeakerResult] = []
        if volume is not None:
            results.append(self.set_volume(volume))
        if voice_type is not None:
            results.append(self.set_voice(voice_type))
        if speed is not None:
            results.append(self.set_speed(speed))
        results.append(self.send_command(0x57, 0x03, b"\x00"))
        for chunk in _chunks(encoded, MAX_CHUNK_BYTES):
            results.append(self.send_command(0x57, 0x05, chunk))
        results.append(self.send_command(0x57, 0x04, b"\x00"))
        failed = next((item for item in results if not item.ok), None)
        if failed:
            return failed
        return JzSpeakerResult(
            True,
            simulated=all(item.simulated for item in results),
            output={"text_bytes": len(encoded), "chunks": len(list(_chunks(encoded, MAX_CHUNK_BYTES)))},
        )

    def set_voice(self, voice_type: Any) -> JzSpeakerResult:
        if isinstance(voice_type, int):
            code = voice_type
        else:
            code = VOICE_TYPES.get(str(voice_type or "default").lower(), VOICE_TYPES["default"])
        return self.send_command(0x57, 0x01, bytes([code & 0xFF]))

    def set_speed(self, speed: Any) -> JzSpeakerResult:
        value = _as_int(speed, 50)
        if isinstance(speed, float) and 0 < speed <= 2:
            value = int(speed * 50)
        value = max(1, min(100, value))
        return self.send_command(0x57, 0x02, bytes([value]))

    def query_status(self) -> Dict[str, Any]:
        status: Dict[str, Any] = {
            "connection_state": self.connection_state(),
            "transport": "tcp" if self._use_tcp else "serial",
            "host": self.host,
            "tcp_port": self.tcp_port,
            "serial_port": self.port,
            "baudrate": self.baudrate,
            "last_reply": self._last_reply,
        }
        if not self.enabled or (not self._use_tcp and serial is None) or not self.connected:
            return status
        volume = self.send_command(0x53, 0x01, b"\x00", ack=True)
        play_state = self.send_command(0x53, 0x02, b"\x00", ack=True)
        if volume.ok and volume.output and "data" in volume.output:
            data = volume.output["data"]
            if data:
                status["volume"] = data[0]
        if play_state.ok and play_state.output and "data" in play_state.output:
            data = play_state.output["data"]
            if data:
                status["play_state"] = PLAY_STATE_NAMES.get(data[0], f"unknown_{data[0]}")
        return status

    def send_command(
        self,
        func1: int,
        func2: int,
        data: bytes = b"\x00",
        *,
        ack: Optional[bool] = None,
        collect_list: bool = False,
    ) -> JzSpeakerResult:
        should_wait = self.wait_reply if ack is None else bool(ack)
        frame = build_frame(func1, func2, data, ack=1 if should_wait else 0, seq=self._next_seq())
        if not self.enabled:
            return JzSpeakerResult(True, simulated=True, message="speaker serial is not configured", frame_hex=frame.hex(" "))
        if not self._use_tcp and serial is None:
            return JzSpeakerResult(False, message="pyserial is not installed", frame_hex=frame.hex(" "))
        if not self.connected:
            opened = self.connect()
            if not opened.ok:
                opened.frame_hex = frame.hex(" ")
                return opened
        try:
            self._write_frame(frame)
            reply = b""
            parsed: Dict[str, Any] = {}
            frames: List[Dict[str, Any]] = []
            if should_wait:
                if collect_list:
                    reply_chunks: List[bytes] = []
                    while True:
                        current = self._read_frame()
                        if not current:
                            break
                        reply_chunks.append(current)
                        item = parse_frame(current)
                        frames.append(item)
                        if not item.get("valid"):
                            return JzSpeakerResult(False, frame_hex=frame.hex(" "), reply_hex=current.hex(" "), message=item.get("error", "invalid reply"))
                        if item.get("func1") == 0x58 and item.get("func2") == 0xFF:
                            break
                    reply = b"".join(reply_chunks)
                    parsed = frames[-1] if frames else {"valid": False, "error": "empty reply"}
                else:
                    reply = self._read_frame()
                    parsed = parse_frame(reply)
                self._last_reply = parsed
                if not parsed.get("valid"):
                    return JzSpeakerResult(False, frame_hex=frame.hex(" "), reply_hex=reply.hex(" "), message=parsed.get("error", "invalid reply"))
            output = {"func1": func1, "func2": func2}
            if parsed:
                output.update(parsed)
                if frames:
                    output["frames"] = frames
                if parsed.get("data"):
                    output["model_code"] = parsed["data"][0]
            return JzSpeakerResult(True, frame_hex=frame.hex(" "), reply_hex=reply.hex(" "), output=output)
        except Exception as exc:
            logger.error("JZ speaker command failed: %s", exc)
            if self._use_tcp:
                self.close()
            return JzSpeakerResult(False, frame_hex=frame.hex(" "), message=str(exc))

    def _write_frame(self, frame: bytes) -> None:
        if self._use_tcp:
            self._socket.sendall(frame)
            return
        self._serial.write(frame)
        self._serial.flush()

    def _read_frame(self) -> bytes:
        deadline = time.monotonic() + self.timeout
        buffer = bytearray()
        while time.monotonic() < deadline:
            chunk = self._socket.recv(1) if self._use_tcp else self._serial.read(1)
            if not chunk:
                continue
            buffer.extend(chunk)
            if len(buffer) >= 5 and bytes(buffer[:3]) != REPLY_HEAD:
                del buffer[0]
                continue
            if len(buffer) >= 5:
                expected = int.from_bytes(buffer[3:5], "big")
                if expected > 0 and len(buffer) >= expected:
                    return bytes(buffer[:expected])
        return bytes(buffer)

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFF
        return self._seq

    def _encode_text(self, value: str) -> bytes:
        return value.encode(self.text_encoding, errors="ignore")


def _chunks(data: bytes, size: int) -> Iterable[bytes]:
    for offset in range(0, len(data), size):
        yield data[offset:offset + size]


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_tcp_endpoint(endpoint: str) -> tuple[str, int]:
    value = endpoint.strip()
    if not value:
        return "", 0
    parsed = urlparse(value if "://" in value else f"tcp://{value}")
    host = parsed.hostname or ""
    port = parsed.port or 0
    return host, port


def parse_audio_file_list_frames(frames: Iterable[Mapping[str, Any]], encoding: str = "utf-8") -> Dict[str, Any]:
    files: List[str] = []
    count = 0
    complete = False
    for frame in frames:
        if not frame.get("valid") or frame.get("func1") != 0x58:
            continue
        data = frame.get("data") or b""
        if frame.get("func2") == 0x00:
            count = data[0] if data else 0
        elif frame.get("func2") == 0x02:
            name = _decode_name(bytes(data), encoding)
            if name:
                files.append(name)
        elif frame.get("func2") == 0xFF:
            complete = True
    return {
        "count": count if count else len(files),
        "files": files,
        "complete": complete,
        "query_supported": True,
    }


def _decode_name(data: bytes, encoding: str = "utf-8") -> str:
    return bytes(data or b"").rstrip(b"\x00").decode(encoding, errors="ignore")
