"""Turn a request into an executable plan.

Two layers, in this order:

1. **Local intents** - the deterministic parser handles the commands people use
   most. These run instantly, work offline, and cost nothing.
2. **AI planning** - anything else goes to the engine the policy picks
   (OpenRouter or Gemini, see ray/ai/policy.py) with the real tool schemas. The model returns a JSON plan which is validated tool-by-tool: an
   unknown tool or a malformed argument aborts the plan instead of being
   "helpfully" guessed at.

A plan is data, not code. Nothing runs until the orchestrator executes it
through the permission-gated registry.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..agents.registry import AgentRegistry
from ..ai.base import ChatMessage, ProviderError
from ..core.logging_setup import get_logger
from ..tools.registry import ToolRegistry
from .intent import Intent, IntentParser, parse_when

log = get_logger("brain.planner")


@dataclass
class PlanStep:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    why: str = ""
    optional: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": self.args, "why": self.why, "optional": self.optional}


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    agent: str = "assistant"
    speak: str = ""
    source: str = "local"
    confidence: float = 0.0
    notes: str = ""
    answer: str = ""  # for conversational replies with no tools
    engine: str = ""  # which engine produced it ("" for local, offline plans)

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "agent": self.agent,
            "speak": self.speak,
            "source": self.source,
            "engine": self.engine,
            "confidence": self.confidence,
            "notes": self.notes,
            "answer": self.answer,
            "steps": [step.as_dict() for step in self.steps],
        }


INTENT_PLANS: dict[str, tuple[str, str, str]] = {
    # intent kind: (tool, agent, description)
    "open_app": ("open_application", "computer", "Open the requested application"),
    "open_folder": ("open_known_folder", "files", "Open the requested folder"),
    "open_file": ("open_file_by_name", "files", "Find and open the requested file"),
    "open_url": ("open_url", "research", "Open the address in the browser"),
    "search_files": ("search_files", "files", "Search local files"),
    "create_folder": ("create_folder_by_name", "files", "Create a new folder"),
    "plan_organisation": ("plan_downloads_organisation", "files", "Plan a tidy-up of Downloads"),
    "backup": ("backup_path", "files", "Create a verified archive"),
    "delete_path": ("delete_path", "files", "Delete the requested path"),
    "find_duplicates": ("find_duplicates", "files", "Find duplicate files"),
    "find_large_files": ("find_large_files", "files", "Find the largest files"),
    "system_report": ("system_report", "system", "Read real system metrics"),
    "screenshot": ("take_screenshot", "computer", "Capture the screen"),
    "volume": ("change_volume", "computer", "Adjust the system volume"),
    "set_volume": ("set_volume", "computer", "Set an exact volume level"),
    "set_brightness": ("set_brightness", "computer", "Adjust screen brightness"),
    "create_reminder": ("create_reminder", "productivity", "Schedule the reminder"),
    "create_event": ("create_event", "productivity", "Add the calendar event"),
    "create_task": ("create_task", "productivity", "Add the task"),
    "create_note": ("create_note", "productivity", "Save the note"),
    "remember": ("remember", "assistant", "Store the memory"),
    "web_search": ("web_search", "research", "Search the web"),
    "play_media": ("play_media", "media", "Play the requested media"),
    "list_tasks": ("list_tasks", "productivity", "Read the task list"),
    "list_reminders": ("list_reminders", "productivity", "Read reminders"),
    "list_schedule": ("list_schedule", "productivity", "Read the calendar"),
    "close_app": ("close_application", "computer", "Close the requested application"),
    "open_panel": ("open_system_panel", "computer", "Open the settings page"),
    "clipboard_get": ("clipboard_get", "computer", "Read the clipboard"),
    "clipboard_set": ("clipboard_set", "computer", "Put text on the clipboard"),
    "type_text": ("type_text", "computer", "Type into the active window"),
    "press_hotkey": ("press_hotkey", "computer", "Press a keyboard shortcut"),
    "index_files": ("index_files", "files", "Index the chosen folders"),
    "index_status": ("index_status", "files", "Report the index state"),
    "list_processes": ("list_processes", "system", "Read the busiest processes"),
    "list_running_apps": ("list_running_apps", "computer", "List the open applications"),
    "start_focus": ("start_focus", "productivity", "Start a focus session"),
    "recall": ("recall_memory", "assistant", "Search long-term memory"),
    "research": ("research", "research", "Research the question with sources"),
    "list_bookmarks": ("list_bookmarks", "research", "Read saved bookmarks"),
}

# Intents that answer in words instead of running a tool.
ANSWER_INTENTS = {"capabilities", "smalltalk", "explain"}

# Local intents use friendly argument names; tools use their own schema.
ARG_ALIASES: dict[str, dict[str, str]] = {
    "open_app": {"app": "target"},
}

POWER_TOOLS = {"lock": "power_action", "sleep": "power_action", "restart": "power_action", "shutdown": "power_action"}

PLANNER_SYSTEM = """You are the planning component of RAY, a personal AI operating system running on the user's own Windows PC.

