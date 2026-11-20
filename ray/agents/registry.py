"""The twelve specialised agents.

Agents are not separate chatbots. They are roles layered over one shared
engine: the same tool registry, permissions, memory, task state and audit log.
An agent mainly (a) contributes a system prompt, (b) narrows which tools it may
use, and (c) carries its own limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentSpec:
    name: str
    title: str
    description: str
    system_prompt: str
    tool_prefixes: tuple[str, ...]
    keywords: tuple[str, ...] = ()
    max_steps: int = 12

    def allows(self, tool: str) -> bool:
        if not self.tool_prefixes:
            return True
        return any(tool == prefix or tool.startswith(prefix) for prefix in self.tool_prefixes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "tools": list(self.tool_prefixes),
            "max_steps": self.max_steps,
            "keywords": list(self.keywords),
        }


AGENTS: dict[str, AgentSpec] = {
    "assistant": AgentSpec(
        name="assistant",
        title="Personal Assistant",
        description="Everyday instructions, reminders, small talk and general coordination.",
        system_prompt=(
            "You are RAY's personal assistant. Be direct, warm and brief. You are running on the "
            "user's own computer and can act through tools, so prefer action over describing action."
        ),
        tool_prefixes=(),
        keywords=("remind", "note", "remember", "task", "hello", "hi"),
        max_steps=8,
    ),
    "research": AgentSpec(
        name="research",
        title="Research Agent",
        description="Web search, source gathering, comparison and research reports.",
        system_prompt=(
            "You are RAY's research agent. Search, read the actual pages, then summarise with "
            "citations to the URLs you read. Never present an unsourced claim as verified."
        ),
        tool_prefixes=("web_search", "fetch_page", "research", "open_url", "add_bookmark", "list_bookmarks", "summarise"),
        keywords=("search", "research", "look up", "compare", "sources", "news"),
        max_steps=10,
    ),
    "files": AgentSpec(
        name="files",
        title="File Agent",
        description="Find, read, organise, back up and clean up local files.",
        system_prompt=(
            "You are RAY's file agent. Work with real paths on this machine. Confirm before "
            "destructive operations, and prefer moving to quarantine over permanent deletion."
        ),
        tool_prefixes=(
            "search_files", "list_directory", "file_info", "read_text_file", "create_folder",
            "rename_path", "copy_path", "move_path", "delete_path", "find_duplicates",
            "find_large_files", "plan_downloads_organisation", "apply_organisation",
            "backup_path", "open_file_by_name", "open_known_folder", "open_path",
            "search_index", "index_files", "index_status", "extract_document",
        ),
        keywords=("file", "folder", "download", "organise", "organize", "duplicate", "backup", "delete"),
        max_steps=15,
    ),
    "productivity": AgentSpec(
        name="productivity",
        title="Productivity Agent",
        description="Tasks, calendar, reminders, notes, planner and focus sessions.",
        system_prompt=(
            "You are RAY's productivity agent. Keep the user's system of record tidy: tasks with "
            "real deadlines, events with real times, notes they can find again."
        ),
        tool_prefixes=("create_task", "list_tasks", "update_task", "complete_task", "delete_task",
                       "create_note", "list_notes", "update_note", "delete_note",
                       "create_reminder", "list_reminders", "delete_reminder", "snooze_reminder",
                       "create_event", "list_schedule", "delete_event", "habit", "focus"),
        keywords=("task", "todo", "remind", "calendar", "schedule", "note", "event", "deadline"),
        max_steps=10,
    ),
    "computer": AgentSpec(
        name="computer",
        title="Computer Control Agent",
        description="Applications, windows, volume, brightness, screenshots and power actions.",
        system_prompt=(
            "You are RAY's computer control agent. Prefer OS APIs over blind screen coordinates. "
            "Power actions and anything irreversible need explicit approval."
        ),
        tool_prefixes=("open_application", "close_application", "list_installed_apps", "list_running_apps",
                       "open_known_folder", "open_path", "take_screenshot", "change_volume", "set_volume",
                       "set_brightness", "power_action", "open_system_panel", "type_text",
                       "press_hotkey", "clipboard"),
        keywords=("open", "launch", "close", "volume", "brightness", "screenshot", "lock", "shutdown", "restart"),
        max_steps=10,
    ),
    "coding": AgentSpec(
        name="coding",
        title="Coding Agent",
        description="Programming help, project files, git status and controlled script runs.",
        system_prompt=(
            "You are RAY's coding agent. Read before you write, back up before you change, and never "
            "silently overwrite a project file. Explain the change in one or two lines."
        ),
        tool_prefixes=("search_files", "list_directory", "read_text_file", "extract_document", "file_info",
                       "backup_path", "run_command", "project", "git_status", "search_code"),
        keywords=("code", "python", "script", "bug", "function", "git", "project", "compile"),
        max_steps=12,
    ),
    "documents": AgentSpec(
        name="documents",
        title="Document Agent",
        description="Read, summarise, compare and answer questions about documents.",
        system_prompt=(
            "You are RAY's document agent. Quote from the real extracted text. If the document has no "
            "readable text (scanned PDF), say so instead of guessing."
        ),
        tool_prefixes=("extract_document", "summarise_document", "ask_document", "search_index",
                       "search_files", "read_text_file", "file_info", "index_files", "index_status"),
        keywords=("document", "pdf", "docx", "summarise", "summarize", "summarise", "read this", "compare"),
        max_steps=8,
    ),
    "media": AgentSpec(
        name="media",
        title="Media Agent",
        description="Local music and video libraries, playlists and playback.",
        system_prompt=(
            "You are RAY's media agent. Work with the files actually present on this machine; "
            "never claim to play something that is not there."
        ),
        tool_prefixes=("play_media", "search_media", "list_playlists", "media_library", "open_path", "open_url"),
        keywords=("play", "music", "song", "playlist", "album", "video", "revx"),
        max_steps=6,
    ),
    "automation": AgentSpec(
        name="automation",
        title="Automation Agent",
        description="Build, test, enable and run workflows from natural language.",
        system_prompt=(
            "You are RAY's automation agent. Workflows are trigger + conditions + actions. Never make "
            "a destructive action automatic; require approval for anything level 2 or 3."
        ),
        tool_prefixes=("list_workflows", "create_workflow", "update_workflow", "run_workflow",
                       "workflow_history", "delete_workflow"),
        keywords=("automate", "automation", "workflow", "every day", "whenever", "when a file"),
        max_steps=6,
    ),
    "system": AgentSpec(
        name="system",
        title="System Agent",
        description="Performance, storage, processes and diagnostics.",
        system_prompt=(
            "You are RAY's system agent. Report only real measured values. If a sensor is not "
            "available, say it is not available."
        ),
        tool_prefixes=("system_report", "system_snapshot", "list_processes", "find_large_files",
                       "disk", "battery", "network"),
        keywords=("performance", "cpu", "ram", "memory", "disk", "battery", "slow", "process"),
        max_steps=6,
    ),
    "study": AgentSpec(
        name="study",
        title="Study Agent",
        description="Explain material, build revision plans and quiz the user.",
        system_prompt=(
            "You are RAY's study agent. Teach in short steps, use the user's own material when it is "
            "available, and be honest about uncertainty."
        ),
        tool_prefixes=("extract_document", "ask_document", "summarise_document", "search_files",
                       "create_task", "create_event", "create_note", "list_schedule", "web_search"),
        keywords=("study", "exam", "revision", "explain", "learn", "teach", "quiz"),
        max_steps=8,
    ),
    "release": AgentSpec(
        name="release",
        title="Release Management Agent",
        description="Music release metadata, file checks and publishing checklists.",
        system_prompt=(
            "You are RAY's release agent for music projects like REVX. Check files and metadata, "
            "build a clear checklist, and never publish anything without explicit approval."
        ),
        tool_prefixes=("search_files", "list_directory", "file_info", "extract_document", "create_note",
                       "create_task", "backup_path", "read_text_file", "summarise_document"),
        keywords=("release", "publish", "distribute", "metadata", "album", "revx", "cover art"),
        max_steps=10,
    ),
}


class AgentRegistry:
    def __init__(self) -> None:
        self.agents = AGENTS

    def get(self, name: str | None) -> AgentSpec:
        if name and name in self.agents:
            return self.agents[name]
        return self.agents["assistant"]

    def list(self) -> list[dict[str, Any]]:
        return [agent.as_dict() for agent in self.agents.values()]

    def select(self, text: str, suggested: str | None = None) -> AgentSpec:
        """Prefer an explicit suggestion, else score keywords in the request."""
        if suggested and suggested in self.agents:
            return self.agents[suggested]
        lowered = (text or "").lower()
        scores: dict[str, int] = {}
        for name, agent in self.agents.items():
            score = sum(1 for keyword in agent.keywords if keyword in lowered)
            if score:
                scores[name] = score
        if not scores:
            return self.agents["assistant"]
        best = max(scores.items(), key=lambda item: item[1])
        return self.agents[best[0]]
