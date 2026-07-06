from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from .models import TelemetryData

MAX_LOCAL_TELEMETRY = 3600


class TelemetryDB:
    @staticmethod
    async def save_telemetry_async(session: AsyncSession, device_sn: str, osd_data: dict):
        battery = osd_data.get('battery', {})
        position_state = osd_data.get('position_state', {})

        telemetry = TelemetryData(
            device_sn=device_sn,
            timestamp=osd_data.get('timestamp', int(osd_data.get('timestamp_ms', 0))),
            latitude=osd_data.get('latitude'),
            longitude=osd_data.get('longitude'),
            altitude=osd_data.get('altitude'),
            height=osd_data.get('height'),
            horizontal_speed=osd_data.get('horizontal_speed'),
            vertical_speed=osd_data.get('vertical_speed'),
            attitude_pitch=osd_data.get('attitude_pitch'),
            attitude_roll=osd_data.get('attitude_roll'),
            attitude_head=osd_data.get('attitude_head'),
            battery_capacity=battery.get('capacity_percent'),
            battery_voltage=battery.get('voltage'),
            battery_current=battery.get('current'),
            battery_temperature=battery.get('temperature'),
            gps_number=position_state.get('gps_number'),
            gps_fixed=position_state.get('is_fixed'),
            wind_speed=osd_data.get('wind_speed'),
            wind_direction=osd_data.get('wind_direction'),
            mode_code=osd_data.get('mode_code'),
            gear=osd_data.get('gear'),
            flight_time=osd_data.get('flight_time'),
        )
        session.add(telemetry)
        await session.flush()
        await TelemetryDB._trim_telemetry_buffer(session, device_sn)
        return telemetry

    @staticmethod
    def save_telemetry_sync(session: Session, device_sn: str, osd_data: dict):
        battery = osd_data.get('battery', {})
        position_state = osd_data.get('position_state', {})

        telemetry = TelemetryData(
            device_sn=device_sn,
            timestamp=osd_data.get('timestamp', int(osd_data.get('timestamp_ms', 0))),
            latitude=osd_data.get('latitude'),
            longitude=osd_data.get('longitude'),
            altitude=osd_data.get('altitude'),
            height=osd_data.get('height'),
            horizontal_speed=osd_data.get('horizontal_speed'),
            vertical_speed=osd_data.get('vertical_speed'),
            attitude_pitch=osd_data.get('attitude_pitch'),
            attitude_roll=osd_data.get('attitude_roll'),
            attitude_head=osd_data.get('attitude_head'),
            battery_capacity=battery.get('capacity_percent'),
            battery_voltage=battery.get('voltage'),
            battery_current=battery.get('current'),
            battery_temperature=battery.get('temperature'),
            gps_number=position_state.get('gps_number'),
            gps_fixed=position_state.get('is_fixed'),
            wind_speed=osd_data.get('wind_speed'),
            wind_direction=osd_data.get('wind_direction'),
            mode_code=osd_data.get('mode_code'),
            gear=osd_data.get('gear'),
            flight_time=osd_data.get('flight_time'),
        )
        session.add(telemetry)
        session.flush()
        TelemetryDB._trim_telemetry_buffer_sync(session, device_sn)
        return telemetry

    @staticmethod
    async def _trim_telemetry_buffer(session: AsyncSession, device_sn: str):
        result = await session.execute(
            select(TelemetryData.id)
            .where(TelemetryData.device_sn == device_sn)
            .order_by(TelemetryData.id.asc())
        )
        all_ids = [row[0] for row in result.all()]
        if len(all_ids) > MAX_LOCAL_TELEMETRY:
            ids_to_delete = all_ids[:len(all_ids) - MAX_LOCAL_TELEMETRY]
            await session.execute(
                delete(TelemetryData).where(TelemetryData.id.in_(ids_to_delete))
            )

    @staticmethod
    def _trim_telemetry_buffer_sync(session: Session, device_sn: str):
        result = session.execute(
            select(TelemetryData.id)
            .where(TelemetryData.device_sn == device_sn)
            .order_by(TelemetryData.id.asc())
        )
        all_ids = [row[0] for row in result.all()]
        if len(all_ids) > MAX_LOCAL_TELEMETRY:
            ids_to_delete = all_ids[:len(all_ids) - MAX_LOCAL_TELEMETRY]
            session.execute(
                delete(TelemetryData).where(TelemetryData.id.in_(ids_to_delete))
            )

    @staticmethod
    async def get_latest_telemetry_async(session: AsyncSession, device_sn: str) -> TelemetryData | None:
        result = await session.execute(
            select(TelemetryData)
            .where(TelemetryData.device_sn == device_sn)
            .order_by(TelemetryData.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def get_latest_telemetry_sync(session: Session, device_sn: str) -> TelemetryData | None:
        result = session.execute(
            select(TelemetryData)
            .where(TelemetryData.device_sn == device_sn)
            .order_by(TelemetryData.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_telemetry_history_async(session: AsyncSession, device_sn: str, limit: int = 100):
        result = await session.execute(
            select(TelemetryData)
            .where(TelemetryData.device_sn == device_sn)
            .order_by(TelemetryData.id.desc())
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def clear_old_data_async(session: AsyncSession, device_sn: str, hours_to_keep: int = 2):
        import time
        cutoff_time = int(time.time()) - (hours_to_keep * 3600)
        await session.execute(
            delete(TelemetryData)
            .where(TelemetryData.device_sn == device_sn)
            .where(TelemetryData.timestamp < cutoff_time)
        )