#!/usr/bin/env python3
"""Validate Dock3 OSD and state reports published by the running gateway."""

import argparse
import json
import sys
import time
from pathlib import Path
from threading import Event, Lock
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ``python tools/<script>.py`` puts only ``tools/`` on sys.path. Add the
# repository root before importing the shared ``src`` package so the tool also
# works outside ``python -m unittest``.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, property_result
from tool_config import add_config_argument, resolve_mqtt_settings
from src.cloud_api.dock3_device import (
    GATEWAY_DOMAIN,
    GATEWAY_SUB_TYPE,
    GATEWAY_TYPE,
    THING_VERSION as DOCK3_THING_VERSION,
)
from src.cloud_api.dock3_properties import (
    DOCK3_PROPERTY_SPECS,
    official_property_names,
)


DEFAULT_DRONE_SN = "DRONESIM001"
THING_VERSION = DOCK3_THING_VERSION


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def unwrap_value(node: Any) -> Any:
    if isinstance(node, dict) and "value" in node:
        return node.get("value")
    return node


def get_path(data: Dict[str, Any], path: str) -> Tuple[bool, Any]:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = unwrap_value(node[part])
    return True, node


def type_label(expected: Any) -> str:
    if isinstance(expected, tuple):
        return "/".join(type_label(item) for item in expected)
    if expected == "number":
        return "number"
    if hasattr(expected, "__name__"):
        return expected.__name__
    return str(expected)


