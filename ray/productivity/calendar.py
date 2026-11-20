"""Calendar events with month/week/day views."""

from __future__ import annotations

import calendar as calendar_module
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from ..core.database import Database, utcnow
from ..core.events import EventBus, bus as default_bus
from .common import parse_dt, to_iso


class CalendarService:
    def __init__(self, db: Database, bus: EventBus | None = None) -> None:
        self.db = db
        self.bus = bus or default_bus

    def create(
        self,
        title: str,
        start_at: str | datetime,
        end_at: str | datetime | None = None,
        description: str = "",
        location: str = "",
        all_day: bool = False,
        color: str = "cyan",
    ) -> dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise ValueError("an event needs a title")
        start = parse_dt(start_at) if not isinstance(start_at, datetime) else start_at
        if start is None:
            raise ValueError("start time could not be understood")
        end = parse_dt(end_at) if end_at and not isinstance(end_at, datetime) else end_at
        if end and end < start:
            raise ValueError("the event ends before it starts")
        event_id = self.db.insert(
            "events",
            {
                "title": title[:300],
                "description": (description or "")[:3000],
                "start_at": to_iso(start),
                "end_at": to_iso(end) if end else None,
                "all_day": 1 if all_day else 0,
                "location": (location or "")[:200],
                "color": color or "cyan",
                "created_at": utcnow(),
            },
        )
        event = self.get(event_id)
        self.bus.publish("event_created", event=event)
        return event

    def get(self, event_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM events WHERE id = ?", (event_id,))
        if not row:
            raise KeyError(f"event {event_id} not found")
        return row

    def list_range(self, start: datetime | date, end: datetime | date) -> list[dict[str, Any]]:
        start_iso = to_iso(start) or ""
        end_iso = to_iso(end) or ""
        return self.db.query(
            "SELECT * FROM events WHERE start_at >= ? AND start_at <= ? ORDER BY start_at",
            (start_iso, end_iso),
        )

    def today(self) -> list[dict[str, Any]]:
        start = datetime.combine(date.today(), datetime.min.time())
        return self.list_range(start, start + timedelta(days=1))

    def upcoming(self, days: int = 7, limit: int = 20) -> list[dict[str, Any]]:
        now = datetime.now()
        return self.list_range(now, now + timedelta(days=days))[:limit]

    def update(self, event_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {"title", "description", "start_at", "end_at", "all_day", "location", "color"}
        values = {key: value for key, value in fields.items() if key in allowed}
        for key in ("start_at", "end_at"):
            if key in values:
                values[key] = to_iso(parse_dt(values[key]))
        if "all_day" in values:
            values["all_day"] = 1 if values["all_day"] else 0
        if values:
            self.db.update("events", event_id, values)
        return self.get(event_id)

    def delete(self, event_id: int) -> bool:
        if not self.db.query_one("SELECT id FROM events WHERE id = ?", (event_id,)):
            return False
        self.db.delete("events", event_id)
        return True

    # ------------------------------------------------------------------- views
    def month_view(self, year: int | None = None, month: int | None = None) -> dict[str, Any]:
        today = date.today()
        year = year or today.year
        month = month or today.month
        first = date(year, month, 1)
        last = date(year, month, calendar_module.monthrange(year, month)[1])
        events = self.list_range(first, datetime.combine(last, datetime.max.time()))
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            by_day[event["start_at"][:10]].append(event)
        weeks = calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month)
        grid = [
            [
                {
                    "date": day.isoformat(),
                    "in_month": day.month == month,
                    "is_today": day == today,
                    "events": by_day.get(day.isoformat(), []),
                }
                for day in week
            ]
            for week in weeks
        ]
        return {
            "year": year,
            "month": month,
            "label": first.strftime("%B %Y"),
            "weeks": grid,
            "event_count": len(events),
        }

    def week_view(self, day: date | None = None) -> dict[str, Any]:
        reference = day or date.today()
        start = reference - timedelta(days=reference.weekday())
        days = []
        for offset in range(7):
            current = start + timedelta(days=offset)
            events = self.list_range(
                datetime.combine(current, datetime.min.time()),
                datetime.combine(current, datetime.max.time()),
            )
            days.append({"date": current.isoformat(), "label": current.strftime("%a %d"), "events": events})
        return {"start": start.isoformat(), "days": days}

    def day_view(self, day: date | None = None) -> dict[str, Any]:
        reference = day or date.today()
        events = self.list_range(
            datetime.combine(reference, datetime.min.time()),
            datetime.combine(reference, datetime.max.time()),
        )
        timeline = []
        for hour in range(24):
            timeline.append(
                {
                    "hour": hour,
                    "label": f"{hour:02d}:00",
                    "events": [e for e in events if e["start_at"][11:13] == f"{hour:02d}"],
                }
            )
        return {"date": reference.isoformat(), "events": events, "timeline": timeline}

    def stats(self) -> dict[str, Any]:
        return {
            "total": self.db.scalar("SELECT COUNT(*) FROM events", default=0),
            "today": len(self.today()),
            "next": (self.upcoming(limit=1) or [None])[0],
        }
