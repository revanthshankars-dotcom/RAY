"""Response cache and usage accounting.

Cheap efficiency wins on an 8 GB Celeron: identical reasoning prompts are not
sent twice, and every call is metered so quota exhaustion is visible before it
happens.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from ..core.database import Database, utcnow


class ResponseCache:
    def __init__(self, db: Database, default_ttl: int = 1800) -> None:
        self.db = db
        self.default_ttl = default_ttl

    @staticmethod
    def make_key(provider: str, model: str, messages: list, extra: str = "") -> str:
        blob = json.dumps(
            {
                "provider": provider,
                "model": model,
                "messages": [
                    message if isinstance(message, dict) else {"role": m.role, "content": m.content}
                    for message in messages
                    for m in [message]
                ],
                "extra": extra,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: str) -> str | None:
        row = self.db.query_one("SELECT response, expires_at FROM cache WHERE key = ?", (key,))
        if not row:
            return None
        if float(row["expires_at"]) < time.time():
            self.db.execute("DELETE FROM cache WHERE key = ?", (key,))
            return None
        return row["response"]

    def put(self, key: str, response: str, provider: str = "", model: str = "", ttl: int | None = None) -> None:
        now = time.time()
        ttl = self.default_ttl if ttl is None else ttl
        self.db.execute(
            "INSERT OR REPLACE INTO cache (key, provider, model, response, created_at, expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (key, provider, model, response, now, now + max(1, ttl)),
        )

    def purge_expired(self) -> int:
        return self.db.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))

    def clear(self) -> int:
        return self.db.execute("DELETE FROM cache")

    def stats(self) -> dict[str, Any]:
        total = self.db.scalar("SELECT COUNT(*) FROM cache", default=0)
        live = self.db.scalar("SELECT COUNT(*) FROM cache WHERE expires_at > ?", (time.time(),), 0)
        return {"entries": int(total or 0), "live": int(live or 0)}


class UsageTracker:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        provider: str,
        model: str = "",
        kind: str = "chat",
        input_tokens: int = 0,
        output_tokens: int = 0,
        characters: int = 0,
        ok: bool = True,
        error: str = "",
        cached: bool = False,
        cost_usd: float = 0.0,
    ) -> None:
        self.db.execute(
            "INSERT INTO usage (ts, provider, model, kind, input_tokens, output_tokens, characters, ok, error, cached, cost_usd) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                utcnow(), provider, model, kind, int(input_tokens), int(output_tokens),
                int(characters), 1 if ok else 0, (error or "")[:300], 1 if cached else 0,
                round(float(cost_usd or 0.0), 6),
            ),
        )

    def today(self) -> dict[str, Any]:
        rows = self.db.query(
            "SELECT provider, "
            "COUNT(*) AS calls, "
            "SUM(CASE WHEN ok = 1 THEN 1 ELSE 0 END) AS successes, "
            "SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failures, "
            "SUM(input_tokens) AS input_tokens, "
            "SUM(output_tokens) AS output_tokens, "
            "SUM(characters) AS characters, "
            "SUM(cached) AS cached, "
            "COALESCE(SUM(cost_usd), 0) AS cost_usd "
            "FROM usage WHERE ts >= datetime('now','start of day') GROUP BY provider"
        )
        totals = {
            "calls": sum(int(row["calls"] or 0) for row in rows),
            "failures": sum(int(row["failures"] or 0) for row in rows),
            "cached": sum(int(row["cached"] or 0) for row in rows),
            "input_tokens": sum(int(row["input_tokens"] or 0) for row in rows),
            "output_tokens": sum(int(row["output_tokens"] or 0) for row in rows),
            "characters": sum(int(row["characters"] or 0) for row in rows),
            "cost_usd": round(sum(float(row["cost_usd"] or 0) for row in rows), 6),
        }
        return {"by_provider": rows, "totals": totals}

    def recent_errors(self, limit: int = 10) -> list[dict]:
        return self.db.query(
            "SELECT ts, provider, kind, error FROM usage WHERE ok = 0 ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    def history(self, days: int = 14) -> list[dict]:
        return self.db.query(
            "SELECT substr(ts,1,10) AS day, provider, COUNT(*) AS calls, "
            "COALESCE(SUM(cost_usd), 0) AS cost_usd "
            "FROM usage WHERE ts >= datetime('now', ?) GROUP BY day, provider ORDER BY day",
            (f"-{int(days)} days",),
        )

    def month_spend(self, provider: str) -> dict[str, Any]:
        """Spend for the current calendar month, from the local meter.

        ``cost_usd`` is what providers reported; rows where a provider bill was
        not reported still count as calls but contribute estimated cost, and
        ``estimated`` says so honestly.
        """
        row = self.db.query_one(
            "SELECT COUNT(*) AS calls, "
            "COALESCE(SUM(cost_usd), 0) AS cost_usd, "
            "SUM(CASE WHEN cost_usd > 0 THEN 1 ELSE 0 END) AS reported "
            "FROM usage WHERE provider = ? AND ts >= datetime('now','start of month')",
            (provider,),
        ) or {}
        calls = int(row.get("calls") or 0)
        reported = int(row.get("reported") or 0)
        return {
            "calls": calls,
            "cost_usd": round(float(row.get("cost_usd") or 0.0), 6),
            "reported_calls": reported,
            "estimated": bool(calls and reported < calls),
        }

    def spend_month(self) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT provider, COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost_usd "
            "FROM usage WHERE ts >= datetime('now','start of month') GROUP BY provider"
        )

    def calls_today(self, provider: str) -> int:
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM usage WHERE provider = ? AND ts >= datetime('now','start of day')",
                (provider,),
                0,
            )
            or 0
        )
