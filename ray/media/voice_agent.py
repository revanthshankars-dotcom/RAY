"""Live voice conversations with a Fish Audio agent, plus local accounting.

RAY already speaks through Fish (``POST /api/voice/speak``, server side, key in
the vault).  A Fish *agent* is different: the widget opens a WebRTC call from the
browser to Fish, so the audio never passes through RAY and the minutes are billed
by Fish against your own account.

That means RAY cannot meter the call the way it meters Gemini/OpenRouter tokens.
What it can do - and what this module does - is make the minutes **visible**:
every call is timed, stored locally, and counted against a monthly budget you
set.  The budget never blocks a call (the user asked for warn-only); it turns the
Voice page amber and says by how much you are over.

Nothing here needs a network connection and nothing here stores audio.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..core.database import Database

AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

#: Fish sells voice minutes per account; this is a soft, local budget only.
MIN_CAP_MINUTES = 1
MAX_CAP_MINUTES = 6000


def month_key(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return moment.strftime("%Y-%m")


class VoiceAgent:
    """Settings, session accounting and honest status for the Fish agent widget."""

    def __init__(self, db: Database, config: Any, audit: Any = None) -> None:
        self.db = db
        self.config = config
        self.audit = audit

    # ------------------------------------------------------------- settings
    def enabled(self) -> bool:
        return bool(self.config.get("voice.agent_enabled", False))

    def agent_id(self) -> str:
        return str(self.config.get("voice.agent_id", "") or "").strip()

    def cap_minutes(self) -> int:
        try:
            value = int(self.config.get("voice.agent_minutes_cap", 30) or 0)
        except (TypeError, ValueError):
            value = 30
        return max(MIN_CAP_MINUTES, min(MAX_CAP_MINUTES, value))

    def save(self, *, enabled: bool | None = None, agent_id: str | None = None,
             cap_minutes: int | None = None) -> dict[str, Any]:
        changed: dict[str, Any] = {}
        if agent_id is not None:
            candidate = agent_id.strip()
            if candidate and not AGENT_ID_RE.match(candidate):
                raise ValueError(
                    "an agent id is 8-64 letters, digits, dashes or underscores - "
                    "copy it from the Fish Audio agent page"
                )
            if candidate != self.agent_id():
                changed["agent_id"] = candidate
                self.config.set("voice.agent_id", candidate, save=False)
        if cap_minutes is not None:
            try:
                cap = int(cap_minutes)
            except (TypeError, ValueError):
                raise ValueError("the monthly budget is a whole number of minutes") from None
            cap = max(MIN_CAP_MINUTES, min(MAX_CAP_MINUTES, cap))
            if cap != self.cap_minutes():
                changed["cap_minutes"] = cap
                self.config.set("voice.agent_minutes_cap", cap, save=False)
        if enabled is not None:
            wanted = bool(enabled) and bool(self.agent_id())
            if wanted != self.enabled():
                changed["enabled"] = wanted
                self.config.set("voice.agent_enabled", wanted, save=False)
        if changed:
            self.config.save()
            if self.audit is not None:
                self.audit.record(
                    "voice_agent_settings",
                    tool="voice",
                    target=", ".join(sorted(changed)),
                    result="ok",
                    detail=changed,
                )
        return self.describe()

    # ------------------------------------------------------------- sessions
    def record_session(self, seconds: float, *, started_at: str = "", note: str = "") -> dict[str, Any]:
        """Store a finished call. Never rejects: the budget only warns."""
        try:
            duration = max(0.0, float(seconds))
        except (TypeError, ValueError):
            raise ValueError("seconds must be a number") from None
        if duration > 6 * 60 * 60:
            duration = 6 * 60 * 60
        started = started_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.db.insert(
            "voice_sessions",
            {
                "started_at": started,
                "ended_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "seconds": round(duration, 1),
                "agent_id": self.agent_id(),
                "note": note[:200],
            },
        )
        usage = self.usage()
        if self.audit is not None:
            self.audit.record(
                "voice_agent_session",
                tool="voice",
                target=f"{usage['seconds_used']:.0f}s this month",
                result="ok",
                detail={
                    "seconds": round(duration, 1),
                    "minutes_used": usage["minutes_used"],
                    "cap_minutes": usage["cap_minutes"],
                    "over_cap": usage["over_cap"],
                },
            )
        return usage

    def usage(self) -> dict[str, Any]:
        month = month_key()
        rows = self.db.query(
            "SELECT COALESCE(SUM(seconds), 0) AS seconds, COUNT(*) AS calls "
            "FROM voice_sessions WHERE substr(started_at, 1, 7) = ?",
            (month,),
        )
        row = rows[0] if rows else {"seconds": 0, "calls": 0}
        seconds = float(row.get("seconds") or 0)
        cap = self.cap_minutes()
        return {
            "month": month,
            "seconds_used": round(seconds, 1),
            "minutes_used": round(seconds / 60, 1),
            "calls": int(row.get("calls") or 0),
            "cap_minutes": cap,
            "minutes_left": round(max(0.0, cap - seconds / 60), 1),
            "over_cap": seconds / 60 > cap,
            "percent": min(100, round((seconds / 60) / cap * 100)) if cap else 0,
        }

    def reset_month(self) -> dict[str, Any]:
        """Clear this month's counter - and record exactly what was cleared.

        Deleting usage without a trace would be the dishonest version of this
        feature, so the totals are written to the audit log first.
        """
        month = month_key()
        rows = self.db.query(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(seconds), 0) AS seconds "
            "FROM voice_sessions WHERE substr(started_at, 1, 7) = ?",
            (month,),
        )
        row = rows[0] if rows else {"calls": 0, "seconds": 0}
        cleared_calls = int(row.get("calls") or 0)
        cleared_seconds = round(float(row.get("seconds") or 0), 1)
        if cleared_calls:
            self.db.execute(
                "DELETE FROM voice_sessions WHERE substr(started_at, 1, 7) = ?", (month,)
            )
        if self.audit is not None:
            self.audit.record(
                "voice_agent_usage_reset",
                tool="voice",
                target=month,
                result="ok",
                detail={"cleared_calls": cleared_calls, "cleared_seconds": cleared_seconds},
            )
        usage = self.usage()
        usage["cleared_calls"] = cleared_calls
        usage["cleared_seconds"] = cleared_seconds
        return usage

    # --------------------------------------------------------------- status
    def describe(self) -> dict[str, Any]:
        """What the UI needs to draw the panel, including the honest caveats."""
        return {
            "enabled": self.enabled() and bool(self.agent_id()),
            "agent_id": self.agent_id(),
            "configured": bool(self.agent_id()),
            "cap_minutes": self.cap_minutes(),
            "usage": self.usage(),
            # Served from RAY's own disk: no CDN, so the panel works offline
            # until you actually press Start (the call itself needs the network).
            "script": "/assets/fish-agent-widget.js",
            "widget_version": "1.1.0",
            "transport": "webrtc-livekit",
            "notes": [
                "The call goes browser -> Fish Audio over WebRTC; RAY does not relay the audio "
                "and cannot read or record it.",
                "Fish bills these minutes on your own account. RAY counts them locally and warns; "
                "it never blocks a call.",
                "Your Fish agent must be public with this page's origin on its allowlist, "
                "otherwise Fish refuses the session.",
                "The microphone is requested by the browser only when you press Start live call.",
            ],
        }
