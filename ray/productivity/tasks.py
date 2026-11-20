"""Tasks with priorities, deadlines, categories and recurrence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..core.database import Database, utcnow
from ..core.events import EventBus, bus as default_bus
from .common import next_occurrence, parse_dt, to_iso

PRIORITIES = ("low", "normal", "high", "urgent")
STATUSES = ("open", "in_progress", "done", "cancelled")


class TaskService:
    def __init__(self, db: Database, bus: EventBus | None = None) -> None:
        self.db = db
        self.bus = bus or default_bus

    # ------------------------------------------------------------------- CRUD
    def create(
        self,
        title: str,
        description: str = "",
        priority: str = "normal",
        due_at: str | None = None,
        category: str = "general",
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise ValueError("a task needs a title")
        if priority not in PRIORITIES:
            priority = "normal"
        stamp = utcnow()
        task_id = self.db.insert(
            "tasks",
            {
                "title": title[:500],
                "description": (description or "")[:5000],
                "priority": priority,
                "status": "open",
                "category": (category or "general")[:80],
                "due_at": to_iso(parse_dt(due_at) if due_at else None),
                "recurrence": recurrence or None,
                "created_at": stamp,
                "updated_at": stamp,
            },
        )
        task = self.get(task_id)
        self.bus.publish("task_created", task=task)
        return task

    def get(self, task_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not row:
            raise KeyError(f"task {task_id} not found")
        return row

    def list(
        self,
        status: str | None = "open",
        category: str | None = None,
        limit: int = 200,
        include_done: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status == "all" or include_done:
            pass
        elif status:
            clauses.append("status = ?")
            params.append(status)
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM tasks {where} ORDER BY "
            "CASE status WHEN 'in_progress' THEN 0 WHEN 'open' THEN 1 ELSE 2 END, "
            "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, "
            "COALESCE(due_at, '9999') ASC, id DESC LIMIT ?",
            (*params, limit),
        )
        for row in rows:
            row["overdue"] = bool(
                row["due_at"] and row["status"] in ("open", "in_progress") and self._is_overdue(row["due_at"])
            )
        return rows

    def update(self, task_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {"title", "description", "priority", "status", "category", "due_at", "recurrence"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if "priority" in values and values["priority"] not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")
        if "status" in values and values["status"] not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        if "due_at" in values:
            values["due_at"] = to_iso(parse_dt(values["due_at"]))
        if not values:
            return self.get(task_id)
        values["updated_at"] = utcnow()
        self.db.update("tasks", task_id, values)
        return self.get(task_id)

    def complete(self, task_id: int) -> dict[str, Any]:
        task = self.get(task_id)
        self.update(task_id, status="done")
        self.db.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (utcnow(), task_id))
        created_next = None
        if task.get("recurrence"):
            reference = parse_dt(task.get("due_at")) or datetime.now()
            nxt = next_occurrence(reference, task["recurrence"])
            if nxt:
                created_next = self.create(
                    title=task["title"],
                    description=task.get("description") or "",
                    priority=task.get("priority", "normal"),
                    due_at=to_iso(nxt),
                    category=task.get("category", "general"),
                    recurrence=task["recurrence"],
                )
        self.bus.publish("task_completed", task_id=task_id, next=created_next)
        return {"task": self.get(task_id), "next": created_next}

    def delete(self, task_id: int) -> bool:
        row = self.db.query_one("SELECT id FROM tasks WHERE id = ?", (task_id,))
        if not row:
            return False
        self.db.delete("tasks", task_id)
        self.bus.publish("task_deleted", task_id=task_id)
        return True

    # ------------------------------------------------------------------ views
    def due_today(self) -> list[dict[str, Any]]:
        today = datetime.now().strftime("%Y-%m-%d")
        return [
            row for row in self.list(status="open", limit=500)
            if (row.get("due_at") or "").startswith(today)
        ]

    def goals_for_today(self) -> list[dict[str, Any]]:
        """Open tasks due today or overdue, ordered by urgency."""
        rows = [row for row in self.list(status="open", limit=500) if row.get("due_at")]
        return [row for row in rows if row["due_at"][:10] <= datetime.now().strftime("%Y-%m-%d")][:20]

    def categories(self) -> list[str]:
        rows = self.db.query("SELECT DISTINCT category FROM tasks WHERE category != '' ORDER BY category")
        return [row["category"] for row in rows]

    def stats(self) -> dict[str, Any]:
        total = self.db.scalar("SELECT COUNT(*) FROM tasks", default=0)
        done = self.db.scalar("SELECT COUNT(*) FROM tasks WHERE status = 'done'", default=0)
        open_count = self.db.scalar(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('open','in_progress')", default=0
        )
        overdue = sum(1 for task in self.list(status="open", limit=500) if task["overdue"])
        completed_today = self.db.scalar(
            "SELECT COUNT(*) FROM tasks WHERE completed_at LIKE ?",
            (datetime.now().strftime("%Y-%m-%d") + "%",),
            0,
        )
        return {
            "total": total,
            "open": open_count,
            "done": done,
            "overdue": overdue,
            "completed_today": completed_today,
            "completion_rate": round(done / total * 100, 1) if total else 0.0,
        }

    @staticmethod
    def _is_overdue(due_at: str) -> bool:
        moment = parse_dt(due_at)
        if not moment:
            return False
        return moment < datetime.now() and not due_at.startswith(datetime.now().strftime("%Y-%m-%dT"))