def check_field(
    data: Dict[str, Any],
    path: str,
    expected_type: Any,
    errors: List[str],
    *,
    allowed: Optional[Iterable[Any]] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> None:
    exists, value = get_path(data, path)
    if not exists:
        errors.append(f"missing data.{path}")
        return

    if expected_type == "number":
        ok = is_number(value)
    elif isinstance(expected_type, tuple):
        ok = isinstance(value, expected_type)
    else:
        ok = isinstance(value, expected_type)

    if not ok:
        errors.append(f"data.{path} expected {type_label(expected_type)}, actual={type(value).__name__}")
        return

    if allowed is not None and value not in set(allowed):
        errors.append(f"data.{path} expected one of {sorted(set(allowed))}, actual={value}")
    if min_value is not None and value < min_value:
        errors.append(f"data.{path} expected >= {min_value}, actual={value}")
    if max_value is not None and value > max_value:
        errors.append(f"data.{path} expected <= {max_value}, actual={value}")


def envelope_errors(payload: Dict[str, Any], expected_gateway: str) -> List[str]:
    errors: List[str] = []
    for name in ("tid", "bid", "gateway", "timestamp", "data"):
        if name not in payload:
            errors.append(f"missing {name}")
    if payload.get("gateway") != expected_gateway:
        errors.append(f"gateway mismatch: expected={expected_gateway}, actual={payload.get('gateway')}")
    if "timestamp" in payload and not isinstance(payload.get("timestamp"), int):
        errors.append("timestamp must be int milliseconds")
    if "data" in payload and not isinstance(payload.get("data"), dict):
        errors.append("data must be object")
    return errors


def validate_dock_osd(payload: Dict[str, Any], gateway_sn: str) -> List[str]:
    errors = envelope_errors(payload, gateway_sn)
    data = payload.get("data")
    if not isinstance(data, dict):
        return errors

    expected_osd = official_property_names(push_mode=0)
    for name in sorted(expected_osd):
        spec = DOCK3_PROPERTY_SPECS[name]
        expected_type = {
            "bool": bool,
            "enum_int": int,
            "int": int,
            "float": "number",
            "double": "number",
            "text": str,
            "struct": dict,
            "array": list,
        }[spec.value_type]
        check_field(
            data,
            name,
            expected_type,
            errors,
            allowed=spec.enum_values,
            min_value=spec.min_value,
            max_value=spec.max_value,
        )

    leaked_state = sorted(official_property_names(push_mode=1).intersection(data))
    if leaked_state:
        errors.append(f"dock OSD contains pushMode=1 properties: {leaked_state}")

    check_field(data, "network_state.type", int, errors, allowed={1, 2})
    check_field(data, "network_state.quality", int, errors, min_value=0, max_value=5)
    check_field(data, "network_state.rate", "number", errors, min_value=0)
    check_field(data, "drone_charge_state.capacity_percent", "number", errors, min_value=0, max_value=100)
    check_field(data, "drone_charge_state.state", int, errors, allowed={0, 1})
    check_field(data, "position_state.is_fixed", int, errors, allowed={0, 1, 2, 3})
    check_field(data, "position_state.quality", int, errors, allowed={0, 1, 2, 3, 4, 5, 10})
    check_field(data, "position_state.gps_number", int, errors, min_value=0)
    check_field(data, "position_state.rtk_number", int, errors, min_value=0)
    check_field(data, "home_position_is_valid", int, errors, allowed={0, 1, 2, 3})
    check_field(data, "heading", "number", errors, min_value=-180, max_value=180)
    check_field(data, "self_converge_coordinate.latitude", "number", errors, min_value=-90, max_value=90)
    check_field(data, "self_converge_coordinate.longitude", "number", errors, min_value=-180, max_value=180)
    check_field(data, "self_converge_coordinate.height", "number", errors)
    check_field(data, "wind_speed", "number", errors, min_value=0)
    check_field(data, "drc_state", int, errors, allowed={0, 1, 2})
    check_field(data, "maintain_status.maintain_status_array", list, errors)
    check_field(data, "air_conditioner.air_conditioner_state", int, errors, allowed=set(range(16)))
    check_field(data, "air_conditioner.switch_time", int, errors, min_value=0)
    check_field(data, "backup_battery.switch", int, errors, allowed={0, 1})
    check_field(data, "backup_battery.voltage", int, errors, min_value=0, max_value=30000)
    check_field(data, "storage.total", int, errors, min_value=0)
    check_field(data, "storage.used", int, errors, min_value=0)

    if "battery" in data or "putter_state" in data or "electricity" in data:
        errors.append("dock OSD must use Dock3 dock properties, not drone battery OSD")
    return errors


def validate_drone_osd(payload: Dict[str, Any], gateway_sn: str) -> List[str]:
    errors = envelope_errors(payload, gateway_sn)
    data = payload.get("data")
    if not isinstance(data, dict):
        return errors

    for field in (
        "latitude",
        "longitude",
        "height",
        "altitude",
        "horizontal_speed",
        "vertical_speed",
        "attitude_pitch",
        "attitude_roll",
        "attitude_head",
        "wind_speed",
    ):
        check_field(data, field, "number", errors)

    check_field(data, "latitude", "number", errors, min_value=-90, max_value=90)
    check_field(data, "longitude", "number", errors, min_value=-180, max_value=180)
    check_field(data, "horizontal_speed", "number", errors, min_value=0)

    check_field(data, "battery.capacity_percent", "number", errors, min_value=0, max_value=100)
    check_field(data, "battery.voltage", "number", errors, min_value=0)
    check_field(data, "position_state.gps_number", int, errors, min_value=0)
    check_field(data, "position_state.is_fixed", int, errors, allowed={0, 1, 2, 3})
    check_field(data, "position_state.quality", int, errors, min_value=0, max_value=5)
    check_field(data, "mode_code", int, errors, min_value=0)
    check_field(data, "gear", int, errors, allowed={0, 1})
    check_field(data, "flight_time", int, errors, min_value=0)
    check_field(data, "timestamp", int, errors, min_value=0)
    check_field(data, "return_home_power", int, errors, min_value=0, max_value=100)

    cameras = data.get("cameras")
    if cameras is not None:
        if not isinstance(cameras, list):
            errors.append("data.cameras expected list")
        else:
            for index, camera in enumerate(cameras):
                if not isinstance(camera, dict):
                    errors.append(f"data.cameras[{index}] expected object")
                    continue
                if not isinstance(camera.get("payload_index"), str) or not camera.get("payload_index"):
                    errors.append(f"data.cameras[{index}].payload_index expected non-empty str")
                if "camera_mode" in camera:
                    _check_inline_camera_field(camera, index, "camera_mode", int, errors, allowed={-1, 0, 1, 2, 3})
                if "photo_state" in camera:
                    _check_inline_camera_field(camera, index, "photo_state", int, errors, allowed={0, 1})
                if "recording_state" in camera:
                    _check_inline_camera_field(camera, index, "recording_state", int, errors, allowed={0, 1})
                if "record_time" in camera:
                    _check_inline_camera_field(camera, index, "record_time", int, errors, min_value=0)
                if "zoom_factor" in camera:
                    _check_inline_camera_field(camera, index, "zoom_factor", "number", errors, min_value=1)
                if "ir_zoom_factor" in camera:
                    _check_inline_camera_field(camera, index, "ir_zoom_factor", "number", errors, min_value=1)
                if "screen_split_enable" in camera:
                    _check_inline_camera_field(camera, index, "screen_split_enable", bool, errors)

    if "drone_charge_state" in data:
        errors.append("drone OSD must use drone telemetry, not dock drone_charge_state")
    return errors


def _check_inline_camera_field(
    camera: Dict[str, Any],
    index: int,
    name: str,
    expected_type: Any,
    errors: List[str],
    *,
    allowed: Optional[Iterable[Any]] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> None:
    value = camera.get(name)
    if expected_type == "number":
        ok = is_number(value)
    else:
        ok = isinstance(value, expected_type)
    if not ok:
        errors.append(
            f"data.cameras[{index}].{name} expected {type_label(expected_type)}, "
            f"actual={type(value).__name__}"
        )
        return
    if allowed is not None and value not in set(allowed):
        errors.append(f"data.cameras[{index}].{name} expected one of {sorted(set(allowed))}, actual={value}")
    if min_value is not None and value < min_value:
        errors.append(f"data.cameras[{index}].{name} expected >= {min_value}, actual={value}")
    if max_value is not None and value > max_value:
        errors.append(f"data.cameras[{index}].{name} expected <= {max_value}, actual={value}")


def live_drone_osd_errors(payload: Dict[str, Any]) -> List[str]:
    """判断 aircraft OSD 是否已包含真实 SITL/飞控动态遥测。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return ["drone OSD data must be object"]

    errors = []
    latitude = data.get("latitude", 0)
    longitude = data.get("longitude", 0)
    if not is_number(latitude) or not is_number(longitude) or (
        abs(float(latitude)) < 1e-7 and abs(float(longitude)) < 1e-7
    ):
        errors.append("latitude/longitude are still zero placeholders")

    battery = data.get("battery")
    voltage = battery.get("voltage", 0) if isinstance(battery, dict) else 0
    if not is_number(voltage) or float(voltage) <= 0:
        errors.append("battery.voltage is not live")

    position = data.get("position_state")
    gps_number = position.get("gps_number", 0) if isinstance(position, dict) else 0
    fixed_state = position.get("is_fixed", 0) if isinstance(position, dict) else 0
    if not isinstance(gps_number, int) or gps_number <= 0:
        errors.append("position_state.gps_number is not live")
    if fixed_state != 2:
        errors.append(f"position_state.is_fixed expected live fix=2, actual={fixed_state}")
    return errors


def validate_state(payload: Dict[str, Any], gateway_sn: str, expected_property: str) -> List[str]:
    errors = envelope_errors(payload, gateway_sn)
    data = payload.get("data")
    if not isinstance(data, dict):
        return errors

    if "property" in data:
        errors.append("state data must not contain legacy property wrapper")
        return errors
    if not data:
        errors.append("state data must not be empty")
        return errors

    internal_fields = {
        "flight_mode", "armed", "connection_status", "battery", "gps_fixed",
        "putter_state", "charging_state", "electricity",
    }
    leaked = sorted(
        (internal_fields | official_property_names(push_mode=0)).intersection(data)
    )
    if leaked:
        errors.append(f"state contains internal telemetry fields: {leaked}")
    return errors


def is_ignorable_legacy_state(payload: Dict[str, Any], errors: List[str]) -> bool:
    """
    Ignore known legacy state broadcasts left by old gateway processes.

    New Dock3 state samples must have ``bid`` and direct properties under ``data``. Older
    local tools published raw state changes such as ``{"reason":
    "property_changed", "data": {"cover_state": 0}}``.  Those can still be
    seen on a shared broker during E2E and should not fail the strict Dock3
    sample once a valid state has arrived.
    """
    if payload.get("reason") != "property_changed":
        return False
    return any("state contains internal telemetry fields" in error for error in errors)


def has_expected_state_property(payload: Dict[str, Any], expected_property: str) -> bool:
    if not expected_property:
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    return expected_property in data


def is_value_wrapped_object(node: Any) -> bool:
    return isinstance(node, dict) and "value" in node


def has_legacy_value_wrapped_fields(payload: Dict[str, Any]) -> bool:
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    return any(is_value_wrapped_object(value) for value in data.values())


def is_ignorable_legacy_osd(payload: Dict[str, Any], errors: List[str]) -> bool:
    """
    Ignore partial OSD samples published by old local helpers/gateway builds.

    Current gateway Dock3 OSD may wrap values as ``{"value": ...}``, but it
    still carries the full Dock3 required field set.  Older helpers published
    partial value-wrapped OSD snapshots on the same topics; when a shared broker
    still has those publishers running, they should be treated as stale noise
    rather than failing this strict current-format check.
    """
    if not has_legacy_value_wrapped_fields(payload):
        return False
    return any(error.startswith("missing data.") for error in errors)


def build_update_topo_payload(args) -> Dict[str, Any]:
    return {
        "domain": args.gateway_domain,
        "type": args.gateway_type,
        "sub_type": args.gateway_sub_type,
        "device_secret": args.gateway_secret,
        "nonce": args.gateway_nonce,
        "thing_version": args.thing_version,
        "sub_devices": [
            {
                "sn": args.drone_sn,
                "domain": args.drone_domain,
                "type": args.drone_type,
                "sub_type": args.drone_sub_type,
                "index": args.drone_index,
                "device_secret": args.drone_secret,
                "nonce": args.drone_nonce,
                "thing_version": args.thing_version,
            }
        ],
    }


class Dock3OsdStateClient(MqttRequestClient):
    def __init__(
        self,
        host: str,
        port: int,
        sn: str,
        drone_sn: str,
        qos: int,
        expected_state_property: str,
        quiet_stale: bool = False,
        require_live_drone: bool = False,
        observe_drone_osd: bool = True,
        mqtt_module=None,
    ):
        self.drone_sn = drone_sn
        self.expected_state_property = expected_state_property
        self.require_live_drone = require_live_drone
        self.changed = Event()
        self.lock = Lock()
        self.dock_osd_topic = f"thing/product/{sn}/osd"
        self.drone_osd_topic = f"thing/product/{drone_sn}/osd"
        self.state_topic = f"thing/product/{sn}/state"
        self.valid_dock_osd: List[Dict[str, Any]] = []
        self.valid_drone_osd: List[Dict[str, Any]] = []
        self.valid_state: List[Dict[str, Any]] = []
        self.expected_state: List[Dict[str, Any]] = []
        self.invalid_samples: List[str] = []
        self.ignored_legacy_samples: List[Dict[str, Any]] = []
        self.placeholder_drone_samples: List[Dict[str, Any]] = []
        observe_topics = {self.dock_osd_topic, self.state_topic}
        if observe_drone_osd:
            observe_topics.add(self.drone_osd_topic)
        super().__init__(
            host,
            port,
            sn,
            qos,
            quiet_stale=quiet_stale,
            client_id_prefix="edge_gateway_dock3_osd_state",
            observe_topics=observe_topics,
            mqtt_module=mqtt_module,
        )
        self.stats["ignored_observed_legacy"] = 0

    def stale_total(self) -> int:
        return super().stale_total() + int(self.stats.get("ignored_observed_legacy", 0))

    def _record_invalid(self, topic: str, errors: List[str], payload: Any) -> None:
        preview = json.dumps(payload, ensure_ascii=False)[:500]
        self.invalid_samples.append(f"{topic}: {'; '.join(errors)}; payload={preview}")

    def _record_ignored_legacy(self, topic: str, reason: str, payload: Any) -> None:
        self.stats["ignored_observed_legacy"] += 1
        sample: Dict[str, Any] = {"category": "legacy_observed", "topic": topic, "reason": reason}
        if isinstance(payload, dict):
            sample.update(
                {
                    "tid": payload.get("tid"),
                    "bid": payload.get("bid"),
                    "gateway": payload.get("gateway"),
                    "message_reason": payload.get("reason"),
                }
            )
            data = payload.get("data")
            if isinstance(data, dict):
                sample["data_keys"] = sorted(str(key) for key in data)[:20]
        self.ignored_legacy_samples.append(sample)
        if not self.quiet_stale:
            tid = payload.get("tid") if isinstance(payload, dict) else None
            print(f"ignored legacy observed sample <- {topic}, reason={reason}, tid={tid}")

    def _handle_invalid_json(self, topic: str, exc: Exception) -> None:
        if topic in self.observed_topics:
            with self.lock:
                self.invalid_samples.append(f"{topic}: invalid JSON: {exc}")
                self.changed.set()
            return
        super()._handle_invalid_json(topic, exc)

    def _handle_observed_message(self, topic: str, payload: Any) -> None:
        with self.lock:
            self.observed_by_topic.setdefault(topic, []).append(payload)
            self.stats["observed"] += 1
            self.observed_signal.set()

            if not isinstance(payload, dict):
                self._record_invalid(topic, ["payload must be object"], payload)
                self.changed.set()
                return

            if topic == self.dock_osd_topic:
                errors = validate_dock_osd(payload, self.sn)
                if errors and is_ignorable_legacy_osd(payload, errors):
                    self._record_ignored_legacy(topic, "legacy_value_wrapped_osd", payload)
                elif errors:
                    self._record_invalid(topic, errors, payload)
                else:
                    self.valid_dock_osd.append(payload)
                    print(f"valid dock osd <- {topic}, count={len(self.valid_dock_osd)}")

            elif topic == self.drone_osd_topic:
                errors = validate_drone_osd(payload, self.sn)
                if errors and is_ignorable_legacy_osd(payload, errors):
                    self._record_ignored_legacy(topic, "legacy_value_wrapped_osd", payload)
                elif errors:
                    self._record_invalid(topic, errors, payload)
                else:
                    live_errors = live_drone_osd_errors(payload) if self.require_live_drone else []
                    if live_errors:
                        self.placeholder_drone_samples.append({
                            "topic": topic,
                            "errors": live_errors,
                            "data": payload.get("data", {}),
                        })
                        if not self.quiet_stale:
                            print(f"waiting live drone osd <- {topic}: {'; '.join(live_errors)}")
                    else:
                        self.valid_drone_osd.append(payload)
                        print(f"valid drone osd <- {topic}, count={len(self.valid_drone_osd)}")

            elif topic == self.state_topic:
                errors = validate_state(payload, self.sn, self.expected_state_property)
                if errors and is_ignorable_legacy_state(payload, errors):
                    self._record_ignored_legacy(topic, "legacy_or_irrelevant_state", payload)
                elif errors:
                    self._record_invalid(topic, errors, payload)
                else:
                    self.valid_state.append(payload)
                    if has_expected_state_property(payload, self.expected_state_property):
                        self.expected_state.append(payload)
                        print(f"valid expected state <- {topic}, count={len(self.expected_state)}")
                    else:
                        print(f"valid unrelated state <- {topic}")

            self.changed.set()

    def publish_services_request(self, method: str, data: Dict[str, Any], timeout: float) -> Tuple[int, Optional[Dict[str, Any]]]:
        return self.request(method, data, timeout, channel="services", allowed_results={0})

    def publish_property_set(self, property_name: str, value: Any, timeout: float) -> Tuple[int, Optional[Dict[str, Any]]]:
        print(f"setting {property_name}={value}")
        rc, reply = self.request("property_set", {property_name: value}, timeout, channel="property")
        if rc != 0:
            return rc, reply
        result = property_result(reply, property_name)
        if result != 0:
            print(
                f"property_set {property_name} unexpected state.result: expected=0, actual={result}",
                file=sys.stderr,
            )
            return 4, reply
        return 0, reply

    def counts(self) -> Tuple[int, int, int]:
        with self.lock:
            return len(self.valid_dock_osd), len(self.valid_drone_osd), len(self.expected_state)

    def has_counts(self, dock_count: int, drone_count: int, state_count: int) -> bool:
        dock_actual, drone_actual, state_actual = self.counts()
        return dock_actual >= dock_count and drone_actual >= drone_count and state_actual >= state_count

    def wait_for_counts(self, dock_count: int, drone_count: int, state_count: int, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.has_counts(dock_count, drone_count, state_count):
                return True
            self.changed.wait(max(0.05, min(0.5, deadline - time.time())))
            self.changed.clear()
        return self.has_counts(dock_count, drone_count, state_count)

    def print_summary(self) -> None:
        dock_count, drone_count, state_count = self.counts()
        print(
            f"dock_osd={dock_count}, drone_osd={drone_count}, state={state_count}, "
            f"invalid={len(self.invalid_samples)}, ignored_legacy={self.stats.get('ignored_observed_legacy', 0)}, "
            f"drone_placeholders={len(self.placeholder_drone_samples)}"
        )
        if self.valid_dock_osd:
            data = self.valid_dock_osd[-1].get("data", {})
            summary = {
                "mode_code": unwrap_value(data.get("mode_code")),
                "cover_state": unwrap_value(data.get("cover_state")),
                "putter_state": unwrap_value(data.get("putter_state")),
                "drone_in_dock": unwrap_value(data.get("drone_in_dock")),
                "drc_state": unwrap_value(data.get("drc_state")),
            }
            print("dock_osd_summary:")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        if self.valid_drone_osd:
            data = self.valid_drone_osd[-1].get("data", {})
            summary = {
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "height": data.get("height"),
                "battery": data.get("battery"),
                "position_state": data.get("position_state"),
            }
            print("drone_osd_summary:")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        if self.expected_state:
            print("state_summary:")
            print(json.dumps(self.expected_state[-1].get("data", {}), ensure_ascii=False, indent=2))


def trigger_state(client: Dock3OsdStateClient, args) -> int:
    if args.skip_state:
        return 0
    if args.no_trigger_state:
        return 0

    before_state = client.counts()[2]
    for value in (0, 1):
        rc, _reply = client.publish_property_set(args.state_property, value, args.reply_timeout)
        if rc != 0:
            return rc
        if client.wait_for_counts(args.dock_samples, 0 if args.skip_drone else args.drone_samples, before_state + 1, 2.0):
            return 0
    return 0


def seed_topology(client: Dock3OsdStateClient, args) -> int:
    if args.skip_topo or args.skip_drone:
        return 0
    return client.publish_services_request("update_topo", build_update_topo_payload(args), args.reply_timeout)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Dock3 OSD/state MQTT reports from a running gateway")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--drone-sn", default=DEFAULT_DRONE_SN)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--reply-timeout", type=float, default=8.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--dock-samples", type=int, default=1)
    parser.add_argument("--drone-samples", type=int, default=1)
    parser.add_argument("--skip-topo", action="store_true", help="Do not publish update_topo before waiting for drone OSD.")
    parser.add_argument("--skip-drone", action="store_true")
    parser.add_argument(
        "--require-live-drone",
        action="store_true",
        help="Require non-zero coordinates, battery voltage and fixed GPS in aircraft OSD.",
    )
    parser.add_argument("--skip-state", action="store_true")
    parser.add_argument("--no-trigger-state", action="store_true", help="Only listen for state messages; do not publish property_set.")
    parser.add_argument("--state-property", default="silent_mode")
    parser.add_argument("--allow-invalid", action="store_true", help="Do not fail when invalid samples are observed before valid samples.")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true", help="Fail if legacy/irrelevant OSD or state samples are observed.")
    parser.add_argument("--gateway-domain", default=GATEWAY_DOMAIN)
    parser.add_argument("--gateway-type", type=int, default=GATEWAY_TYPE)
    parser.add_argument("--gateway-sub-type", type=int, default=GATEWAY_SUB_TYPE)
    parser.add_argument("--gateway-secret", default="gw-secret")
    parser.add_argument("--gateway-nonce", default="gw-nonce")
    parser.add_argument("--thing-version", default=THING_VERSION)
    parser.add_argument("--drone-domain", default="0")
    parser.add_argument("--drone-type", type=int, default=91)
    parser.add_argument("--drone-sub-type", type=int, default=0)
    parser.add_argument("--drone-index", default="A")
    parser.add_argument("--drone-secret", default="drone-secret")
    parser.add_argument("--drone-nonce", default="drone-nonce")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    expected_state_property = "" if args.skip_state else args.state_property
    client = Dock3OsdStateClient(
        args.host,
        args.port,
        args.sn,
        args.drone_sn,
        args.qos,
        expected_state_property,
        quiet_stale=args.quiet_stale,
        require_live_drone=args.require_live_drone,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = seed_topology(client, args)
        if rc != 0:
            return rc

        rc = trigger_state(client, args)
        if rc != 0:
            return rc

        expected_drone = 0 if args.skip_drone else args.drone_samples
        expected_state = 0 if args.skip_state else 1
        if not client.wait_for_counts(args.dock_samples, expected_drone, expected_state, args.timeout):
            dock_count, drone_count, state_count = client.counts()
            print(
                "timeout waiting for osd/state: "
                f"dock_osd={dock_count}/{args.dock_samples}, "
                f"drone_osd={drone_count}/{expected_drone}, "
                f"state={state_count}/{expected_state}",
                file=sys.stderr,
            )
            for sample in client.invalid_samples[:5]:
                print(f"invalid sample: {sample}", file=sys.stderr)
            if client.placeholder_drone_samples:
                print(
                    "latest drone placeholder="
                    + json.dumps(client.placeholder_drone_samples[-1], ensure_ascii=False)[:1000],
                    file=sys.stderr,
                )
            return 4

        if client.invalid_samples and not args.allow_invalid:
            for sample in client.invalid_samples[:5]:
                print(f"invalid sample: {sample}", file=sys.stderr)
            print("invalid osd/state samples observed", file=sys.stderr)
            return 5

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(
                "stale osd/state samples observed "
                f"(ignored_legacy={client.stats.get('ignored_observed_legacy', 0)}, "
                f"ignored_reply_stale={stale_total - int(client.stats.get('ignored_observed_legacy', 0))})",
                file=sys.stderr,
            )
            if client.ignored_legacy_samples:
                print(
                    "stale_samples=" + json.dumps(client.ignored_legacy_samples[:5], ensure_ascii=False),
                    file=sys.stderr,
                )
            return 6

        client.print_summary()
        print("dock3 osd/state test ok")
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
