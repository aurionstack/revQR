"""Convert UTC database timestamps into the configured display timezone."""

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings


@lru_cache(maxsize=8)
def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def app_timezone() -> ZoneInfo:
    return _timezone(settings.APP_TIMEZONE)


def as_local_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(app_timezone())


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(app_timezone())


def format_local_datetime(value: datetime | None, pattern: str = "%b %d, %Y %I:%M %p") -> str:
    local_value = as_local_datetime(value)
    return local_value.strftime(pattern) if local_value else ""
