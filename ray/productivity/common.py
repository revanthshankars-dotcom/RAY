"""Shared date/recurrence helpers for the productivity modules."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Any

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    return datetime.now().replace(microsecond=0)


def to_iso(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    return datetime.combine(value, datetime.min.time()).isoformat()


def parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "")
    for fmt in (ISO_FORMAT, "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def human_when(value: str | datetime | None) -> str:
    moment = parse_dt(value) if not isinstance(value, datetime) else value
    if not moment:
        return "no date"
    delta = moment - local_now()
    seconds = delta.total_seconds()
    if abs(seconds) < 60:
        return "now"
    if 0 < seconds < 3600:
        return f"in {int(seconds // 60)} min"
    if 0 < seconds < 86400:
        return f"in {int(seconds // 3600)}h {int(seconds % 3600 // 60)}m"
    if seconds < 0:
        return moment.strftime("%d %b %H:%M") + " (overdue)"
    if moment.date() == local_now().date() + timedelta(days=1):
        return "tomorrow " + moment.strftime("%H:%M")
    return moment.strftime("%d %b %H:%M")


def next_occurrence(current: datetime, recurrence: str | None) -> datetime | None:
    """Compute the next due time for a simple recurrence rule.

    Supported: daily, weekdays, weekly, biweekly, monthly, yearly,
    ``every:Nd`` / ``every:Nh`` / ``every:Nm``.
    """
    if not recurrence:
        return None
    rule = recurrence.strip().lower()
    if rule == "daily":
        return current + timedelta(days=1)
    if rule == "weekdays":
        nxt = current + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt
    if rule == "weekly":
        return current + timedelta(weeks=1)
    if rule == "biweekly":
        return current + timedelta(weeks=2)
    if rule == "monthly":
        return add_months(current, 1)
    if rule == "yearly":
        return add_months(current, 12)
    if rule.startswith("every:"):
        spec = rule.split(":", 1)[1]
        unit = spec[-1]
        try:
            amount = int(spec[:-1])
        except ValueError:
            return None
        if unit == "d":
            return current + timedelta(days=amount)
        if unit == "h":
            return current + timedelta(hours=amount)
        if unit == "m":
            return current + timedelta(minutes=amount)
        if unit == "w":
            return current + timedelta(weeks=amount)
    return None


def add_months(moment: datetime, months: int) -> datetime:
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def human_delta(seconds: float) -> str:
    seconds = abs(int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600}h"


def summarise_rows(rows: list[dict[str, Any]], key: str = "title") -> str:
    if not rows:
        return "nothing"
    return ", ".join(str(row.get(key, "")) for row in rows[:5])
