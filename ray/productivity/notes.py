"""Notes: quick capture, categories, pinning, search, export."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.database import Database, utcnow
from ..core.events import EventBus, bus as default_bus


class NoteService:
    def __init__(self, db: Database, bus: EventBus | None = None) -> None:
        self.db = db
        self.bus = bus or default_bus

    def create(self, title: str = "", body: str = "", category: str = "general") -> dict[str, Any]:
        if not (title or "").strip() and not (body or "").strip():
            raise ValueError("a note needs a title or some content")
        stamp = utcnow()
        note_id = self.db.insert(
            "notes",
            {
                "title": (title or "").strip()[:300] or _auto_title(body),
                "body": body or "",
                "category": (category or "general")[:80],
                "pinned": 0,
                "created_at": stamp,
                "updated_at": stamp,
            },
        )
        note = self.get(note_id)
        self.bus.publish("note_created", note=note)
        return note

    def get(self, note_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM notes WHERE id = ?", (note_id,))
        if not row:
            raise KeyError(f"note {note_id} not found")
        return row

    def list(self, category: str | None = None, query: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if query:
            clauses.append("(title LIKE ? OR body LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.db.query(
            f"SELECT * FROM notes {where} ORDER BY pinned DESC, updated_at DESC LIMIT ?",
            (*params, limit),
        )

    def update(self, note_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {"title", "body", "category", "pinned"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return self.get(note_id)
        if "pinned" in values:
            values["pinned"] = 1 if values["pinned"] else 0
        values["updated_at"] = utcnow()
        self.db.update("notes", note_id, values)
        return self.get(note_id)

    def append(self, note_id: int, text: str) -> dict[str, Any]:
        note = self.get(note_id)
        body = (note["body"] + "\n" + text).strip()
        return self.update(note_id, body=body)

    def delete(self, note_id: int) -> bool:
        if not self.db.query_one("SELECT id FROM notes WHERE id = ?", (note_id,)):
            return False
        self.db.delete("notes", note_id)
        self.bus.publish("note_deleted", note_id=note_id)
        return True

    def toggle_pin(self, note_id: int) -> dict[str, Any]:
        note = self.get(note_id)
        return self.update(note_id, pinned=0 if note["pinned"] else 1)

    def categories(self) -> list[str]:
        return [row["category"] for row in self.db.query(
            "SELECT DISTINCT category FROM notes WHERE category != '' ORDER BY category"
        )]

    def export_markdown(self, destination: Path | str) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# RAY notes export", "", f"_Generated {utcnow()}_", ""]
        for note in self.list(limit=10000):
            lines.extend([f"## {note['title']}", f"_{note['created_at']} · {note['category']}_", "", note["body"], ""])
        target.write_text("\n".join(lines), encoding="utf-8")
        return target

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.list(query=query, limit=limit)

    def stats(self) -> dict[str, Any]:
        return {
            "total": self.db.scalar("SELECT COUNT(*) FROM notes", default=0),
            "pinned": self.db.scalar("SELECT COUNT(*) FROM notes WHERE pinned = 1", default=0),
            "categories": len(self.categories()),
        }


def _auto_title(body: str) -> str:
    first = " ".join((body or "").strip().split())
    return (first[:60] + "…") if len(first) > 60 else (first or "Untitled note")
