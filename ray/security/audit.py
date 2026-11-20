"""Append-only audit trail for every operation RAY performs."""

from __future__ import annotations

import json
from typing import Any

from ..core.database import Database, utcnow
from ..core.logging_setup import redact


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        action: str,
        tool: str = "",
        target: str = "",
        level: int = 1,
        result: str = "ok",
        detail: Any = "",
        actor: str = "ray",
    ) -> int:
        if not isinstance(detail, str):
            detail = json.dumps(detail, default=str)
        return self.db.insert(
            "audit_log",
            {
                "ts": utcnow(),
                "actor": actor,
                "action": action,
                "tool": tool,
                "target": redact(str(target))[:400],
                "permission_level": int(level),
                "result": result,
                "detail": redact(detail or "")[:2000],
            },
        )

    def recent(self, limit: int = 100, result: str | None = None) -> list[dict]:
        if result:
            return self.db.query(
                "SELECT * FROM audit_log WHERE result = ? ORDER BY id DESC LIMIT ?", (result, limit)
            )
        return self.db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))

    def search(self, text: str, limit: int = 100) -> list[dict]:
        like = f"%{text}%"
        return self.db.query(
            "SELECT * FROM audit_log WHERE action LIKE ? OR tool LIKE ? OR target LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (like, like, like, limit),
        )

    def stats(self) -> dict:
        total = self.db.scalar("SELECT COUNT(*) FROM audit_log", default=0)
        failures = self.db.scalar("SELECT COUNT(*) FROM audit_log WHERE result != 'ok'", default=0)
        by_tool = self.db.query(
            "SELECT tool, COUNT(*) AS n FROM audit_log WHERE tool != '' GROUP BY tool ORDER BY n DESC LIMIT 12"
        )
        return {"total": total, "failures": failures, "top_tools": by_tool}

    def prune(self, keep_days: int = 90) -> int:
        return self.db.execute(
            "DELETE FROM audit_log WHERE ts < datetime('now', ?)", (f"-{int(keep_days)} days",)
        )
