"""
Device-side custom flight area applier.

Dock3 custom-fly-region files arrive through the Cloud API as opaque files.
This project does not contain DJI's device-side FlySafe SDK, so the real
device path available here is the flight-controller MAVLink link.  The applier
parses common polygon region files and uploads them as MAVLink fence mission
items, waiting for the flight controller ACK before reporting success upstream.
"""
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from pymavlink import mavutil
from pymavlink.mavutil import mavlink

from src.modules.base_module import BaseModule
from src.utils.config import Config


MAV_MISSION_TYPE_FENCE = getattr(mavlink, "MAV_MISSION_TYPE_FENCE", 1)
MAV_MISSION_ACCEPTED = getattr(mavlink, "MAV_MISSION_ACCEPTED", 0)
MAV_FRAME_GLOBAL = getattr(mavlink, "MAV_FRAME_GLOBAL", 0)
MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION = getattr(
    mavlink,
    "MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION",
    5001,
)
MAV_CMD_NAV_FENCE_POLYGON_VERTEX_EXCLUSION = getattr(
    mavlink,
    "MAV_CMD_NAV_FENCE_POLYGON_VERTEX_EXCLUSION",
    5002,
)


class FlightAreaDeviceApplier(BaseModule):
    """Apply downloaded custom flight area files to the real MAVLink fence store."""

    def __init__(self, target_system: int = 1, target_component: int = 1):
        super().__init__("FlightAreaDeviceApplier")
        self.target_system = target_system
        self.target_component = target_component
        self.dispatcher = None
        self._condition = threading.Condition()
        self._upload_lock = threading.Lock()
        self._upload_active = False
        self._pending_items: List[Any] = []
        self._requested_sequences = set()
        self._ack_type: Optional[int] = None

    def set_dispatcher(self, dispatcher):
        self.dispatcher = dispatcher
        if dispatcher and getattr(dispatcher, "connection", None):
            self.target_system = getattr(dispatcher.connection, "target_system", self.target_system)
            self.target_component = getattr(dispatcher.connection, "target_component", self.target_component)

    def on_message(self, message):
        if not self._upload_active:
            return
        msg_type = getattr(message, "msg_type", "")
        msg = getattr(message, "msg", None)
        if msg_type in ("MISSION_REQUEST", "MISSION_REQUEST_INT"):
            self._handle_mission_request(msg)
        elif msg_type == "MISSION_ACK":
            self._handle_mission_ack(msg)

    def apply_files(self, files: List[dict]) -> Dict[str, Any]:
        """Parse downloaded files and upload the resulting fence to the flight controller."""
        if not Config.get("device.safety.geofence_enabled", True):
            return {
                "success": False,
                "reason": "device.safety.geofence_enabled=false",
                "applied": False,
            }
        if not self.dispatcher or not getattr(self.dispatcher, "connection", None):
            return {
                "success": False,
                "reason": "mavlink_fence_dispatcher_not_available",
                "applied": False,
            }
        if not self.dispatcher.connection.is_connected():
            return {
                "success": False,
                "reason": "mavlink_not_connected",
                "applied": False,
            }

        try:
            items = self._build_fence_items(files)
        except Exception as exc:
            return {
                "success": False,
                "reason": f"flight_area_parse_failed: {exc}",
                "applied": False,
            }

        timeout = float(Config.get("flight_area.apply_timeout_seconds", 15.0) or 15.0)
        return self._upload_fence_items(items, timeout=timeout)

    def _build_fence_items(self, files: List[dict]) -> List[Any]:
        polygons = []
        for file_info in files:
            local_path = file_info.get("local_path")
            if not local_path or not os.path.exists(local_path):
                raise ValueError(f"missing local flight area file: {file_info.get('name', '')}")
            with open(local_path, "r", encoding="utf-8") as f:
                document = json.load(f)
            polygons.extend(self._extract_polygons(document, file_info.get("name", local_path)))

        items = []
        for polygon in polygons:
            command = (
                MAV_CMD_NAV_FENCE_POLYGON_VERTEX_EXCLUSION
                if polygon["mode"] == "exclusion"
                else MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION
            )
            points = polygon["points"]
            vertex_count = len(points)
            for point in points:
                items.append(self._make_fence_vertex_item(
                    seq=len(items),
                    command=command,
                    vertex_count=vertex_count,
                    latitude=point["latitude"],
                    longitude=point["longitude"],
                ))
        return items

    def _extract_polygons(self, document: Any, source: str) -> List[dict]:
        if isinstance(document, dict) and document.get("type") == "FeatureCollection":
            return self._extract_geojson_features(document.get("features", []), source)
        if isinstance(document, dict) and document.get("type") == "Feature":
            return self._extract_geojson_features([document], source)

        candidates = []
        if isinstance(document, dict):
            for key in ("areas", "flight_areas", "flightAreas", "regions", "fences"):
                value = document.get(key)
                if isinstance(value, list):
                    candidates.extend(value)
            if not candidates and isinstance(document.get("points"), list):
                candidates.append(document)
        elif isinstance(document, list):
            candidates = document

        polygons = []
        for index, item in enumerate(candidates):
            points = self._points_from_area(item)
            if points:
                polygons.append({
                    "mode": self._area_mode(item),
                    "points": self._normalize_ring(points, f"{source}[{index}]"),
                })
        if not polygons:
            raise ValueError(f"no supported polygon areas in {source}")
        return polygons

    def _extract_geojson_features(self, features: List[dict], source: str) -> List[dict]:
        polygons = []
        for index, feature in enumerate(features or []):
            if not isinstance(feature, dict):
                continue
            geometry = feature.get("geometry") or {}
            properties = feature.get("properties") or {}
            mode = self._area_mode(properties)
            if geometry.get("type") == "Polygon":
                rings = geometry.get("coordinates") or []
                if rings:
                    polygons.append({
                        "mode": mode,
                        "points": self._normalize_ring(
                            self._points_from_geojson_ring(rings[0]),
                            f"{source}.features[{index}]",
                        ),
                    })
            elif geometry.get("type") == "MultiPolygon":
                for ring_index, polygon in enumerate(geometry.get("coordinates") or []):
                    if polygon:
                        polygons.append({
                            "mode": mode,
                            "points": self._normalize_ring(
                                self._points_from_geojson_ring(polygon[0]),
                                f"{source}.features[{index}].rings[{ring_index}]",
                            ),
                        })
        if not polygons:
            raise ValueError(f"no supported GeoJSON polygons in {source}")
        return polygons

    @staticmethod
    def _points_from_geojson_ring(ring: List[Any]) -> List[dict]:
        points = []
        for coord in ring or []:
            if not isinstance(coord, (list, tuple)) or len(coord) < 2:
                continue
            points.append({"longitude": float(coord[0]), "latitude": float(coord[1])})
        return points

    @staticmethod
    def _points_from_area(area: Any) -> List[dict]:
        if not isinstance(area, dict):
            return []
        raw_points = (
            area.get("points")
            or area.get("coordinates")
            or area.get("polygon")
            or area.get("vertices")
            or []
        )
        points = []
        for point in raw_points:
            if isinstance(point, dict):
                lat = point.get("latitude", point.get("lat"))
                lon = point.get("longitude", point.get("lng", point.get("lon")))
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                lon, lat = point[0], point[1]
            else:
                continue
            points.append({"latitude": float(lat), "longitude": float(lon)})
        return points

    @staticmethod
    def _area_mode(area: Any) -> str:
        if not isinstance(area, dict):
            return "inclusion"
        raw = str(
            area.get("mode")
            or area.get("type")
            or area.get("area_type")
            or area.get("areaType")
            or area.get("fence_type")
            or area.get("fenceType")
            or ""
        ).lower()
        if any(token in raw for token in ("exclude", "exclusion", "no_fly", "nofly", "禁飞")):
            return "exclusion"
        return "inclusion"

    @staticmethod
    def _normalize_ring(points: List[dict], source: str) -> List[dict]:
        cleaned = []
        for point in points:
            lat = float(point["latitude"])
            lon = float(point["longitude"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"invalid coordinate in {source}: {lat},{lon}")
            cleaned.append({"latitude": lat, "longitude": lon})

        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1]:
            cleaned = cleaned[:-1]
        if len(cleaned) < 3:
            raise ValueError(f"polygon needs at least 3 vertices in {source}")
        if len(cleaned) > 70:
            raise ValueError(f"polygon has too many vertices in {source}: {len(cleaned)}")
        return cleaned

    def _make_fence_vertex_item(self, seq: int, command: int, vertex_count: int,
                                latitude: float, longitude: float):
        return self._mavlink_message(
            mavutil.mavlink.MAVLink_mission_item_int_message,
            self.target_system,
            self.target_component,
            seq,
            MAV_FRAME_GLOBAL,
            command,
            0,
            0,
            float(vertex_count),
            0.0,
            0.0,
            0.0,
            int(round(latitude * 1e7)),
            int(round(longitude * 1e7)),
            0.0,
            MAV_MISSION_TYPE_FENCE,
        )

    @staticmethod
    def _mavlink_message(message_cls, *args):
        try:
            return message_cls(*args)
        except TypeError:
            return message_cls(*args[:-1])

    def _upload_fence_items(self, items: List[Any], timeout: float) -> Dict[str, Any]:
        with self._upload_lock:
            with self._condition:
                self._pending_items = items
                self._requested_sequences = set()
                self._ack_type = None
                self._upload_active = True

            count_msg = self._mavlink_message(
                mavutil.mavlink.MAVLink_mission_count_message,
                self.target_system,
                self.target_component,
                len(items),
                MAV_MISSION_TYPE_FENCE,
            )
            if not self.dispatcher.connection.send_message(count_msg):
                with self._condition:
                    self._upload_active = False
                return {
                    "success": False,
                    "reason": "failed_to_send_fence_count",
                    "applied": False,
                    "fence_item_count": len(items),
                }

            deadline = time.monotonic() + timeout
            with self._condition:
                while self._ack_type is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._upload_active = False
                        return {
                            "success": False,
                            "reason": "fence_upload_timeout",
                            "applied": False,
                            "fence_item_count": len(items),
                            "requested_sequences": sorted(self._requested_sequences),
                        }
                    self._condition.wait(timeout=remaining)

                ack_type = self._ack_type
                self._upload_active = False

            return {
                "success": ack_type == MAV_MISSION_ACCEPTED,
                "reason": "accepted" if ack_type == MAV_MISSION_ACCEPTED else f"mission_ack_{ack_type}",
                "applied": ack_type == MAV_MISSION_ACCEPTED,
                "fence_item_count": len(items),
                "requested_sequences": sorted(self._requested_sequences),
            }

    def _handle_mission_request(self, msg):
        if msg is None:
            return
        seq = int(getattr(msg, "seq", -1))
        with self._condition:
            if seq < 0 or seq >= len(self._pending_items):
                return
            item = self._pending_items[seq]
            self._requested_sequences.add(seq)
        self.dispatcher.connection.send_message(item)

    def _handle_mission_ack(self, msg):
        if msg is None:
            return
        mission_type = getattr(msg, "mission_type", MAV_MISSION_TYPE_FENCE)
        if mission_type not in (MAV_MISSION_TYPE_FENCE, 0):
            return
        with self._condition:
            self._ack_type = int(getattr(msg, "type", -1))
            self._condition.notify_all()
