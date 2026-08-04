"""Dock3 services handlers for one-key takeoff and point flight."""
import time
from typing import Callable, Optional

from src.cloud_api.dock3_point_flight import (
    normalize_fly_to_point_update,
    normalize_takeoff_to_point,
)
from src.utils.config import Config
from src.utils.dji_error_codes import (
    DOCK_COMMUNICATION_ERROR,
    INVALID_COMMAND_FORMAT,
)
from src.utils.logger import get_logger
from src.utils.message import MessageBuilder


logger = get_logger("point_flight")


class PointFlightController:
    """Services-topic controller for Dock3 takeoff_to_point/fly_to_point."""

    def __init__(self, flight_runtime):
        self.gateway_sn = Config.get_gateway_sn()
        self._runtime = flight_runtime
        self._event_callback: Optional[Callable] = None
        self._last_flyto_cmd = None
        self._active_fly_to_point = None
        self._active_takeoff_to_point = None

    def set_event_callback(self, callback: Callable):
        self._event_callback = callback

    def get_status(self) -> dict:
        return {
            "last_flyto_cmd": self._last_flyto_cmd,
            "active_fly_to_point": self._active_fly_to_point,
            "active_takeoff_to_point": self._active_takeoff_to_point,
        }

    def clear_active(self):
        self._active_fly_to_point = None
        self._active_takeoff_to_point = None

    def _real_adapter_unavailable(self, msg: dict, reason: str) -> dict:
        return self._runtime._real_adapter_unavailable(msg, reason)

    def _check_target_boundary(self, latitude: float, longitude: float, height: float) -> tuple:
        return self._runtime._check_target_boundary(latitude, longitude, height)

    def _ensure_authority(self, msg: dict) -> tuple[bool, str]:
        return self._runtime._ensure_drc_authority_for_point_flight(msg)

    def _has_guided_adapter(self) -> bool:
        mode_control = getattr(self._runtime, "_mode_control", None)
        return bool(mode_control) and (
            hasattr(mode_control, "guided_loiter_at_point") or hasattr(mode_control, "guided_goto")
        )

    def _send_guided_loiter_target(self, latitude: float, longitude: float, height: float) -> dict:
        return self._runtime._send_guided_loiter_target(latitude, longitude, height)

    def _active_point_flight_target(self) -> tuple:
        if self._active_fly_to_point:
            points = self._active_fly_to_point.get("points") or []
            if points:
                return "fly_to_point", self._active_fly_to_point, points[0]

        if self._active_takeoff_to_point:
            target = self._active_takeoff_to_point.get("target") or {}
            if target:
                return "takeoff_to_point", self._active_takeoff_to_point, {
                    "latitude": target.get("target_latitude"),
                    "longitude": target.get("target_longitude"),
                    "height": target.get("target_height"),
                }

        return None, None, None

    def point_flight_emergency_hold(self) -> tuple:
        flight_kind, active_flight, target_point = self._active_point_flight_target()
        if not active_flight or not target_point:
            return False, False, "", {}

        telemetry, telemetry_error = self._runtime._get_current_point_flight_telemetry()
        if telemetry is None:
            return False, False, telemetry_error, {}

        try:
            target_lat = float(target_point.get("latitude"))
            target_lon = float(target_point.get("longitude"))
        except (TypeError, ValueError):
            return False, False, "active point flight target invalid", {}

        current_lat = float(telemetry["latitude"])
        current_lon = float(telemetry["longitude"])
        distance = self._runtime._distance_meters(current_lat, current_lon, target_lat, target_lon)
        arrival_radius = float(Config.get('device.drc.point_flight_arrival_radius', 5.0) or 5.0)
        if distance <= arrival_radius:
            return False, False, f"point flight target reached: distance={distance:.1f}m", {}

        loiter_height = (
            telemetry.get("altitudeRelative")
            or telemetry.get("height")
            or target_point.get("height")
            or telemetry.get("altitude")
            or 0
        )
        try:
            loiter_height = float(loiter_height)
        except (TypeError, ValueError):
            loiter_height = float(target_point.get("height") or 0)

        result = self._send_guided_loiter_target(current_lat, current_lon, loiter_height)
        ok = bool(result.get("success"))
        message = result.get("message", "current position loiter" if ok else "current position loiter failed")
        output = {
            "status": "ok" if ok else "failed",
            "action": "hold_current_position",
            "flight_kind": flight_kind,
            "distance_to_target": distance,
            "arrival_radius": arrival_radius,
            "loiter_point": {
                "latitude": current_lat,
                "longitude": current_lon,
                "height": loiter_height,
            },
            "message": message,
        }
        return True, ok, message, output

    def report_flyto_progress(self, fly_to_id: str, status: str, result: int = 0,
                              way_point_index: int = 0, remaining_distance: float = 0,
                              remaining_time: float = 0, planned_path_points: list = None):
        if self._event_callback:
            data = {
                'fly_to_id': fly_to_id,
                'status': status,
                'result': result,
                'way_point_index': way_point_index,
                'remaining_distance': remaining_distance,
                'remaining_time': remaining_time,
            }
            if planned_path_points:
                data['planned_path_points'] = planned_path_points
            self._event_callback(MessageBuilder.build_event('fly_to_point_progress', data, self.gateway_sn))

    def report_takeoff_to_point_progress(self, flight_id: str, status: str,
                                         result: int = 0, track_id: str = "",
                                         way_point_index: int = 0,
                                         remaining_distance: float = 0,
                                         remaining_time: float = 0,
                                         planned_path_points: list = None):
        if self._event_callback:
            data = {
                'flight_id': flight_id,
                'status': status,
                'result': result,
                'track_id': track_id,
                'way_point_index': way_point_index,
                'remaining_distance': remaining_distance,
                'remaining_time': remaining_time,
                'output': {
                    'status': status,
                    'progress': {
                        'way_point_index': way_point_index,
                        'remaining_distance': remaining_distance,
                        'remaining_time': remaining_time,
                    },
                },
            }
            if planned_path_points:
                data['planned_path_points'] = planned_path_points
                data['output']['ext'] = {'planned_path_points': planned_path_points}
            self._event_callback(MessageBuilder.build_event('takeoff_to_point_progress', data, self.gateway_sn))

    def handle_fly_to_point_stop(self, msg: dict) -> dict:
        logger.info("Fly to point stop")
        stopped = False
        fly_to_id = ""
        if self._active_fly_to_point:
            fly_to_id = self._active_fly_to_point.get("fly_to_id", "")
            self.report_flyto_progress(fly_to_id, "wayline_cancel", result=0)
            self._active_fly_to_point = None
            stopped = True
        if self._active_takeoff_to_point:
            flight_id = self._active_takeoff_to_point.get("flight_id", "")
            track_id = self._active_takeoff_to_point.get("track_id", "")
            self.report_takeoff_to_point_progress(flight_id, "wayline_cancel", result=0, track_id=track_id)
            self._active_takeoff_to_point = None
            stopped = True
            fly_to_id = fly_to_id or flight_id
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={"fly_to_id": fly_to_id, "output": {"status": "ok", "stopped": stopped}},
        )

    def handle_fly_to_point_update(self, msg: dict) -> dict:
        data = msg.get('data', {})
        default_fly_to_id = msg.get('tid') or str(int(time.time() * 1000))
        valid, target, errors = normalize_fly_to_point_update(data, default_fly_to_id=default_fly_to_id)
        if not valid:
            reason = "; ".join(errors)
            logger.warning("fly_to_point_update payload invalid: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        max_speed = target["max_speed"]
        points = target["points"]
        fly_to_id = target["fly_to_id"]
        first_point = points[0]
        valid, reason = self._check_target_boundary(
            first_point["latitude"], first_point["longitude"], first_point["height"]
        )
        if not valid:
            logger.warning("fly_to_point_update boundary rejected: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        authority_ok, authority_reason = self._ensure_authority(msg)
        if not authority_ok:
            logger.warning("fly_to_point_update rejected before authority: %s", authority_reason)
            return MessageBuilder.build_reply(
                msg,
                result=DOCK_COMMUNICATION_ERROR,
                data={"output": {"status": "failed", "reason": authority_reason}},
                msg_str=authority_reason,
            )

        self._active_fly_to_point = {
            "fly_to_id": fly_to_id,
            "max_speed": max_speed,
            "points": points,
            "timestamp": time.time(),
        }
        logger.info("Fly to point update: id=%s, max_speed=%s, points=%s", fly_to_id, max_speed, points)

        if not self._has_guided_adapter():
            self.report_flyto_progress(fly_to_id, "wayline_failed", result=DOCK_COMMUNICATION_ERROR)
            self._active_fly_to_point = None
            return self._real_adapter_unavailable(
                msg,
                "fly_to_point_update requires a real guided flight adapter",
            )

        try:
            result = self._send_guided_loiter_target(
                first_point["latitude"], first_point["longitude"], first_point["height"]
            )
        except Exception as exc:
            self.report_flyto_progress(fly_to_id, "wayline_failed", result=DOCK_COMMUNICATION_ERROR)
            self._active_fly_to_point = None
            return self._real_adapter_unavailable(msg, f"fly_to_point_update guided dispatch failed: {exc}")
        if not result.get("success"):
            warning = result.get("message", "guided_goto failed")
            logger.warning("fly_to_point_update guided_goto failed: %s", warning)
            self.report_flyto_progress(fly_to_id, "wayline_failed", result=DOCK_COMMUNICATION_ERROR)
            self._active_fly_to_point = None
            return self._real_adapter_unavailable(msg, warning)

        self.report_flyto_progress(
            fly_to_id,
            "wayline_progress",
            result=0,
            way_point_index=0,
            planned_path_points=points,
        )
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={
                "fly_to_id": fly_to_id,
                "max_speed": max_speed,
                "target_point": first_point,
                "output": {"status": "ok", "simulated": False, "warning": ""},
            },
        )

    def handle_fly_to_point(self, msg: dict) -> dict:
        return self.handle_fly_to_point_update(msg)

    def handle_takeoff_to_point(self, msg: dict) -> dict:
        data = msg.get('data', {})
        default_flight_id = msg.get('tid') or getattr(self._runtime, "_session_id", "") or str(int(time.time() * 1000))
        valid, target, errors = normalize_takeoff_to_point(data, default_flight_id=default_flight_id)
        if not valid:
            reason = "; ".join(errors)
            logger.warning("takeoff_to_point payload invalid: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        target_latitude = target["target_latitude"]
        target_longitude = target["target_longitude"]
        target_height = target["target_height"]
        flight_id = target["flight_id"]
        max_speed = target["max_speed"]
        valid, reason = self._check_target_boundary(target_latitude, target_longitude, target_height)
        if not valid:
            logger.warning("takeoff_to_point boundary rejected: %s", reason)
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        track_id = f"track-{flight_id}"
        self._last_flyto_cmd = {
            **target,
            "track_id": track_id,
            "timestamp": time.time(),
            "source": "takeoff_to_point",
        }
        self._active_takeoff_to_point = {
            "flight_id": flight_id,
            "track_id": track_id,
            "target": target,
            "timestamp": time.time(),
        }

        logger.info(
            "Takeoff to point: lat=%s, lon=%s, height=%s, flight_id=%s, max_speed=%s",
            target_latitude,
            target_longitude,
            target_height,
            flight_id,
            max_speed,
        )
        self.report_takeoff_to_point_progress(
            flight_id,
            "task_ready",
            result=0,
            track_id=track_id,
            planned_path_points=[{
                "latitude": target_latitude,
                "longitude": target_longitude,
                "height": target_height,
            }],
        )

        if not self._has_guided_adapter():
            self.report_takeoff_to_point_progress(
                flight_id,
                "wayline_failed",
                result=DOCK_COMMUNICATION_ERROR,
                track_id=track_id,
            )
            self._active_takeoff_to_point = None
            return self._real_adapter_unavailable(
                msg,
                "takeoff_to_point requires a real guided flight adapter",
            )

        guidance_message = "guided target sent"
        try:
            goto_result = self._send_guided_loiter_target(target_latitude, target_longitude, target_height)
            if not goto_result.get("success"):
                guidance_warning = goto_result.get("message", "guided_goto failed")
                logger.warning("takeoff_to_point guided_goto failed: %s", guidance_warning)
                self.report_takeoff_to_point_progress(
                    flight_id,
                    "wayline_failed",
                    result=DOCK_COMMUNICATION_ERROR,
                    track_id=track_id,
                )
                self._active_takeoff_to_point = None
                return self._real_adapter_unavailable(msg, guidance_warning)
            guidance_message = goto_result.get("message", guidance_message)
        except Exception as exc:
            guidance_warning = str(exc)
            logger.warning("takeoff_to_point guidance dispatch failed: %s", guidance_warning)
            self.report_takeoff_to_point_progress(
                flight_id,
                "wayline_failed",
                result=DOCK_COMMUNICATION_ERROR,
                track_id=track_id,
            )
            self._active_takeoff_to_point = None
            return self._real_adapter_unavailable(msg, guidance_warning)

        self.report_takeoff_to_point_progress(
            flight_id,
            "wayline_progress",
            result=0,
            track_id=track_id,
            remaining_distance=0,
            remaining_time=0,
            planned_path_points=[{
                "latitude": target_latitude,
                "longitude": target_longitude,
                "height": target_height,
            }],
        )
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={
                "flight_id": flight_id,
                "track_id": track_id,
                "target_point": {
                    "latitude": target_latitude,
                    "longitude": target_longitude,
                    "height": target_height,
                },
                "max_speed": max_speed,
                "output": {
                    "status": "ok",
                    "message": guidance_message,
                    "simulated": False,
                    "warning": "",
                },
            },
        )
