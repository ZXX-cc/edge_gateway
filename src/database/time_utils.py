"""Timestamp helpers for database retention and sync windows."""
from __future__ import annotations

import time
from typing import Optional, Tuple

from sqlalchemy import and_, or_


TIMESTAMP_MS_THRESHOLD = 10_000_000_000


def now_ms() -> int:
    return int(time.time() * 1000)


def now_seconds() -> int:
    return int(time.time())


def is_millisecond_timestamp(value: Optional[int]) -> bool:
    if value is None:
        return False
    try:
        return int(value) >= TIMESTAMP_MS_THRESHOLD
    except (TypeError, ValueError):
        return False


def retention_cutoffs(age_seconds: int, now_value: Optional[float] = None) -> Tuple[int, int]:
    """Return ``(millisecond_cutoff, second_cutoff)`` for an age window."""
    now_value = time.time() if now_value is None else now_value
    age_seconds = int(age_seconds)
    current_ms = int(now_value * 1000)
    current_seconds = int(now_value)
    return current_ms - age_seconds * 1000, current_seconds - age_seconds


def is_expired_timestamp_value(value: Optional[int], age_seconds: int,
                               now_value: Optional[float] = None) -> bool:
    """Evaluate expiration for mixed second/ms timestamp columns."""
    if value is None:
        return False
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return False

    cutoff_ms, cutoff_seconds = retention_cutoffs(age_seconds, now_value)
    if is_millisecond_timestamp(timestamp):
        return timestamp < cutoff_ms
    return timestamp < cutoff_seconds


def expired_timestamp_condition(column, age_seconds: int):
    """Build a SQLAlchemy condition for mixed second/ms timestamp columns."""
    cutoff_ms, cutoff_seconds = retention_cutoffs(age_seconds)
    return or_(
        and_(column >= TIMESTAMP_MS_THRESHOLD, column < cutoff_ms),
        and_(column < TIMESTAMP_MS_THRESHOLD, column < cutoff_seconds),
    )
