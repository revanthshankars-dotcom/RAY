"""Permission policy and human approval.

Three levels, enforced for every tool invocation:

LEVEL 1  automatic   - read-only or easily reversible (search, open, monitor)
LEVEL 2  confirmation - changes user data (move/rename files, install, settings)
LEVEL 3  explicit approval - irreversible or high impact (delete, publish, admin)

The manager answers one question: may this tool run right now?  Anything that
needs a human becomes a pending approval row plus an event; the UI shows a
dialog and calls back.

Also provides the emergency stop (freezes everything except reads) and an
optional idle session lock.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from ..core.database import Database, utcnow
from ..core.events import EventBus, bus as default_bus
from ..core.logging_setup import get_logger
from ..core.config import Config

log = get_logger("security.permissions")

LEVEL_AUTO = 1
LEVEL_CONFIRM = 2
LEVEL_APPROVE = 3

LEVEL_LABELS = {
    LEVEL_AUTO: "automatic",
    LEVEL_CONFIRM: "confirmation",
    LEVEL_APPROVE: "explicit approval",
}


@dataclass
class Decision:
    allowed: bool
    level: int
    reason: str = ""
    approval_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "level": self.level,
            "level_label": LEVEL_LABELS.get(self.level, "unknown"),
            "reason": self.reason,
            "approval_id": self.approval_id,
        }


class PermissionDenied(RuntimeError):
    def __init__(self, message: str, decision: Decision | None = None) -> None:
        super().__init__(message)
        self.decision = decision


class PermissionManager:
    def __init__(self, db: Database, config: Config, bus: EventBus | None = None) -> None:
        self.db = db
        self.config = config
        self.bus = bus or default_bus
        self._lock = threading.RLock()
        self._stop = False
        self._locked = False
        self._last_activity = time.time()

    # ---------------------------------------------------------------- policy
    @property
    def emergency_stopped(self) -> bool:
        return self._stop or bool(self.config.get("permissions.emergency_stop", False))

    def emergency_stop(self, active: bool = True) -> None:
        with self._lock:
            self._stop = active
            self.config.set("permissions.emergency_stop", active)
        log.warning("EMERGENCY STOP %s", "ENGAGED" if active else "released")
        self.bus.publish("security", kind="emergency_stop", active=active)

    @property
    def locked(self) -> bool:
        timeout = int(self.config.get("permissions.session_lock_minutes", 0) or 0)
        if timeout <= 0:
            return self._locked
        if self._locked:
            return True
        return (time.time() - self._last_activity) > timeout * 60

    def lock(self) -> None:
        self._locked = True
        self.bus.publish("security", kind="session_locked", locked=True)

    def unlock(self) -> None:
        self._locked = False
        self._last_activity = time.time()
        self.bus.publish("security", kind="session_locked", locked=False)

    def touch(self) -> None:
        self._last_activity = time.time()

    # -------------------------------------------------------------- decisions
    def check(self, tool: str, level: int, args: dict[str, Any] | None = None, reason: str = "") -> Decision:
        """Decide whether ``tool`` at ``level`` may run now."""
        level = max(1, min(3, int(level)))
        if self.emergency_stopped:
            return Decision(False, level, "Emergency stop is engaged. Release it in Security Center.")
        if self.locked and level > LEVEL_AUTO:
            return Decision(False, level, "Session locked. Unlock RAY to continue.")
        mode = str(self.config.get("permissions.mode", "standard"))

        if level == LEVEL_AUTO:
            return Decision(True, level, "automatic")
        if level == LEVEL_CONFIRM:
            if mode == "trusted" or not self.config.get("permissions.level2_confirmation", True):
                return Decision(True, level, "auto-approved (trusted mode)")
            return self._request(tool, args or {}, level, reason or "This action changes your data.")
        if level == LEVEL_APPROVE:
            if mode == "trusted" and not self.config.get("permissions.level3_confirmation", True):
                return Decision(True, level, "auto-approved (trusted mode)")
            return self._request(tool, args or {}, level, reason or "This action is irreversible.")
        return Decision(False, level, "unknown permission level")

    def _request(self, tool: str, args: dict[str, Any], level: int, reason: str) -> Decision:
        # Collapse duplicate requests from retries within the same second.
        recent = self.db.query_one(
            "SELECT id, status FROM approvals WHERE tool = ? AND args_json = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (tool, json.dumps(args, sort_keys=True, default=str)),
        )
        if recent:
            return Decision(False, level, "awaiting your approval", approval_id=recent["id"])
        approval_id = self.db.insert(
            "approvals",
            {
                "ts": utcnow(),
                "tool": tool,
                "args_json": json.dumps(args, sort_keys=True, default=str)[:8000],
                "level": level,
                "reason": reason[:400],
                "status": "pending",
            },
        )
        log.info("approval #%s requested for tool '%s' (level %s)", approval_id, tool, level)
        self.bus.publish(
            "approval",
            approval_id=approval_id,
            tool=tool,
            level=level,
            reason=reason,
            args=args,
        )
        return Decision(False, level, "awaiting your approval", approval_id=approval_id)

    def pending(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM approvals WHERE status = 'pending' ORDER BY id DESC LIMIT 50"
        )
        for row in rows:
            try:
                row["args"] = json.loads(row.pop("args_json"))
            except (json.JSONDecodeError, TypeError):
                row["args"] = {}
        return rows

    def get(self, approval_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if row:
            try:
                row["args"] = json.loads(row.pop("args_json"))
            except (json.JSONDecodeError, TypeError):
                row["args"] = {}
        return row

    def decide(self, approval_id: int, approved: bool, by: str = "user") -> bool:
        row = self.db.query_one("SELECT status FROM approvals WHERE id = ?", (approval_id,))
        if not row or row["status"] != "pending":
            return False
        self.db.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
            ("approved" if approved else "denied", utcnow(), by, approval_id),
        )
        detail = self.db.query_one("SELECT tool, level FROM approvals WHERE id = ?", (approval_id,))
        self.db.execute(
            "INSERT INTO audit_log (ts, actor, action, tool, target, permission_level, result, detail) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                utcnow(),
                by,
                "approval_decision",
                (detail or {}).get("tool", ""),
                str(approval_id),
                int((detail or {}).get("level", 1) or 1),
                "approved" if approved else "denied",
                "",
            ),
        )
        log.info("approval #%s %s by %s", approval_id, "approved" if approved else "denied", by)
        self.bus.publish("approval_decided", approval_id=approval_id, approved=approved)
        return True

    def is_approved(self, approval_id: int) -> bool:
        row = self.db.query_one("SELECT status FROM approvals WHERE id = ?", (approval_id,))
        return bool(row and row["status"] == "approved")

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.config.get("permissions.mode", "standard"),
            "emergency_stop": self.emergency_stopped,
            "locked": self.locked,
            "pending_approvals": len(self.pending()),
            "allow_shell": bool(self.config.get("permissions.allow_shell", False)),
            "levels": LEVEL_LABELS,
        }
