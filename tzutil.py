"""Local-time helpers. Week = Monday 00:00 to next Monday 00:00 in BOT_TZ."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

TZ_NAME = os.getenv("BOT_TZ", "Asia/Kolkata")
try:
    TZ = ZoneInfo(TZ_NAME)
except Exception:
    TZ = ZoneInfo("Asia/Kolkata")
    TZ_NAME = "Asia/Kolkata"


def now_local() -> datetime:
    return datetime.now(TZ)


def today_local() -> date:
    return now_local().date()


def today_str() -> str:
    return today_local().isoformat()


def monday_00(ref: datetime | None = None) -> datetime:
    """Monday 00:00 local of the week containing ref (default now)."""
    ref = ref or now_local()
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=TZ)
    start = (ref - timedelta(days=ref.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start


def this_week_range() -> tuple[datetime, datetime]:
    start = monday_00()
    return start, start + timedelta(days=7)


def last_week_range() -> tuple[datetime, datetime]:
    start = monday_00() - timedelta(days=7)
    return start, start + timedelta(days=7)


def week_before_last_range() -> tuple[datetime, datetime]:
    start = monday_00() - timedelta(days=14)
    return start, start + timedelta(days=7)


def this_month_range() -> tuple[datetime, datetime]:
    start = now_local().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, now_local()


def this_year_range() -> tuple[datetime, datetime]:
    start = now_local().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, now_local()


def to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone_utc()).isoformat()


def timezone_utc():
    from datetime import timezone

    return timezone.utc


def countdown_to_monday(ref: datetime | None = None) -> str:
    """'3d 4h' until next Monday 00:00 local."""
    ref = ref or now_local()
    delta = monday_00(ref) + timedelta(days=7) - ref
    days = delta.days
    hours = delta.seconds // 3600
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        mins = (delta.seconds % 3600) // 60
        return f"{hours}h {mins}m"
    return f"{max(delta.seconds // 60, 1)}m"


def fmt_day(dt: datetime) -> str:
    return dt.strftime("%b %-d") if os.name != "nt" else dt.strftime("%b %#d")


def fmt_month(dt: datetime) -> str:
    return dt.strftime("%B %Y")
