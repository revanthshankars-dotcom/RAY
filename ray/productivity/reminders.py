"""Reminders with recurrence and a polling due-check.

Real reminders, not decorations: the automation tick calls :meth:`due` every
20 seconds and delivers a desktop notification plus a spoken line when a voice
provider is configured.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..core.database import Database, utcnow
from ..core.events import EventBus, bus as default_bus
from .common import next_occurrence, parse_dt, to_iso


class ReminderService:
    def __init__(self, db: Database, bus: EventBus | None = None) -> None:
        self.db = db
        self.bus = bus or default_bus

    def create(
        self,
        title: str,
        due_at: str | datetime,
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise ValueError("a reminder needs a title")
        moment = parse_dt(due_at) if not isinstance(due_at, datetime) else due_at
        if moment is None:
            raise ValueError("due time could not be understood; use e.g. 2026-11-20T18:00")
        reminder_id = self.db.insert(
            "reminders",
            {
                "title": title[:300],
                "due_at": to_iso(moment),
                "recurrence": recurrence or None,
                "enabled": 1,
                "created_at": utcnow(),
            },
        )
        reminder = self.get(reminder_id)
        self.bus.publish("reminder_created", reminder=reminder)
        return reminder

    def get(self, reminder_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        if not row:
            raise KeyError(f"reminder {reminder_id} not found")
        return row

    def list(self, include_disabled: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        where = "" if include_disabled else "WHERE enabled = 1"
        return self.db.query(
            f"SELECT * FROM reminders {where} ORDER BY due_at ASC LIMIT ?", (limit,)
        )

    def upcoming(self, hours: int = 24) -> list[dict[str, Any]]:
        cutoff = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
        return self.db.query(
            "SELECT * FROM reminders WHERE enabled = 1 AND due_at <= ? AND due_at >= ? ORDER BY due_at",
            (cutoff, datetime.now().isoformat(timespec="seconds")),
        )

    def update(self, reminder_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {"title", "due_at", "recurrence", "enabled"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if "due_at" in values:
            values["due_at"] = to_iso(parse_dt(values["due_at"]))
        if "enabled" in values:
            values["enabled"] = 1 if values["enabled"] else 0
        if values:
            self.db.update("reminders", reminder_id, values)
        return self.get(reminder_id)

    def delete(self, reminder_id: int) -> bool:
        if not self.db.query_one("SELECT id FROM reminders WHERE id = ?", (reminder_id,)):
            return False
        self.db.delete("reminders", reminder_id)
        return True

    def due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Return reminders that should fire now; advances recurring ones."""
        current = now or datetime.now()
        rows = self.db.query(
            "SELECT * FROM reminders WHERE enabled = 1 AND due_at <= ? ORDER BY due_at",
            (current.isoformat(timespec="seconds"),),
        )
        fired: list[dict[str, Any]] = []
        for row in rows:
            due_at = parse_dt(row["due_at"])
            if not due_at:
                continue
            fired.append(row)
            if row.get("recurrence"):
                nxt = next_occurrence(due_at, row["recurrence"])
                if nxt:
                    self.db.execute(
                        "UPDATE reminders SET due_at = ?, last_fired = ? WHERE id = ?",
                        (to_iso(nxt), utcnow(), row["id"]),
                    )
                else:
                    self.db.execute(
                        "UPDATE reminders SET enabled = 0, last_fired = ? WHERE id = ?",
                        (utcnow(), row["id"]),
                    )
            else:
                self.db.execute(
                    "UPDATE reminders SET enabled = 0, last_fired = ? WHERE id = ?",
                    (utcnow(), row["id"]),
                )
            self.bus.publish("reminder_due", reminder=row)
        return fired

    def snooze(self, reminder_id: int, minutes: int = 10) -> dict[str, Any]:
        reminder = self.get(reminder_id)
        new_due = datetime.now() + timedelta(minutes=max(1, int(minutes)))
        self.db.execute(
            "UPDATE reminders SET due_at = ?, enabled = 1 WHERE id = ?",
            (to_iso(new_due), reminder["id"]),
        )
        return self.get(reminder_id)

    def stats(self) -> dict[str, Any]:
        return {
            "active": self.db.scalar("SELECT COUNT(*) FROM reminders WHERE enabled = 1", default=0),
            "total": self.db.scalar("SELECT COUNT(*) FROM reminders", default=0),
            "next": (self.list(limit=1) or [None])[0],
        }