Convert the user's request into a JSON plan that uses ONLY the tools supplied. Rules:
- Reply with a single JSON object and nothing else.
- Use the smallest number of steps that really accomplishes the goal.
- Never invent tool names or arguments that are not in the schema.
- Never plan a destructive action unless the user explicitly asked for it.
- If the request is a question or conversation that needs no tools, return {"steps": [], "answer": "<your reply>"}.
- If a piece of information is missing and guessing would be wrong, return {"steps": [], "answer": "<a short clarifying question>"}.
- Put a short spoken-style sentence in "speak" that summarises what you are about to do.
- Set "agent" to one of: assistant, research, files, productivity, computer, coding, documents, media, automation, system, study, release.

Schema:
{"agent": "assistant", "speak": "one short sentence", "steps": [{"tool": "<tool name>", "args": {...}, "why": "short reason"}]}
"""


class Planner:
    def __init__(self, router, tools: ToolRegistry, memory=None, config=None) -> None:
        self.router = router
        self.tools = tools
        self.memory = memory
        self.config = config
        self.intents = IntentParser()

    # ------------------------------------------------------------------ public
    def plan(
        self,
        text: str,
        agent: str | None = None,
        history: list[dict[str, str]] | None = None,
        engine: str | None = None,
    ) -> Plan:
        text = (text or "").strip()
        if not text:
            return Plan(goal="", answer="I did not catch a request.")
        local = self.plan_local(text)
        if local is not None:
            # The agent a caller asks for is a hint, not a cage: typing "open
            # chrome" while the System tab is selected must still open Chrome.
            if agent and self._agent_can_run(agent, local):
                local.agent = agent
            return local
        return self.plan_with_ai(text, agent=agent, history=history, engine=engine)

    @staticmethod
    def _agent_can_run(agent: str, plan: Plan) -> bool:
        registry = AgentRegistry()
        spec = registry.agents.get(agent)
        if spec is None:
            return False
        return all(spec.allows(step.tool) for step in plan.steps)

    # ------------------------------------------------------------ local layer
    def plan_local(self, text: str) -> Plan | None:
        intent = self.intents.parse(text)
        if intent is None:
            return None
        if intent.kind == "time":
            from datetime import datetime

            now = datetime.now()
            return Plan(
                goal="tell the time",
                answer=f"It is {now.strftime('%H:%M')} on {now.strftime('%A, %d %B %Y')}.",
                speak=now.strftime("It's %H:%M."),
                source="local",
                confidence=1.0,
            )
        if intent.kind == "backup_prompt":
            return Plan(goal="clarify backup target", answer=intent.speak, source="local", confidence=0.6)
        if intent.kind in ANSWER_INTENTS:
            answer = str(intent.args.get("answer") or intent.speak)
            return Plan(
                goal=intent.kind, answer=answer, speak=intent.speak,
                source="local", confidence=intent.confidence, notes=intent.evidence,
            )

        if intent.kind.startswith("power_"):
            action = intent.args.get("action", intent.kind.split("_", 1)[1])
            tool_name = "power_off" if action in ("restart", "shutdown") else "power_action"
            step = PlanStep(tool_name, {"action": action}, intent.speak)
            return Plan(
                goal=f"{action} the computer", steps=[step], agent="computer",
                speak=intent.speak, source="local", confidence=intent.confidence,
            )

        mapping = INTENT_PLANS.get(intent.kind)
        if not mapping:
            return None
        tool, agent_name, reason = mapping
        if tool not in self.tools:
            log.warning("intent '%s' maps to missing tool '%s'", intent.kind, tool)
            return None
        aliases = ARG_ALIASES.get(intent.kind, {})
        args = {
            aliases.get(key, key): value
            for key, value in intent.args.items()
            if value is not None
        }
        required = (self.tools.get(tool).parameters or {}).get("required", []) if self.tools.get(tool) else []
        missing = [name for name in required if name not in args]
        if missing:
            # A parser/tool schema mismatch is a bug, not something to guess at.
            log.warning("intent '%s' is missing %s for tool '%s'", intent.kind, missing, tool)
            return None
        step = PlanStep(tool, args, reason)
        return Plan(
            goal=reason,
            steps=[step],
            agent=agent_name,
            speak=intent.speak,
            source="local",
            confidence=intent.confidence,
            notes=intent.evidence,
        )

    # --------------------------------------------------------------- AI layer
    def plan_with_ai(
        self,
        text: str,
        agent: str | None = None,
        history: list[dict[str, str]] | None = None,
        engine: str | None = None,
    ) -> Plan:
        context = self._context_block()
        messages: list[ChatMessage] = []
        for item in (history or [])[-6:]:
            role = item.get("role", "user")
            if role in ("user", "assistant") and item.get("content"):
                messages.append(ChatMessage(role, str(item["content"])[:2000]))
        messages.append(
            ChatMessage(
                "user",
                f"{context}\n\nCurrent date and time: {__import__('datetime').datetime.now().strftime('%A %d %B %Y, %H:%M')}\n"
                f"User request: {text}",
            )
        )
        schemas = self.tools.openai_schemas()
        try:
            result = self.router.chat(
                "planning",
                messages,
                system=PLANNER_SYSTEM,
                tools=schemas,
                temperature=0.2,
                max_tokens=1200,
                provider=engine or None,
            )
        except ProviderError as exc:
            return Plan(
                goal=text,
                agent=agent or "assistant",
                source="offline",
                answer=self._offline_message(exc),
                notes=exc.message,
            )

        plan = self._parse_plan(result.text, text, agent)
        plan.engine = str(getattr(result, "provider", "") or "")
        if result.tool_calls and not plan.steps:
            steps = [
                PlanStep(call.name, call.arguments, "from provider tool call")
                for call in result.tool_calls
                if call.name in self.tools
            ]
            if steps:
                plan.steps = steps
                plan.source = "ai-tools"
        return plan

    def _max_steps(self) -> int:
        try:
            return max(1, min(25, int(self.config.get("brain.max_steps", 8) or 8)))
        except (TypeError, ValueError):
            return 8

    def _parse_plan(self, raw: str, text: str, agent: str | None) -> Plan:
        payload = _extract_json(raw)
        if payload is None:
            # The model replied in prose: treat it as a conversational answer rather
            # than pretending it was a plan.
            return Plan(
                goal=text, agent=agent or "assistant", answer=raw.strip()[:4000],
                source="ai-chat", confidence=0.5,
            )

        steps: list[PlanStep] = []
        rejected: list[str] = []
        for item in payload.get("steps", []) or []:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool", "")).strip()
            args = item.get("args") or {}
            if tool not in self.tools:
                rejected.append(f"unknown tool '{tool}'")
                continue
            if not isinstance(args, dict):
                rejected.append(f"bad args for '{tool}'")
                continue
            steps.append(PlanStep(tool, args, str(item.get("why", ""))[:300]))
            if len(steps) >= self._max_steps():
                rejected.append(f"step limit of {self._max_steps()} reached")
                break
        return Plan(
            goal=text,
            steps=steps,
            agent=str(payload.get("agent") or agent or "assistant"),
            speak=str(payload.get("speak", ""))[:400],
            source="ai",
            confidence=0.8 if steps else 0.4,
            notes="; ".join(rejected) if rejected else str(payload.get("notes", ""))[:300],
            answer=str(payload.get("answer", ""))[:4000],
        )

    # ---------------------------------------------------------------- helpers
    def _context_block(self) -> str:
        parts: list[str] = []
        if self.memory is not None:
            try:
                block = self.memory.context_block()
                if block:
                    parts.append(block)
            except Exception:
                pass
        parts.append(
            "Tools you can call are supplied as function schemas. Use real tool names only. "
            "All file system access happens on this computer."
        )
        return "\n\n".join(parts)

    def _offline_message(self, exc: ProviderError) -> str:
        return (
            f"I can't reach an AI provider right now ({exc.message}).\n\n"
            "I can still do the local things: open apps and folders, find and organise files, "
            "take notes, manage tasks, reminders and your calendar, check system health, take "
            "screenshots and run automations. Just phrase it directly, for example "
            "\"open my documents folder\" or \"remind me to call home at 7pm\". "
            "Say \"help\" to see everything I understand."
        )


def _extract_json(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            payload = json.loads(text[start:end + 1])
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None
    return None
