"""Automation engine.

A workflow is a trigger + optional conditions + an ordered list of actions.
Actions are real tool calls, notifications or spoken lines.  The engine refuses
to run destructive tools automatically: those always become a pending approval.

Supported triggers
    manual      only when the user asks
    every       {"type":"every","minutes":30}
    daily       {"type":"daily","at":"18:00"}
    file_added  {"type":"file_added","folder":"C:/Users/me/Downloads"}
    on_start    when RAY starts

Supported conditions
    open_tasks      {"type":"open_tasks","at_least":1}
    time_window     {"type":"time_window","start":"08:00","end":"22:00"}
    weekday         {"type":"weekday","days":["mon","tue"]}
    file_exists     {"type":"file_exists","path":"..."}
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

from ..core.config import Config
from ..core.database import Database, utcnow
from ..core.events import EventBus, bus as default_bus
from ..core.logging_setup import get_logger
from ..productivity.tasks import TaskService
from ..tools.registry import ToolRegistry

log = get_logger("automation.engine")

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class WorkflowService:
    def __init__(self, db: Database, bus: EventBus | None = None) -> None:
        self.db = db
        self.bus = bus or default_bus

    def create(
        self,
        name: str,
        trigger: dict[str, Any],
        actions: list[dict[str, Any]],
        conditions: list[dict[str, Any]] | None = None,
        description: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("a workflow needs a name")
        if not isinstance(trigger, dict) or not trigger.get("type"):
            raise ValueError("a workflow needs a trigger with a 'type'")
        if trigger["type"] not in ("manual", "every", "daily", "file_added", "on_start"):
            raise ValueError(f"unsupported trigger type '{trigger['type']}'")
        if not actions:
            raise ValueError("a workflow needs at least one action")
        if trigger["type"] == "every" and int(trigger.get("minutes", 0) or 0) < 1:
            raise ValueError("'every' triggers need minutes >= 1")
        if trigger["type"] == "daily" and not trigger.get("at"):
            raise ValueError("'daily' triggers need an 'at' time like 18:00")
        if trigger["type"] == "file_added" and not trigger.get("folder"):
            raise ValueError("'file_added' triggers need a folder")
        level = 1
        if any("delete" in str(action).lower() or "shutdown" in str(action).lower() for action in actions):
            level = 3
        elif any("move" in str(action).lower() or "rename" in str(action).lower() for action in actions):
            level = 2
        stamp = utcnow()
        workflow_id = self.db.insert(
            "workflows",
            {
                "name": name[:200],
                "description": (description or "")[:1000],
                "enabled": 1 if enabled else 0,
                "trigger_json": json.dumps(trigger),
                "conditions_json": json.dumps(conditions or []),
                "actions_json": json.dumps(actions),
                "permission_level": level,
                "created_at": stamp,
                "updated_at": stamp,
            },
        )
        workflow = self.get(workflow_id)
        self.bus.publish("workflow_created", workflow=workflow)
        return workflow

    def get(self, workflow_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
        if not row:
            raise KeyError(f"workflow {workflow_id} not found")
        return _decode_workflow(row)

    def list(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE enabled = 1" if enabled_only else ""
        rows = self.db.query(f"SELECT * FROM workflows {where} ORDER BY name")
        return [_decode_workflow(row) for row in rows]

    def update(self, workflow_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {"name", "description", "enabled", "trigger", "conditions", "actions"}
        values: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("trigger", "conditions", "actions"):
                values[f"{key}_json"] = json.dumps(value)
            elif key == "enabled":
                values["enabled"] = 1 if value else 0
            else:
                values[key] = value
        if values:
            values["updated_at"] = utcnow()
            self.db.update("workflows", workflow_id, values)
        return self.get(workflow_id)

    def delete(self, workflow_id: int) -> bool:
        if not self.db.query_one("SELECT id FROM workflows WHERE id = ?", (workflow_id,)):
            return False
        self.db.delete("workflows", workflow_id)
        self.db.execute("DELETE FROM workflow_runs WHERE workflow_id = ?", (workflow_id,))
        return True

    def set_enabled(self, workflow_id: int, enabled: bool) -> dict[str, Any]:
        return self.update(workflow_id, enabled=enabled)

    def start_run(self, workflow_id: int, trigger: str) -> int:
        return self.db.insert(
            "workflow_runs",
            {"workflow_id": workflow_id, "started_at": utcnow(), "status": "running", "trigger": trigger},
        )

    def finish_run(self, run_id: int, status: str, detail: str = "") -> None:
        self.db.execute(
            "UPDATE workflow_runs SET finished_at = ?, status = ?, detail = ? WHERE id = ?",
            (utcnow(), status, (detail or "")[:4000], run_id),
        )

    def runs(self, workflow_id: int | None = None, limit: int = 30) -> list[dict[str, Any]]:
        if workflow_id:
            return self.db.query(
                "SELECT * FROM workflow_runs WHERE workflow_id = ? ORDER BY id DESC LIMIT ?",
                (workflow_id, limit),
            )
        return self.db.query("SELECT * FROM workflow_runs ORDER BY id DESC LIMIT ?", (limit,))


def _decode_workflow(row: dict[str, Any]) -> dict[str, Any]:
    for key, target in (("trigger_json", "trigger"), ("conditions_json", "conditions"), ("actions_json", "actions")):
        try:
            row[target] = json.loads(row.get(key) or "null") or ({} if target == "trigger" else [])
        except json.JSONDecodeError:
            row[target] = {} if target == "trigger" else []
        row.pop(key, None)
    row["enabled"] = bool(row.get("enabled"))
    return row


class AutomationEngine:
    def __init__(
        self,
        db: Database,
        config: Config,
        tools: ToolRegistry,
        tasks: TaskService | None = None,
        bus: EventBus | None = None,
        state_path: Path | None = None,
        notifier=None,
    ) -> None:
        self.db = db
        self.config = config
        self.tools = tools
        self.tasks = tasks or TaskService(db)
        self.bus = bus or default_bus
        self.workflows = WorkflowService(db, self.bus)
        self.state_path = state_path
        self.notifier = notifier
        self._state: dict[str, Any] = self._load_state()
        self._lock = threading.RLock()
        self.last_tick: float | None = None

    # ------------------------------------------------------------------ state
    def _load_state(self) -> dict[str, Any]:
        if self.state_path and Path(self.state_path).exists():
            try:
                return json.loads(Path(self.state_path).read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_state(self) -> None:
        if not self.state_path:
            return
        try:
            Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.state_path).write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        except OSError as exc:  # pragma: no cover
            log.warning("could not persist automation state: %s", exc)

    # --------------------------------------------------------------- triggers
    def _trigger_due(self, workflow: dict[str, Any], now: datetime) -> tuple[bool, str]:
        trigger = workflow.get("trigger") or {}
        kind = trigger.get("type")
        key = f"workflow:{workflow['id']}"
        state = self._state.get(key, {})
        if kind == "manual":
            return False, "manual"
        if kind == "on_start":
            return not state.get("fired_for_start"), "start"
        if kind == "every":
            minutes = max(1, int(trigger.get("minutes", 30)))
            last = float(state.get("last_run", 0) or 0)
            return (time.time() - last) >= minutes * 60, "schedule"
        if kind == "daily":
            target = str(trigger.get("at", "00:00"))
            try:
                hour, minute = [int(part) for part in target.split(":")[:2]]
            except (ValueError, IndexError):
                return False, "schedule"
            today_key = now.strftime("%Y-%m-%d")
            last_fired = state.get("last_daily")
            if last_fired == today_key:
                return False, "schedule"
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now >= scheduled:
                return True, "schedule"
            return False, "schedule"
        if kind == "file_added":
            folder = Path(str(trigger.get("folder", ""))).expanduser()
            if not folder.is_dir():
                return False, "file_watch"
            try:
                newest = max(
                    (entry.stat().st_mtime for entry in folder.iterdir() if entry.is_file()),
                    default=0,
                )
            except OSError:
                return False, "file_watch"
            previous = float(state.get("folder_mtime", 0) or 0)
            self._state.setdefault(key, {})["folder_mtime"] = newest
            return newest > previous > 0, "file_watch"
        return False, "unknown"

    def _check_conditions(self, conditions: list[dict[str, Any]], now: datetime) -> tuple[bool, str]:
        for condition in conditions or []:
            kind = condition.get("type")
            if kind == "open_tasks":
                minimum = int(condition.get("at_least", 1))
                open_count = int(self.tasks.stats().get("open", 0))
                if open_count < minimum:
                    return False, f"only {open_count} open task(s), needs {minimum}"
            elif kind == "time_window":
                try:
                    start = dtime.fromisoformat(str(condition.get("start", "00:00")))
                    end = dtime.fromisoformat(str(condition.get("end", "23:59")))
                except ValueError:
                    return False, "invalid time window"
                current = now.time()
                inside = start <= current <= end if start <= end else (current >= start or current <= end)
                if not inside:
                    return False, "outside the allowed time window"
            elif kind == "weekday":
                days = [str(day).lower()[:3] for day in condition.get("days", [])]
                if WEEKDAYS[now.weekday()] not in days:
                    return False, f"not a {condition.get('days')} day"
            elif kind == "file_exists":
                if not Path(str(condition.get("path", ""))).expanduser().exists():
                    return False, "required file/folder is missing"
            else:
                return False, f"unknown condition '{kind}'"
        return True, ""

    # ------------------------------------------------------------------ exec
    def run(self, workflow_id: int, trigger: str = "manual", dry_run: bool | None = None) -> dict[str, Any]:
        workflow = self.workflows.get(workflow_id)
        if not workflow.get("enabled") and trigger != "manual":
            return {"ok": False, "status": "disabled", "workflow": workflow["name"]}
        dry_run = bool(self.config.get("automation.dry_run", False) if dry_run is None else dry_run)
        run_id = self.workflows.start_run(workflow_id, trigger)
        results: list[dict[str, Any]] = []
        status = "ok"
        max_steps = int(self.config.get("automation.max_steps", 25) or 25)

        for index, action in enumerate(workflow.get("actions", [])[:max_steps], start=1):
            kind = action.get("type", "tool")
            if dry_run:
                results.append({"step": index, "action": action, "status": "dry_run"})
                continue
            try:
                if kind == "tool":
                    tool_name = action.get("tool", "")
                    tool = self.tools.get(tool_name)
                    if tool is None:
                        raise ValueError(f"unknown tool '{tool_name}'")
                    if tool.destructive:
                        raise PermissionError(
                            f"'{tool_name}' is destructive, so it will not run automatically. "
                            "Run the workflow by hand and approve it."
                        )
                    result = self.tools.call(tool_name, action.get("args", {}) or {}, actor="automation")
                    entry = {
                        "step": index,
                        "tool": tool_name,
                        "ok": result.ok,
                        "error": result.error,
                        "requires_approval": result.requires_approval,
                    }
                    results.append(entry)
                    if not result.ok:
                        if result.requires_approval:
                            status = "awaiting_approval"
                            break
                        status = "failed"
                        break
                elif kind == "notify":
                    title = str(action.get("title", workflow["name"]))
                    body = str(action.get("body", ""))
                    self.notify(title, body, kind="automation")
                    results.append({"step": index, "notify": title, "ok": True})
                elif kind == "speak":
                    text = str(action.get("text", ""))
                    self.bus.publish("speak_request", text=text, origin="automation")
                    results.append({"step": index, "speak": text[:200], "ok": True})
                elif kind == "log":
                    log.info("workflow '%s': %s", workflow["name"], action.get("text", ""))
                    results.append({"step": index, "log": action.get("text", ""), "ok": True})
                else:
                    raise ValueError(f"unknown action type '{kind}'")
            except Exception as exc:
                log.error("workflow '%s' step %s failed: %s", workflow["name"], index, exc)
                results.append({"step": index, "action": action, "ok": False, "error": str(exc)})
                status = "failed"
                break

        detail = json.dumps(results, default=str)[:4000]
        self.workflows.finish_run(run_id, status, detail)
        self.db.execute(
            "UPDATE workflows SET last_run = ?, run_count = run_count + 1 WHERE id = ?",
            (utcnow(), workflow_id),
        )
        if self._state is not None:
            key = f"workflow:{workflow_id}"
            self._state.setdefault(key, {})["last_run"] = time.time()
            if trigger in ("schedule", "start"):
                self._state[key]["last_daily"] = datetime.now().strftime("%Y-%m-%d")
                self._state[key]["fired_for_start"] = trigger == "start"
            self._save_state()
        self.bus.publish(
            "workflow_run", workflow=workflow["name"], workflow_id=workflow_id,
            status=status, steps=len(results), trigger=trigger,
        )
        return {
            "ok": status == "ok",
            "status": status,
            "workflow": workflow["name"],
            "workflow_id": workflow_id,
            "run_id": run_id,
            "steps": results,
        }

    # ------------------------------------------------------------------- tick
    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        if not self.config.get("automation.enabled", True):
            return []
        now = now or datetime.now()
        executed: list[dict[str, Any]] = []
        with self._lock:
            for workflow in self.workflows.list(enabled_only=True):
                try:
                    due, trigger_name = self._trigger_due(workflow, now)
                    if not due:
                        continue
                    ok, reason = self._check_conditions(workflow.get("conditions") or [], now)
                    if not ok:
                        log.info("workflow '%s' skipped: %s", workflow["name"], reason)
                        continue
                    executed.append(self.run(workflow["id"], trigger=trigger_name))
                except Exception as exc:
                    log.exception("workflow '%s' crashed: %s", workflow.get("name"), exc)
                    self.notify("Automation failed", f"{workflow.get('name')}: {exc}", kind="error")
            self.last_tick = time.time()
        return executed

    def run_on_start(self) -> list[dict[str, Any]]:
        executed = []
        for workflow in self.workflows.list(enabled_only=True):
            if (workflow.get("trigger") or {}).get("type") == "on_start":
                executed.append(self.run(workflow["id"], trigger="start"))
        return executed

    def notify(self, title: str, body: str = "", kind: str = "info") -> int:
        notification_id = self.db.insert(
            "notifications",
            {"ts": utcnow(), "title": title[:200], "body": (body or "")[:1000], "kind": kind, "read": 0},
        )
        self.bus.publish("notification", title=title, body=body, kind=kind, id=notification_id)
        if self.notifier:
            try:
                self.notifier(title, body)
            except Exception:  # pragma: no cover
                pass
        return notification_id

    def describe(self) -> dict[str, Any]:
        workflows = self.workflows.list()
        runs = self.workflows.runs(limit=10)
        return {
            "enabled": bool(self.config.get("automation.enabled", True)),
            "workflows": len(workflows),
            "active": sum(1 for workflow in workflows if workflow.get("enabled")),
            "dry_run": bool(self.config.get("automation.dry_run", False)),
            "last_tick": self.last_tick,
            "recent_runs": runs,
        }
