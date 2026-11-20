"""Habits and the focus (Pomodoro) timer."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ..core.database import Database, utcnow
from ..core.events import EventBus, bus as default_bus


class FocusService:
    def __init__(self, db: Database, bus: EventBus | None = None) -> None:
        self.db = db
        self.bus = bus or default_bus

    # ----------------------------------------------------------------- habits
    def add_habit(self, name: str, cadence: str = "daily", target: int = 1) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("a habit needs a name")
        habit_id = self.db.insert(
            "habits",
            {"name": name[:120], "cadence": cadence or "daily", "target": max(1, int(target)), "created_at": utcnow()},
        )
        return self.db.query_one("SELECT * FROM habits WHERE id = ?", (habit_id,)) or {}

    def habits(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM habits ORDER BY name")
        today = date.today().isoformat()
        for row in rows:
            logged = self.db.query_one(
                "SELECT count FROM habit_log WHERE habit_id = ? AND day = ?", (row["id"], today)
            )
            row["today"] = logged["count"] if logged else 0
            row["streak"] = self._streak(row["id"])
        return rows

    def log_habit(self, habit_id: int, amount: int = 1, day: str | None = None) -> dict[str, Any]:
        target_day = day or date.today().isoformat()
        existing = self.db.query_one(
            "SELECT id, count FROM habit_log WHERE habit_id = ? AND day = ?", (habit_id, target_day)
        )
        if existing:
            self.db.execute(
                "UPDATE habit_log SET count = ? WHERE id = ?",
                (existing["count"] + max(1, amount), existing["id"]),
            )
        else:
            self.db.insert("habit_log", {"habit_id": habit_id, "day": target_day, "count": max(1, amount)})
        self.bus.publish("habit_logged", habit_id=habit_id, day=target_day)
        return {"habit_id": habit_id, "day": target_day}

    def _streak(self, habit_id: int) -> int:
        rows = self.db.query(
            "SELECT day FROM habit_log WHERE habit_id = ? ORDER BY day DESC LIMIT 400", (habit_id,)
        )
        days = {row["day"] for row in rows}
        streak = 0
        cursor = date.today()
        if cursor.isoformat() not in days:
            cursor -= timedelta(days=1)
        while cursor.isoformat() in days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def delete_habit(self, habit_id: int) -> bool:
        if not self.db.query_one("SELECT id FROM habits WHERE id = ?", (habit_id,)):
            return False
        self.db.delete("habits", habit_id)
        self.db.execute("DELETE FROM habit_log WHERE habit_id = ?", (habit_id,))
        return True

    # --------------------------------------------------------------- pomodoro
    def start_focus(self, minutes: int = 25, label: str = "") -> dict[str, Any]:
        minutes = max(1, min(int(minutes), 180))
        session_id = self.db.insert(
            "pomodoro",
            {"started_at": utcnow(), "minutes": minutes, "label": label[:200], "completed": 0},
        )
        self.bus.publish("focus_started", minutes=minutes, label=label)
        return self.db.query_one("SELECT * FROM pomodoro WHERE id = ?", (session_id,)) or {}

    def finish_focus(self, session_id: int, completed: bool = True) -> dict[str, Any]:
        self.db.execute(
            "UPDATE pomodoro SET finished_at = ?, completed = ? WHERE id = ?",
            (utcnow(), 1 if completed else 0, session_id),
        )
        self.bus.publish("focus_finished", session_id=session_id, completed=completed)
        return self.db.query_one("SELECT * FROM pomodoro WHERE id = ?", (session_id,)) or {}

    def focus_stats(self, days: int = 7) -> dict[str, Any]:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.db.query(
            "SELECT substr(started_at,1,10) AS day, SUM(minutes) AS minutes, COUNT(*) AS sessions "
            "FROM pomodoro WHERE completed = 1 AND started_at >= ? GROUP BY day ORDER BY day",
            (since,),
        )
        total = sum(int(row["minutes"] or 0) for row in rows)
        return {"days": rows, "total_minutes": total, "sessions": sum(int(row["sessions"] or 0) for row in rows)}
