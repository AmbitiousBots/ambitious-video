from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from ambitious_kit.domain.envelope import MAX_SEED

NOW_FLAG = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?(Z|[+-]\d{2}:\d{2})$", re.ASCII
)
SEED_FLAG = re.compile(r"^\d{1,10}$", re.ASCII)


def _offset_minutes(zone: str) -> int | None:
    if zone == "Z":
        return 0
    hours, minutes = int(zone[1:3]), int(zone[4:6])
    if hours > 23 or minutes > 59:
        return None
    return (-1 if zone.startswith("-") else 1) * (hours * 60 + minutes)


def parse_now(text: str) -> datetime | None:
    match = NOW_FLAG.fullmatch(text)
    if match is None:
        return None
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    offset = _offset_minutes(zone)
    if offset is None:
        return None
    try:
        local = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second or "0"),
            int((fraction or "0").ljust(3, "0")) * 1000,
            tzinfo=UTC,
        )
        return local - timedelta(minutes=offset)
    except (ValueError, OverflowError):
        return None


def format_instant(instant: datetime) -> str:
    utc = instant.astimezone(UTC)
    return (
        f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}T{utc.hour:02d}:{utc.minute:02d}:{utc.second:02d}"
        f".{utc.microsecond // 1000:03d}Z"
    )


def parse_seed(text: str) -> int | None:
    if SEED_FLAG.fullmatch(text) is None:
        return None
    seed = int(text)
    return seed if seed <= MAX_SEED else None
