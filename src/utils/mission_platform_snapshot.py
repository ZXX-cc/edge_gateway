from typing import Callable

from src.database.connection import SessionLocal
from src.database.mission_platform_snapshot_db import MissionPlatformSnapshotDB
from src.utils.logger import get_logger

logger = get_logger("mission_platform_snapshot")

MISSION_PLATFORM_SNAPSHOT_METHODS = frozenset({
    'flighttask_progress',
    'flighttask_ready',
    'task_ready_notify',
    'flight_task_progress_notify',
    'flight_setup_exception_notify',
    'return_home_info',
    'device_exit_homing_notify',
    'one_key_takeoff_progress',
    'fly_to_point_progress',
    'takeoff_to_point_progress',
    'take_off_progress',
    'file_upload_callback',
    'highest_priority_upload_flighttask_media',
})


def is_mission_platform_snapshot_event(event: dict) -> bool:
    return isinstance(event, dict) and event.get('method') in MISSION_PLATFORM_SNAPSHOT_METHODS


def save_latest_mission_platform_snapshot(
    event: dict,
    *,
    gateway_sn: str,
    source: str,
    session_factory: Callable = SessionLocal,
) -> None:
    if not is_mission_platform_snapshot_event(event):
        return
    with session_factory() as session:
        MissionPlatformSnapshotDB.save_latest(session, gateway_sn, event, source=source)
        session.commit()
    logger.info(
        "Latest mission platform snapshot saved: method=%s source=%s",
        event.get('method'),
        source,
    )


def publish_platform_event_with_latest_snapshot(
    mqtt,
    event: dict,
    *,
    gateway_sn: str,
    source: str = "",
    session_factory: Callable = SessionLocal,
) -> bool:
    ok = mqtt.publish_event(event)
    if not ok:
        try:
            save_latest_mission_platform_snapshot(
                event,
                gateway_sn=gateway_sn,
                source=source,
                session_factory=session_factory,
            )
        except Exception as exc:
            logger.warning(
                "Save latest mission platform snapshot failed: method=%s error=%s",
                event.get('method') if isinstance(event, dict) else "",
                exc,
                exc_info=True,
            )
    return ok


def publish_current_mission_snapshot(mqtt, mission) -> None:
    if not mission:
        return
    try:
        event = mission.build_current_wayline_progress_event()
    except Exception as exc:
        logger.warning("Build current mission snapshot failed: %s", exc, exc_info=True)
        return
    if not event:
        return
    ok = mqtt.publish_event(event)
    logger.info(
        "Current mission snapshot publish after reconnect: ok=%s method=%s",
        ok,
        event.get('method'),
    )


def flush_latest_mission_platform_snapshot(
    mqtt,
    mission,
    *,
    gateway_sn: str,
    session_factory: Callable = SessionLocal,
) -> None:
    if not mqtt or not getattr(mqtt, "connected", False):
        return

    try:
        with session_factory() as session:
            snapshot = MissionPlatformSnapshotDB.get_latest(session, gateway_sn)
    except Exception as exc:
        logger.warning("Read latest mission platform snapshot failed: %s", exc, exc_info=True)
        return

    if snapshot and snapshot.get('payload'):
        payload = snapshot['payload']
        ok = mqtt.publish_event(payload)
        logger.info(
            "Latest mission platform snapshot replay: ok=%s method=%s flight_id=%s updated_at=%s",
            ok,
            snapshot.get('method'),
            snapshot.get('flight_id'),
            snapshot.get('updated_at'),
        )
        if ok:
            try:
                with session_factory() as session:
                    MissionPlatformSnapshotDB.clear_latest(session, gateway_sn)
                    session.commit()
                logger.info("Latest mission platform snapshot cleared after replay")
            except Exception as exc:
                logger.warning(
                    "Clear latest mission platform snapshot failed: %s",
                    exc,
                    exc_info=True,
                )

    publish_current_mission_snapshot(mqtt, mission)
