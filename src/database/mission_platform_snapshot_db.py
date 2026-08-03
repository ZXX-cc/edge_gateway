import time
from typing import Any, Optional

from sqlalchemy import delete

from .connection import is_mongo_session
from .models import MissionPlatformSnapshot


class MissionPlatformSnapshotDB:
    """断链期间只保留一条最新航线平台状态快照。"""

    COLLECTION = "mission_platform_snapshot"

    @staticmethod
    def _extract_flight_id(payload: dict) -> str:
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return ""
        output = data.get("output", {})
        ext = output.get("ext", {}) if isinstance(output, dict) else {}
        file_info = data.get("file", {})
        file_ext = file_info.get("ext", {}) if isinstance(file_info, dict) else {}
        for candidate in (
            data.get("flight_id"),
            data.get("flighttask_id"),
            ext.get("flight_id") if isinstance(ext, dict) else "",
            file_ext.get("flight_id") if isinstance(file_ext, dict) else "",
        ):
            if candidate:
                return str(candidate)
        return ""

    @classmethod
    def save_latest(cls, session: Any, gateway_sn: str, payload: dict, source: str = "") -> None:
        now_ms = int(time.time() * 1000)
        method = str(payload.get("method", "")) if isinstance(payload, dict) else ""
        values = {
            "gateway_sn": gateway_sn,
            "method": method,
            "flight_id": cls._extract_flight_id(payload),
            "source": source,
            "payload": payload,
            "updated_at": now_ms,
        }

        if is_mongo_session(session):
            def upsert_latest(database):
                database[cls.COLLECTION].update_one(
                    {"gateway_sn": gateway_sn},
                    {
                        "$set": values,
                        "$setOnInsert": {"created_at": now_ms},
                    },
                    upsert=True,
                )

            session.run(upsert_latest)
            return

        row = session.get(MissionPlatformSnapshot, gateway_sn)
        if row is None:
            row = MissionPlatformSnapshot(created_at=now_ms, **values)
            session.add(row)
            return
        for key, value in values.items():
            setattr(row, key, value)

    @classmethod
    def get_latest(cls, session: Any, gateway_sn: str) -> Optional[dict]:
        if is_mongo_session(session):
            document = session.run(
                lambda database: database[cls.COLLECTION].find_one({"gateway_sn": gateway_sn})
            )
            if not document:
                return None
            return {
                "gateway_sn": document.get("gateway_sn", ""),
                "method": document.get("method", ""),
                "flight_id": document.get("flight_id", ""),
                "source": document.get("source", ""),
                "payload": document.get("payload") or {},
                "created_at": document.get("created_at", 0),
                "updated_at": document.get("updated_at", 0),
            }

        row = session.get(MissionPlatformSnapshot, gateway_sn)
        if row is None:
            return None
        return {
            "gateway_sn": row.gateway_sn,
            "method": row.method,
            "flight_id": row.flight_id or "",
            "source": row.source or "",
            "payload": row.payload or {},
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @classmethod
    def clear_latest(cls, session: Any, gateway_sn: str) -> bool:
        if is_mongo_session(session):
            result = session.run(
                lambda database: database[cls.COLLECTION].delete_one({"gateway_sn": gateway_sn})
            )
            return result.deleted_count > 0

        result = session.execute(
            delete(MissionPlatformSnapshot).where(
                MissionPlatformSnapshot.gateway_sn == gateway_sn
            )
        )
        return result.rowcount > 0
