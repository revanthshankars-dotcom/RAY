"""Long-term local memory.

Remembers what the user explicitly tells RAY to remember, plus approved
preferences.  Credentials are never stored here: anything that looks like an API
key, password or token is refused at the door.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..core.database import Database, utcnow
from ..core.events import EventBus, bus as default_bus
from ..core.logging_setup import get_logger

log = get_logger("memory.store")

CATEGORIES = ("preference", "project", "person", "fact", "routine", "general", "conversation")

#: shared with settings/notes: one definition of "this looks like a credential"
from ..security.secrets import SECRET_PATTERNS, looks_secret  # noqa: E402,F401


class MemoryStore:
    def __init__(self, db: Database, bus: EventBus | None = None, enabled: bool = True) -> None:
        self.db = db
        self.bus = bus or default_bus
        self.enabled = enabled

    def remember(
        self,
        title: str,
        content: str = "",
        category: str = "general",
        sensitivity: str = "normal",
        source: str = "user",
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("memory is disabled in Settings")
        title = (title or "").strip()
        content = (content or "").strip()
        if not title and not content:
            raise ValueError("nothing to remember")
        if looks_secret(f"{title} {content}"):
            raise ValueError(
                "That looks like a credential. RAY never stores keys or passwords in memory - "
                "use Settings > AI Providers for API keys."
            )
        category = category if category in CATEGORIES else "general"
        stamp = utcnow()
        memory_id = self.db.insert(
            "memories",
            {
                "category": category,
                "title": title[:300] or content[:80],
                "content": content[:8000],
                "sensitivity": sensitivity if sensitivity in ("normal", "private", "sensitive") else "normal",
                "source": source[:80],
                "created_at": stamp,
                "updated_at": stamp,
            },
        )
        memory = self.get(memory_id)
        self.bus.publish("memory_added", memory=memory)
        return memory

    def get(self, memory_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM memories WHERE id = ?", (memory_id,))
        if not row:
            raise KeyError(f"memory {memory_id} not found")
        return row

    def recall(self, query: str = "", category: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if query:
            terms = [term for term in re.split(r"\s+", query) if len(term) > 2][:6]
            if terms:
                like_clauses = " OR ".join("(title LIKE ? OR content LIKE ?)" for _ in terms)
                clauses.append(f"({like_clauses})")
                for term in terms:
                    params.extend([f"%{term}%", f"%{term}%"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM memories {where} ORDER BY updated_at DESC LIMIT ?", (*params, limit)
        )
        if rows:
            self.db.executemany(
                "UPDATE memories SET use_count = use_count + 1, last_used = ? WHERE id = ?",
                [(utcnow(), row["id"]) for row in rows],
            )
        return rows

    def update(self, memory_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {"title", "content", "category", "sensitivity"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return self.get(memory_id)
        if looks_secret(" ".join(str(value) for value in values.values())):
            raise ValueError("that looks like a credential and will not be stored")
        values["updated_at"] = utcnow()
        self.db.update("memories", memory_id, values)
        return self.get(memory_id)

    def forget(self, memory_id: int) -> bool:
        if not self.db.query_one("SELECT id FROM memories WHERE id = ?", (memory_id,)):
            return False
        self.db.delete("memories", memory_id)
        self.bus.publish("memory_deleted", memory_id=memory_id)
        return True

    def forget_all(self) -> int:
        count = int(self.db.scalar("SELECT COUNT(*) FROM memories", default=0) or 0)
        self.db.execute("DELETE FROM memories")
        self.bus.publish("memory_cleared", count=count)
        return count

    def context_block(self, limit: int = 12) -> str:
        """Compact memory context handed to the planner."""
        rows = self.recall(limit=limit)
        if not rows:
            return ""
        lines = ["Things RAY remembers about this user:"]
        for row in rows:
            text = f"{row['title']}: {row['content']}" if row["content"] else row["title"]
            lines.append(f"- [{row['category']}] {text[:240]}")
        return "\n".join(lines)

    # ------------------------------------------------------------ housekeeping
    def prune(self, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self.db.execute(
            "DELETE FROM memories WHERE updated_at < ? AND sensitivity != 'sensitive'", (cutoff,)
        )

    def export_json(self, destination: Path | str) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = self.db.query("SELECT * FROM memories ORDER BY id")
        target.write_text(
            json.dumps({"exported_at": utcnow(), "memories": rows}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return target

    def import_json(self, source: Path | str) -> dict[str, int]:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        memories = payload.get("memories", payload if isinstance(payload, list) else [])
        imported = skipped = 0
        for item in memories:
            try:
                if looks_secret(f"{item.get('title', '')} {item.get('content', '')}"):
                    skipped += 1
                    continue
                self.remember(
                    title=item.get("title", ""),
                    content=item.get("content", ""),
                    category=item.get("category", "general"),
                    sensitivity=item.get("sensitivity", "normal"),
                    source=item.get("source", "import"),
                )
                imported += 1
            except Exception:
                skipped += 1
        return {"imported": imported, "skipped": skipped}

    def stats(self) -> dict[str, Any]:
        by_category = self.db.query(
            "SELECT category, COUNT(*) AS count FROM memories GROUP BY category ORDER BY count DESC"
        )
        return {
            "total": self.db.scalar("SELECT COUNT(*) FROM memories", default=0),
            "by_category": by_category,
            "enabled": self.enabled,
        }
