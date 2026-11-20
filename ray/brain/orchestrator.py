"""The orchestrator: one place where requests become verified actions.

Flow for a request:

    text -> planner (local intents, else AI plan)
         -> agent selection
         -> step-by-step execution through the permission-gated tool registry
         -> per-step progress events, cancellation, verification
         -> a readable result (and a spoken line when voice is on)

Nothing is reported as done unless the tool said it was done and, where a tool
supports it, verification passed.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..agents.registry import AgentRegistry
from ..ai.base import ChatMessage, ProviderError
from ..core.config import Config
from ..core.database import Database, utcnow
from ..core.events import EventBus, bus as default_bus
from ..core.logging_setup import get_logger
from ..tools.registry import ToolRegistry
from .planner import Plan, Planner

log = get_logger("brain.orchestrator")


@dataclass
class StepOutcome:
    index: int
    tool: str
    args: dict[str, Any]
    ok: bool
    data: Any = None
    error: str = ""
    verified: bool | None = None
    requires_approval: int | None = None
    duration_ms: int = 0
    why: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "args": self.args,
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "verified": self.verified,
            "requires_approval": self.requires_approval,
            "duration_ms": self.duration_ms,
            "why": self.why,
        }


@dataclass
class TaskResult:
    task_id: str
    text: str
    status: str
    agent: str = "assistant"
    plan: dict[str, Any] = field(default_factory=dict)
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    engine: str = ""  # which AI engine answered ("" when the answer was local)
    speak: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str = ""

    @property
    def duration_ms(self) -> int:
        return int(((self.finished_at or time.time()) - self.started_at) * 1000)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "text": self.text,
            "status": self.status,
            "agent": self.agent,
            "plan": self.plan,
            "outcomes": self.outcomes,
            "answer": self.answer,
            "engine": self.engine,
            "speak": self.speak,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "finished_at": self.finished_at,
        }


class Orchestrator:
    def __init__(
        self,
        db: Database,
        router,
        tools: ToolRegistry,
        planner: Planner,
        memory=None,
        bus: EventBus | None = None,
        config: Config | None = None,
        agents: AgentRegistry | None = None,
    ) -> None:
        self.db = db
        self.router = router
        self.tools = tools
        self.planner = planner
        self.memory = memory
        self.bus = bus or default_bus
        self.config = config
        self.agents = agents or AgentRegistry()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._last_engine = ""
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ public
    def handle(
        self,
        text: str,
        agent: str | None = None,
        history: list[dict[str, str]] | None = None,
        approval_id: int | None = None,
        resume_task_id: str | None = None,
        engine: str | None = None,
    ) -> TaskResult:
        text = (text or "").strip()
        if resume_task_id:
            return self.resume(resume_task_id, approval_id or 0)

        task_id = uuid.uuid4().hex[:12]
        state = {"cancel": threading.Event(), "started": time.time()}
        with self._lock:
            self._tasks[task_id] = state

        plan: Plan | None = None
        try:
            plan = self.planner.plan(text, agent=agent, history=history, engine=engine)
            spec = self.agents.select(text, suggested=plan.agent)
            result = TaskResult(
                task_id=task_id, text=text, status="running", agent=spec.name,
                plan=plan.as_dict(), engine=plan.engine,
            )
            self.bus.publish("task_started", task_id=task_id, text=text, agent=spec.name, steps=len(plan.steps))

            if not plan.steps:
                if plan.answer and plan.source != "offline":
                    result.answer = plan.answer
                else:
                    # Planning failed (for example: no tool-capable provider is
                    # configured).  A conversational provider can still answer,
                    # so try it before falling back to the offline notice.
                    try:
                        reply = self._chat_result(
                            text, history=history, agent=spec.name, engine=engine
                        )
                        result.engine = self._last_engine
                        result.answer = reply + (
                            "\n\nNote: I answered without tools. Tool actions (files, apps, "
                            "automation) need a tool-capable model - Gemini with a key is the "
                            "default planner."
                            if plan.source == "offline" else ""
                        )
                    except ProviderError:
                        result.answer = plan.answer or self.chat(text, history=history, agent=spec.name)
                result.status = "answered"
                result.speak = plan.speak or _first_sentence(result.answer)
                return self._finish(result)

            self._run_steps(result, plan, spec, state, approval_id)
            # _finish writes the agent_runs row and publishes task_finished; a
            # task that skips it is invisible in the Agents view and in history.
            return self._finish(result)
        except Exception as exc:  # never let a task kill the engine
            log.exception("task failed")
            result = TaskResult(task_id=task_id, text=text, status="error", error=str(exc))
            self.bus.publish("task_failed", task_id=task_id, error=str(exc))
            return self._finish(result)
        finally:
            with self._lock:
                self._tasks.pop(task_id, None)

    def _run_steps(
        self,
        result: TaskResult,
        plan: Plan,
        spec,
        state: dict[str, Any],
        approval_id: int | None = None,
        start_index: int = 0,
    ) -> None:
        max_steps = min(int(spec.max_steps or 12), int(self.config.get("agents.max_steps", 20) or 20))
        deadline = time.time() + float(self.config.get("agents.max_seconds", 180) or 180)
        allowed = [step for step in plan.steps if spec.allows(step.tool)]
        skipped = [step for step in plan.steps if not spec.allows(step.tool)]
        if skipped:
            log.info("agent '%s' skipped %s step(s) outside its remit", spec.name, len(skipped))
        if not allowed:
            # Never report success for work that never happened.
            names = ", ".join(sorted({step.tool for step in skipped})) or "requested step"
            result.status = "failed"
            result.error = (
                f"the '{spec.name}' agent is not allowed to use {names}. "
                "Try rephrasing, or pick a different agent."
            )
            result.answer = result.error
            return

        result.status = "running"
        for offset, step in enumerate(allowed[:max_steps]):
            index = start_index + offset
            if state["cancel"].is_set():
                result.status = "cancelled"
                result.error = "cancelled by the user"
                break
            if time.time() > deadline:
                result.status = "timeout"
                result.error = "the task exceeded its time limit"
                break

            args = self._resolve_args(step.args, result.outcomes)
            self.bus.publish(
                "task_progress", task_id=result.task_id, step=index + 1,
                total=min(len(allowed), max_steps), tool=step.tool, why=step.why,
                args=args,
            )
            outcome = self.tools.call(step.tool, args, actor=spec.name, approval_id=approval_id)
            entry = StepOutcome(
                index=index + 1,
                tool=step.tool,
                args=args,
                ok=outcome.ok,
                data=_jsonable(outcome.data),
                error=outcome.error,
                verified=outcome.verified,
                requires_approval=outcome.requires_approval,
                duration_ms=outcome.duration_ms,
                why=step.why,
            )
            result.outcomes.append(entry.as_dict())
            self.bus.publish(
                "task_step", task_id=result.task_id, step=index + 1, tool=step.tool,
                ok=outcome.ok, error=outcome.error, requires_approval=outcome.requires_approval,
            )

            if outcome.requires_approval:
                result.status = "awaiting_approval"
                result.answer = (
                    f"This step needs your approval: {step.why or step.tool}. "
                    "Approve it in the RAY window and I will continue."
                )
                with self._lock:
                    self._tasks[result.task_id] = state
                self._pending_resume = getattr(self, "_pending_resume", {})
                self._pending_resume[result.task_id] = {
                    "plan": plan, "spec": spec, "index": index + 1, "text": result.text,
                }
                break
            if not outcome.ok:
                result.status = "failed"
                result.error = outcome.error
                break
        else:
            result.status = "completed"

        result.answer = result.answer or self._summarise(result)
        result.speak = plan.speak or _first_sentence(result.answer)

    # ------------------------------------------------------------------ resume
    def resume(self, task_id: str, approval_id: int) -> TaskResult:
        pending = getattr(self, "_pending_resume", {}).get(task_id)
        if not pending:
            return TaskResult(task_id=task_id, text="", status="error", error="no task is waiting for approval")
        result = TaskResult(task_id=task_id, text=pending["text"], status="running", agent=pending["spec"].name)
        state = {"cancel": threading.Event(), "started": time.time()}
        result.outcomes = []
        plan, spec = pending["plan"], pending["spec"]
        # re-run the pending step under the approval, then the rest
        remaining_plan = Plan(goal=plan.goal, steps=[], agent=plan.agent, speak=plan.speak)
        pending_index = pending["index"] - 1
        remaining_plan.steps = plan.steps[pending_index:]
        with self._lock:
            self._tasks[task_id] = state
        try:
            # The approved step is identified by its position in the original plan;
            # execute the remainder under the approval token.
            self._run_steps(result, remaining_plan, spec, state, approval_id=approval_id)
            if result.status == "completed":
                result.status = "completed"
            return self._finish(result)
        finally:
            with self._lock:
                self._tasks.pop(task_id, None)
            getattr(self, "_pending_resume", {}).pop(task_id, None)

    def pending_approval_task(self, task_id: str) -> dict[str, Any] | None:
        pending = getattr(self, "_pending_resume", {}).get(task_id)
        if not pending:
            return None
        return {"task_id": task_id, "next_step": pending["index"], "agent": pending["spec"].name}

    # --------------------------------------------------------------- control
    def cancel(self, task_id: str) -> bool:
        with self._lock:
            state = self._tasks.get(task_id)
        if not state:
            return False
        state["cancel"].set()
        self.bus.publish("task_cancelled", task_id=task_id)
        return True

    def chat(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
        agent: str = "assistant",
        engine: str | None = None,
    ) -> str:
        try:
            return self._chat_result(text, history=history, agent=agent, engine=engine)
        except ProviderError as exc:
            return (
                f"I could not reach an AI provider: {exc.message}\n\n"
                "Local commands still work - try \"open my documents folder\", \"check my system\", "
                "or \"remind me to stretch at 4pm\"."
            )

    def _chat_result(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
        agent: str = "assistant",
        engine: str | None = None,
    ) -> str:
        spec = self.agents.get(agent)
        context = ""
        if self.memory is not None:
            try:
                context = self.memory.context_block()
            except Exception:
                context = ""
        system = (
            f"{spec.system_prompt}\n\nYou are RAY, a personal AI operating system on the user's own "
            "computer. Keep answers concise and useful. Do not invent facts about the user's files or "
            "machine - if you do not know, say so and offer to check with a tool.\n\n"
            f"{context}"
        )
        messages = [
            ChatMessage(item["role"], str(item["content"])[:4000])
            for item in (history or [])[-8:]
            if item.get("role") in ("user", "assistant")
        ]
        messages.append(ChatMessage("user", text))
        result = self.router.chat(
            "chat", messages, system=system, temperature=0.6, max_tokens=900, provider=engine or None
        )
        self._last_engine = str(getattr(result, "provider", "") or "")
        return result.text or "(the model returned an empty reply)"

    # -------------------------------------------------------------- internals
    def _finish(self, result: TaskResult) -> TaskResult:
        result.finished_at = time.time()
        try:
            self.db.insert(
                "agent_runs",
                {
                    "agent": result.agent,
                    "goal": result.text[:400],
                    "status": result.status,
                    "started_at": utcnow(),
                    "finished_at": utcnow(),
                    "steps": len(result.outcomes),
                    "result": (result.answer or result.error)[:2000],
                },
            )
        except Exception:  # pragma: no cover
            pass
        self.bus.publish(
            "task_finished", task_id=result.task_id, status=result.status,
            agent=result.agent, steps=len(result.outcomes), duration_ms=result.duration_ms,
        )
        return result

    @staticmethod
    def _resolve_args(args: dict[str, Any], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        """Substitute ``$1.path`` / ``$prev.data.path`` references between steps."""

        def lookup(reference: str) -> Any:
            match = re.match(r"^(?:step)?(\d+|prev)\.(.+)$", reference)
            if not match:
                return reference
            token, path = match.groups()
            if not outcomes:
                return reference
            outcome = outcomes[-1] if token == "prev" else (
                outcomes[int(token) - 1] if 0 < int(token) <= len(outcomes) else None
            )
            if outcome is None:
                return reference
            value: Any = outcome.get("data")
            for part in path.split("."):
                if isinstance(value, dict):
                    value = value.get(part)
                elif isinstance(value, list):
                    try:
                        value = value[int(part)]
                    except (ValueError, IndexError):
                        return reference
                else:
                    return reference
            return value if value is not None else reference

        def walk(value: Any) -> Any:
            if isinstance(value, str) and value.startswith("$") and "." in value:
                return lookup(value[1:])
            if isinstance(value, str):
                return re.sub(
                    r"\$(\d+|prev)\.([A-Za-z0-9_.\[\]]+)",
                    lambda m: str(lookup(f"{m.group(1)}.{m.group(2)}")),
                    value,
                )
            if isinstance(value, dict):
                return {key: walk(item) for key, item in value.items()}
            if isinstance(value, list):
                return [walk(item) for item in value]
            return value

        return walk(args or {})

    def _summarise(self, result: TaskResult) -> str:
        if result.status == "failed" and not result.outcomes:
            return f"I could not do that: {result.error}"
        lines: list[str] = []
        for outcome in result.outcomes:
            lines.append(_describe_outcome(outcome))
        if result.status == "failed":
            lines.append(f"Stopped at step {len(result.outcomes)}: {result.error}")
        elif result.status == "cancelled":
            lines.append("Cancelled.")
        elif result.status == "timeout":
            lines.append("That took longer than the time limit, so I stopped.")
        return "\n".join(line for line in lines if line)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, default=str))


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)
    return parts[0][:240]


def _describe_outcome(outcome: dict[str, Any]) -> str:
    tool = outcome.get("tool", "")
    data = outcome.get("data")
    if not outcome.get("ok"):
        return f"{tool}: {outcome.get('error') or 'failed'}"
    verified = outcome.get("verified")
    suffix = ""
    if verified is True:
        suffix = " (verified)"
    elif verified is False:
        suffix = " (verification reported a problem)"

    if tool == "search_files" and isinstance(data, dict):
        results = data.get("results", [])
        if not results:
            return f"I found no matching files in {data.get('root', 'that folder')}."
        names = ", ".join(item["name"] for item in results[:5])
        return f"Found {data.get('count', len(results))} file(s): {names}{suffix}"
    if tool == "open_application" and isinstance(data, dict):
        return f"Opened {data.get('launched', 'the application')}{suffix}"
    if tool == "open_path" and isinstance(data, dict):
        return f"Opened {data.get('opened', 'the item')}{suffix}"
    if tool == "system_report":
        return str(data)
    if tool == "take_screenshot" and isinstance(data, dict):
        return f"Screenshot saved to {data.get('path')} ({data.get('width')}x{data.get('height')}){suffix}"
    if tool == "create_task" and isinstance(data, dict):
        return f"Task added: {data.get('title')}"
    if tool == "create_note" and isinstance(data, dict):
        return f"Note saved: {data.get('title')}"
    if tool == "create_reminder" and isinstance(data, dict):
        return f"Reminder set: {data.get('title')} at {str(data.get('due_at'))[:16].replace('T', ' ')}"
    if tool == "create_event" and isinstance(data, dict):
        return f"Event added: {data.get('title')} at {str(data.get('start_at'))[:16].replace('T', ' ')}"
    if tool == "list_tasks" and isinstance(data, list):
        if not data:
            return "You have no open tasks."
        return "Open tasks: " + "; ".join(
            f"{row['title']}" + (f" (due {str(row['due_at'])[:16].replace('T', ' ')})" if row.get("due_at") else "")
            for row in data[:8]
        )
    if tool == "list_reminders" and isinstance(data, list):
        if not data:
            return "No active reminders."
        return "Reminders: " + "; ".join(
            f"{row['title']} ({str(row['due_at'])[:16].replace('T', ' ')})" for row in data[:8]
        )
    if tool == "list_schedule" and isinstance(data, (list, dict)):
        items = data if isinstance(data, list) else data.get("events", data.get("days", []))
        return f"Your schedule has {len(items)} item(s)." if items else "Your schedule is clear."
    if tool == "web_search" and isinstance(data, dict):
        results = data.get("results", [])
        if not results:
            return "The search returned no results."
        return "Top results: " + "; ".join(
            f"{item['title']} ({item['url']})" for item in results[:4]
        )
    if tool == "create_backup" and isinstance(data, dict):
        return f"Backup written to {data.get('archive')} ({data.get('size_human')}){suffix}"
    if tool == "plan_downloads_organisation" and isinstance(data, dict):
        return f"I can tidy {data.get('total', 0)} file(s) in Downloads. Review the plan before applying it."
    if tool == "list_processes" and isinstance(data, list):
        if not data:
            return "No processes were reported."
        top = sorted(data, key=lambda row: float(row.get("cpu_percent") or 0), reverse=True)[:6]
        return "Busiest right now:\n" + "\n".join(
            f"• {row.get('name')} — {float(row.get('cpu_percent') or 0):.0f}% CPU, "
            f"{float(row.get('memory_mb') or 0):.0f} MB"
            for row in top
        )
    if tool == "list_running_apps" and isinstance(data, list):
        if not data:
            return "Nothing else is running."
        return "Open right now: " + ", ".join(
            f"{row.get('name')} ({float(row.get('memory_mb') or 0):.0f} MB)" for row in data[:8]
        )
    if tool == "index_status" and isinstance(data, dict):
        last = str(data.get("last_indexed") or "")[:16].replace("T", " ") or "never"
        return (
            f"Index: {data.get('files', 0)} file(s), {data.get('size_human', '0 B')}, "
            f"last indexed {last}."
        )
    if tool == "list_bookmarks" and isinstance(data, list):
        if not data:
            return "No bookmarks saved yet."
        return "Bookmarks: " + "; ".join(str(row.get("title") or row.get("url")) for row in data[:8])
    if tool == "recall_memory" and isinstance(data, list):
        if not data:
            return "I don't have anything stored about that yet."
        return "I remember: " + "; ".join(
            str(row.get("content") or row.get("title")) for row in data[:6]
        )
    if tool == "clipboard_get" and isinstance(data, dict):
        text = str(data.get("text") or "").strip()
        return f"Clipboard: {text[:500]}" if text else "The clipboard is empty."
    if tool == "close_application" and isinstance(data, dict):
        return f"Closed {data.get('closed') or data.get('name') or 'the application'}{suffix}"
    if tool == "open_known_folder" and isinstance(data, dict):
        return f"Opened {data.get('opened') or data.get('folder')}{suffix}"
    if tool == "open_file_by_name" and isinstance(data, dict):
        return f"Opened {data.get('opened') or data.get('name')}{suffix}"
    if tool in {"open_url", "open_path"} and isinstance(data, dict):
        return f"Opened {data.get('opened') or data.get('url')}{suffix}"
    if tool == "start_focus" and isinstance(data, dict):
        return f"Focus session started for {data.get('minutes', 25)} minutes."
    if tool == "type_text" and isinstance(data, dict):
        return "Typed that into the active window."
    if tool == "press_hotkey" and isinstance(data, dict):
        return f"Pressed {'+'.join(str(key) for key in (data.get('keys') or [])) or 'the keys'}."
    if tool == "remember":
        return "I'll remember that."
    if tool == "play_media" and isinstance(data, dict):
        return f"Playing {data.get('title') or data.get('path')}"
    if tool in {"change_volume", "set_volume", "set_brightness", "power_action"} and isinstance(data, dict):
        return f"{tool.replace('_', ' ').capitalize()}: {json.dumps(data)[:160]}"
    if isinstance(data, str):
        return data[:800]
    if data is None:
        return f"{ tool.replace('_', ' ').capitalize()} done{suffix}"
    return f"{tool.replace('_', ' ').capitalize()}: {json.dumps(data, default=str)[:300]}{suffix}"
