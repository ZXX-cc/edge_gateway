#!/usr/bin/env python3
"""Direct JZ speaker field-test helper.

This bypasses the MQTT gateway and talks to the speaker TCP/serial adapter
directly.  It is meant for on-site verification when the gateway service is
not running yet.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.payload.jz_speaker import JzSpeakerAdapter  # noqa: E402


OGG_CAPTURE = b"OggS"


def _load_yaml(path: str) -> Dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    try:
        import yaml
    except Exception:
        return {}
    try:
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _nested(data: Dict[str, Any], *keys: str, default=None):
    value = data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return default if value is None else value


def _speaker_control_config(args: argparse.Namespace) -> Dict[str, Any]:
    config = _load_yaml(args.config)
    return (
        _nested(
            config,
            "payload",
            "devices",
            "speaker",
            "channels",
            "control_audio",
            default={},
        )
        or {}
    )


def _adapter(args: argparse.Namespace) -> JzSpeakerAdapter:
    control = _speaker_control_config(args)
    tcp = control.get("tcp") if isinstance(control.get("tcp"), dict) else {}
    serial = control.get("serial") if isinstance(control.get("serial"), dict) else {}
    endpoint = str(control.get("endpoint") or "")
    return JzSpeakerAdapter(
        {
            "enabled": True,
            "host": args.host or tcp.get("host"),
            "tcp_port": args.port or tcp.get("tcp_port"),
            "transport": args.transport or control.get("transport") or "tcp",
            "endpoint": endpoint,
            "timeout_seconds": args.timeout or tcp.get("timeout_seconds") or serial.get("timeout_seconds"),
            "wait_reply": args.wait_reply if args.wait_reply else bool(tcp.get("wait_reply", serial.get("wait_reply", False))),
            "text_encoding": args.encoding or tcp.get("text_encoding") or serial.get("text_encoding") or "utf-8",
        }
    )


def _print_result(label: str, result) -> None:
    print(f"{label}: {json.dumps(result.as_output(), ensure_ascii=False)}")


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


def _read_ogg_page(stream: BinaryIO) -> Optional[bytes]:
    header = _read_exact(stream, 27)
    if not header:
        return None
    while header[:4] != OGG_CAPTURE:
        next_byte = stream.read(1)
        if not next_byte:
            return None
        header = header[1:] + next_byte
    segment_count = header[26]
    segment_table = _read_exact(stream, segment_count)
    if len(segment_table) != segment_count:
        return None
    body_size = sum(segment_table)
    body = _read_exact(stream, body_size)
    if len(body) != body_size:
        return None
    return header + segment_table + body


def _packets_from_ogg_page(page: bytes, partial: bytes) -> tuple[List[bytes], bytes]:
    segment_count = page[26]
    segment_table_start = 27
    body_start = segment_table_start + segment_count
    offset = body_start
    packets: List[bytes] = []
    current = partial
    for size in page[segment_table_start:body_start]:
        current += page[offset:offset + size]
        offset += size
        if size < 255:
            packets.append(current)
            current = b""
    return packets, current


def _ffmpeg_output_args(realtime: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    sample_rate = int(args.sample_rate or realtime.get("sample_rate") or 16000)
    channels = int(args.channels or realtime.get("channels") or 1)
    bitrate = str(args.bitrate or realtime.get("bitrate") or "32k")
    frame_duration = int(args.frame_duration_ms or realtime.get("frame_duration_ms") or 20)
    return [
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-c:a",
        "libopus",
        "-application",
        "voip",
        "-b:a",
        bitrate,
        "-vbr",
        "off",
        "-compression_level",
        "0",
        "-frame_duration",
        str(frame_duration),
        "-flush_packets",
        "1",
        "-f",
        "opus",
        "pipe:1",
    ]


def _microphone_ffmpeg_command(realtime: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
    command += _microphone_input_args(realtime, args)
    command += _ffmpeg_output_args(realtime, args)
    return command


def _test_tone_ffmpeg_command(realtime: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    duration = max(0.1, float(args.duration or 2.0))
    frequency = int(args.frequency or 880)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration={duration}",
    ]
    command += _ffmpeg_output_args(realtime, args)
    return command


def _stream_opus_packets(command: List[str], speaker: JzSpeakerAdapter, *, max_seconds: float = 0) -> int:
    print("ffmpeg:", " ".join(command[:-1]), "pipe:1")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sent = 0
    partial = b""
    sizes: List[int] = []
    started_at = time.monotonic()
    try:
        assert process.stdout is not None
        while True:
            if max_seconds > 0 and time.monotonic() - started_at >= max_seconds:
                break
            page = _read_ogg_page(process.stdout)
            if page is None:
                break
            packets, partial = _packets_from_ogg_page(page, partial)
            for packet in packets:
                if packet.startswith(b"OpusHead") or packet.startswith(b"OpusTags"):
                    continue
                result = speaker.send_opus_frame(packet)
                if not result.ok:
                    print(f"opus frame failed: {json.dumps(result.as_output(), ensure_ascii=False)}")
                    return sent
                sizes.append(len(packet))
                sent += 1
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        if process.stderr:
            stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
            if stderr:
                print(stderr)
        if sizes:
            avg_size = sum(sizes) / len(sizes)
            print(f"opus packet size: min={min(sizes)} max={max(sizes)} avg={avg_size:.1f}")
    return sent


def cmd_loop_text(args: argparse.Namespace) -> int:
    fixed = _speaker_control_config(args).get("fixed_text_loop") or {}
    text = args.text or fixed.get("text") or "固定文本循环播放测试"
    interval = args.interval if args.interval is not None else float(fixed.get("interval_seconds", 10) or 10)
    volume = args.volume if args.volume is not None else int(fixed.get("volume", 40) or 40)
    speed = args.speed if args.speed is not None else int(fixed.get("speed", 50) or 50)
    voice = args.voice or fixed.get("voice_type") or "female"
    speaker = _adapter(args)
    try:
        started_at = time.monotonic()
        count = 0
        print("loop-text started; press Ctrl+C to stop.")
        while True:
            count += 1
            _print_result(
                f"tts #{count}",
                speaker.play_tts(
                    text,
                    volume=volume,
                    speed=speed,
                    voice_type=voice,
                ),
            )
            if args.duration > 0 and time.monotonic() - started_at >= args.duration:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("interrupted; stopping speaker.")
    finally:
        _print_result("stop", speaker.stop_audio())
        speaker.close()
    return 0


def cmd_play_file(args: argparse.Namespace) -> int:
    audio_file = _speaker_control_config(args).get("audio_file_play") or {}
    filename = args.filename or audio_file.get("filename") or "01.wav"
    volume = args.volume if args.volume is not None else int(audio_file.get("volume", 40) or 40)
    play_mode = args.play_mode if args.play_mode is not None else int(audio_file.get("play_mode", 0) or 0)
    duration = args.duration if args.duration is not None else float(audio_file.get("duration_seconds", 0) or 0)
    speaker = _adapter(args)
    try:
        _print_result("set_volume", speaker.set_volume(volume))
        _print_result("set_play_mode", speaker.set_play_mode(play_mode))
        _print_result("play", speaker.play_audio(filename))
        if duration > 0:
            time.sleep(duration)
            _print_result("stop", speaker.stop_audio())
    finally:
        speaker.close()
    return 0


def cmd_list_files(args: argparse.Namespace) -> int:
    speaker = _adapter(args)
    try:
        _print_result("files", speaker.query_audio_files())
    finally:
        speaker.close()
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    speaker = _adapter(args)
    try:
        _print_result("stop", speaker.stop_audio())
    finally:
        speaker.close()
    return 0


def cmd_list_audio_devices(args: argparse.Namespace) -> int:
    input_type = args.input_type or "dshow"
    command = ["ffmpeg", "-list_devices", "true", "-f", input_type, "-i", "dummy"]
    print(" ".join(command))
    completed = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True)
    output = "\n".join(item for item in [completed.stdout, completed.stderr] if item)
    print(output)
    return 0


def _microphone_input_args(realtime: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    audio_device = args.audio_device or realtime.get("audio_device") or ""
    if not audio_device:
        raise ValueError("audio device is required; run list-audio-devices first or set realtime_voice.audio_device")
    input_type = str(args.input_type or realtime.get("input_type") or "dshow")
    command = [
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-rtbufsize",
        "16M",
        "-f",
        input_type,
    ]
    if input_type == "dshow":
        command += ["-audio_buffer_size", "50", "-i", f"audio={audio_device}"]
    else:
        command += ["-i", audio_device]
    return command


def cmd_record_mic_check(args: argparse.Namespace) -> int:
    control = _speaker_control_config(args)
    realtime = control.get("realtime_voice") or {}
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]
        command += _microphone_input_args(realtime, args)
        command += [
            "-t",
            str(max(0.1, float(args.duration or 5))),
            "-ar",
            str(int(args.sample_rate or realtime.get("sample_rate") or 16000)),
            "-ac",
            str(int(args.channels or realtime.get("channels") or 1)),
            str(output),
        ]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(" ".join(command))
    completed = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if completed.stdout:
        print(completed.stdout)
    if completed.stderr:
        print(completed.stderr)
    if completed.returncode == 0:
        print(f"recorded: {output}")
    return completed.returncode


def cmd_realtime_voice(args: argparse.Namespace) -> int:
    control = _speaker_control_config(args)
    realtime = control.get("realtime_voice") or {}
    volume = args.volume if args.volume is not None else int(realtime.get("volume", 40) or 40)
    speaker = _adapter(args)
    try:
        _print_result("set_volume", speaker.set_volume(volume))
        _print_result("realtime_start", speaker.start_realtime_speaking())
        if args.test_tone:
            command = _test_tone_ffmpeg_command(realtime, args)
        else:
            command = _microphone_ffmpeg_command(realtime, args)
        print("realtime voice started; press Ctrl+C to stop.")
        try:
            sent = _stream_opus_packets(command, speaker, max_seconds=float(args.duration or 0))
        except KeyboardInterrupt:
            sent = 0
            print("interrupted; stopping realtime voice.")
        print(f"opus packets sent: {sent}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        _print_result("realtime_stop", speaker.stop_realtime_speaking())
        speaker.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Direct field test for JZ speaker")
    parser.add_argument("--config", default="config/gateway.yaml")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--transport", default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--encoding", default=None)
    parser.add_argument("--wait-reply", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)

    loop_text = subparsers.add_parser("loop-text", help="Loop TTS text until Ctrl+C or duration")
    loop_text.add_argument("--text", default=None)
    loop_text.add_argument("--interval", type=float, default=None)
    loop_text.add_argument("--duration", type=float, default=0.0, help="0 means run until Ctrl+C")
    loop_text.add_argument("--volume", type=int, default=None)
    loop_text.add_argument("--speed", type=int, default=None)
    loop_text.add_argument("--voice", default=None)
    loop_text.set_defaults(func=cmd_loop_text)

    play_file = subparsers.add_parser("play-file", help="Play a stored audio file")
    play_file.add_argument("filename", nargs="?")
    play_file.add_argument("--play-mode", type=int, choices=[0, 1], default=None)
    play_file.add_argument("--duration", type=float, default=None, help="0 means do not auto-stop")
    play_file.add_argument("--volume", type=int, default=None)
    play_file.set_defaults(func=cmd_play_file)

    list_files = subparsers.add_parser("list-files", help="List stored audio files")
    list_files.set_defaults(func=cmd_list_files)

    stop = subparsers.add_parser("stop", help="Stop current playback")
    stop.set_defaults(func=cmd_stop)

    list_devices = subparsers.add_parser("list-audio-devices", help="List local ffmpeg audio input devices")
    list_devices.add_argument("--input-type", default="dshow")
    list_devices.set_defaults(func=cmd_list_audio_devices)

    record_mic = subparsers.add_parser("record-mic-check", help="Record local microphone to a WAV file for verification")
    record_mic.add_argument("--audio-device", default=None)
    record_mic.add_argument("--input-type", default=None)
    record_mic.add_argument("--sample-rate", type=int, default=None)
    record_mic.add_argument("--channels", type=int, default=None)
    record_mic.add_argument("--duration", type=float, default=5.0)
    record_mic.add_argument("--output", default="C:/tmp/speaker_mic_check.wav")
    record_mic.set_defaults(func=cmd_record_mic_check)

    realtime_voice = subparsers.add_parser("realtime-voice", help="Stream microphone audio as realtime OPUS frames")
    realtime_voice.add_argument("--audio-device", default=None)
    realtime_voice.add_argument("--input-type", default=None)
    realtime_voice.add_argument("--sample-rate", type=int, default=None)
    realtime_voice.add_argument("--channels", type=int, default=None)
    realtime_voice.add_argument("--bitrate", default=None)
    realtime_voice.add_argument("--frame-duration-ms", type=int, default=None)
    realtime_voice.add_argument("--volume", type=int, default=None)
    realtime_voice.add_argument("--duration", type=float, default=0.0, help="0 means run until Ctrl+C")
    realtime_voice.add_argument("--test-tone", action="store_true", help="Send generated test tone instead of microphone")
    realtime_voice.add_argument("--frequency", type=int, default=880)
    realtime_voice.set_defaults(func=cmd_realtime_voice)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
