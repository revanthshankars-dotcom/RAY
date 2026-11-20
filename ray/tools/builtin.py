"""Concrete tools: the real capabilities behind every RAY button and command.

Each handler is a thin, honest wrapper around a service. Failures raise with a
message a person can act on; success returns real data from the OS or database.
"""

from __future__ import annotations

import os
import platform
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..ai.base import ChatMessage, ProviderError
from ..core.logging_setup import get_logger
from ..knowledge.extract import chunk_text, extract, summarise_locally
from ..security.permissions import LEVEL_APPROVE, LEVEL_AUTO, LEVEL_CONFIRM
from ..tools.registry import Tool, ToolRegistry

log = get_logger("tools.builtin")


def _par(kind: str, description: str, **extra: Any) -> dict[str, Any]:
    payload = {"type": kind, "description": description}
    payload.update(extra)
    return payload


def _obj(**properties: Any) -> dict[str, Any]:
    required = [name for name, spec in properties.items() if spec.pop("_required", False)]
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def known_folder(name: str, config=None) -> Path:
    """Resolve a friendly folder name to a real path on this machine."""
    name = (name or "").strip().lower().rstrip("/\\")
    home = Path.home()
    aliases = {
        "documents": "Documents", "document": "Documents", "docs": "Documents",
        "downloads": "Downloads", "download": "Downloads",
        "desktop": "Desktop", "pictures": "Pictures", "photos": "Pictures",
        "music": "Music", "videos": "Videos", "movies": "Videos",
        "home": "", "projects": "Projects", "ray": None,
    }
    overrides = {}
    if config is not None:
        try:
            overrides = config.section("paths")
        except Exception:
            overrides = {}
    if name in overrides and overrides[name]:
        candidate = Path(os.path.expanduser(str(overrides[name])))
        if candidate.exists():
            return candidate
    if name == "ray":
        from ..core.paths import paths

        return paths().home
    if name in aliases:
        relative = aliases[name]
        candidate = home / relative if relative else home
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"the {name} folder does not exist at {candidate}")
    candidate = Path(os.path.expanduser(name))
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(f"'{name}' is not a folder RAY recognises")


def register_tools(registry: ToolRegistry, ctx) -> ToolRegistry:
    """Attach every tool to the registry. ``ctx`` is the application context."""
    add = registry.register

    # ================================================================ system
    add(Tool(
        name="system_report",
        description="Read real CPU, memory, disk, network, battery and uptime data and summarise it.",
        handler=lambda: ctx.sysmon.report(),
        category="system", level=LEVEL_AUTO,
        parameters=_obj(),
    ))
    add(Tool(
        name="system_snapshot",
        description="Get the raw system metrics snapshot (CPU, RAM, disks, network, battery, temperatures).",
        handler=lambda: ctx.sysmon.snapshot(),
        category="system", level=LEVEL_AUTO, parameters=_obj(),
    ))
    add(Tool(
        name="list_processes",
        description="List running processes with real CPU and memory usage.",
        handler=lambda limit=12, sort_by="cpu": ctx.sysmon.processes(limit=limit, sort_by=sort_by),
        category="system", level=LEVEL_AUTO,
        parameters=_obj(
            limit=_par("integer", "How many processes to return (1-100)"),
            sort_by=_par("string", "Sort by 'cpu' or 'memory'", enum=["cpu", "memory"]),
        ),
    ))

    # ============================================================== computer
    add(Tool(
        name="open_application",
        description="Open an installed application by name, or a file/folder/URL path.",
        handler=lambda target, args=None: ctx.apps.launch(target, args=args),
        category="computer", level=LEVEL_AUTO,
        parameters=_obj(
            target=_par("string", "Application name, executable path, folder, file or URL", _required=True),
            args=_par("array", "Optional list of command-line arguments"),
        ),
        verify=lambda data: (bool(data.get("launched")), "process start requested"),
        examples=["open chrome", "launch visual studio code"],
    ))
    add(Tool(
        name="close_application",
        description="Ask a running application to close (by process name).",
        handler=lambda name, force=False: ctx.apps.close(name, force=force),
        category="computer", level=LEVEL_CONFIRM, destructive=True,
        parameters=_obj(
            name=_par("string", "Process/application name to close", _required=True),
            force=_par("boolean", "Force-kill instead of asking politely"),
        ),
    ))
    add(Tool(
        name="list_installed_apps",
        description="List applications actually installed on this machine.",
        handler=lambda query="", limit=200: [
            app for app in ctx.apps.list_apps()
            if not query or query.lower() in app["name"].lower()
        ][:limit],
        category="computer", level=LEVEL_AUTO,
        parameters=_obj(
            query=_par("string", "Optional name filter"),
            limit=_par("integer", "Maximum entries"),
        ),
    ))
    add(Tool(
        name="list_running_apps",
        description="List currently running applications with memory usage.",
        handler=lambda limit=20: ctx.apps.running()[:limit],
        category="computer", level=LEVEL_AUTO,
        parameters=_obj(limit=_par("integer", "Maximum entries")),
    ))
    add(Tool(
        name="open_known_folder",
        description="Open a well-known folder such as Documents, Downloads, Desktop, Pictures, Music.",
        handler=lambda folder="documents": ctx.apps.open_path(str(known_folder(folder, ctx.config))),
        category="computer", level=LEVEL_AUTO,
        parameters=_obj(folder=_par("string", "Folder name", _required=True)),
        verify=lambda data: (bool(data.get("opened")), "opened with the OS handler"),
    ))
    add(Tool(
        name="open_path",
        description="Open any existing file or folder with the default application.",
        handler=lambda path: ctx.apps.open_path(path),
        category="computer", level=LEVEL_AUTO,
        parameters=_obj(path=_par("string", "File or folder path", _required=True)),
    ))
    add(Tool(
        name="open_file_by_name",
        description="Search for a file by name and open the most recent match.",
        handler=_open_file_by_name(ctx),
        category="computer", level=LEVEL_AUTO,
        parameters=_obj(
            name=_par("string", "File name or part of it", _required=True),
            open_it=_par("boolean", "Open the file (default true)"),
        ),
    ))
    add(Tool(
        name="take_screenshot",
        description="Capture the screen (or a region) to a PNG file in the RAY screenshots folder.",
        handler=lambda region=None, filename=None: ctx.controls.screenshot(
            region=tuple(region) if region else None, filename=filename
        ),
        category="computer", level=LEVEL_AUTO,
        parameters=_obj(
            region=_par("array", "Optional [left, top, width, height]"),
            filename=_par("string", "Optional file name"),
        ),
        verify=lambda data: (Path(data["path"]).exists(), "screenshot file exists"),
    ))
    add(Tool(
        name="change_volume",
        description="Turn the system volume up, down or mute/unmute using the OS media keys.",
        handler=lambda direction="up", steps=2: ctx.controls.volume_step(direction, steps),
        category="computer", level=LEVEL_AUTO,
        parameters=_obj(
            direction=_par("string", "up, down or mute", enum=["up", "down", "mute"], _required=True),
            steps=_par("integer", "How many steps (1-20)"),
        ),
    ))
    add(Tool(
        name="set_volume",
        description="Set an exact system volume percentage.",
        handler=lambda percent: ctx.controls.set_volume(percent),
        category="computer", level=LEVEL_AUTO,
        parameters=_obj(percent=_par("integer", "Volume 0-100", _required=True)),
    ))
    add(Tool(
        name="set_brightness",
        description="Set the screen brightness percentage (internal laptop panels only).",
        handler=lambda percent: ctx.controls.set_brightness(percent),
        category="computer", level=LEVEL_AUTO,
        parameters=_obj(percent=_par("integer", "Brightness 0-100", _required=True)),
    ))
    add(Tool(
        name="power_action",
        description="Lock the screen or put the computer to sleep.",
        handler=lambda action: ctx.power(action),
        category="computer", level=LEVEL_CONFIRM,
        parameters=_obj(
            action=_par("string", "lock or sleep", enum=["lock", "sleep"], _required=True),
        ),
    ))
    add(Tool(
        name="power_off",
        description="Restart or shut down the computer. Always requires explicit approval.",
        handler=lambda action, delay_seconds=5: ctx.power(action, delay_seconds),
        category="computer", level=LEVEL_APPROVE, destructive=True,
        parameters=_obj(
            action=_par("string", "restart or shutdown", enum=["restart", "shutdown"], _required=True),
            delay_seconds=_par("integer", "Delay before the action"),
        ),
    ))
    add(Tool(
        name="open_system_panel",
        description="Open a Windows settings page, Task Manager or a terminal.",
        handler=lambda panel: ctx.controls.open_panel(panel),
        category="computer", level=LEVEL_CONFIRM,
        parameters=_obj(panel=_par("string", "Panel name, e.g. settings, task_manager, terminal, display", _required=True)),
    ))
    add(Tool(
        name="type_text",
        description="Type text into the currently focused window using synthetic keyboard input.",
        handler=lambda text, interval=0.01: ctx.input.type_text(text, interval),
        category="computer", level=LEVEL_APPROVE, destructive=True,
        parameters=_obj(
            text=_par("string", "Text to type", _required=True),
            interval=_par("number", "Delay between keystrokes in seconds"),
        ),
    ))
    add(Tool(
        name="press_hotkey",
        description="Press a keyboard shortcut such as ctrl+s or alt+tab.",
        handler=lambda keys: ctx.input.hotkey(*keys),
        category="computer", level=LEVEL_APPROVE, destructive=True,
        parameters=_obj(keys=_par("array", "Keys to press together, e.g. ['ctrl','s']", _required=True)),
    ))
    add(Tool(
        name="clipboard_get",
        description="Read the current clipboard text.",
        handler=lambda: {"text": ctx.controls.clipboard_get()[:4000]},
        category="computer", level=LEVEL_CONFIRM, parameters=_obj(),
    ))
    add(Tool(
        name="clipboard_set",
        description="Put text on the clipboard.",
        handler=lambda text: ctx.controls.clipboard_set(text),
        category="computer", level=LEVEL_CONFIRM,
        parameters=_obj(text=_par("string", "Text to copy", _required=True)),
    ))
    add(Tool(
        name="run_command",
        description="Run an allow-listed command with no shell and a hard timeout.",
        handler=lambda command, cwd=None, timeout=30: ctx.shell.run(command, cwd=cwd, timeout=timeout),
        category="computer", level=LEVEL_APPROVE, destructive=True,
        parameters=_obj(
            command=_par("array", "Command as an argument list, e.g. ['git','status']", _required=True),
            cwd=_par("string", "Working directory"),
            timeout=_par("integer", "Timeout in seconds"),
        ),
    ))

    # ================================================================= files
    add(Tool(
        name="search_files",
        description="Search local files by name, extension, type, size or modification date.",
        handler=lambda query="", root=None, extension=None, kind=None, limit=30, modified_within_days=None: ctx.files.search(
            query=query,
            root=_resolve_search_root(ctx, root),
            extension=extension,
            kind=kind,
            limit=limit,
            modified_within_days=modified_within_days,
            excluded_names=ctx.config.get("indexing.excluded_names", []),
        ),
        category="files", level=LEVEL_AUTO,
        parameters=_obj(
            query=_par("string", "Part of the file name"),
            root=_par("string", "Folder to search in (defaults to your home folder)"),
            extension=_par("string", "Extension like pdf or .docx"),
            kind=_par("string", "document, image, audio, video, spreadsheet, code, archive"),
            limit=_par("integer", "Maximum results"),
            modified_within_days=_par("integer", "Only files modified in the last N days"),
        ),
        examples=["find the latest PDF I downloaded"],
    ))
    add(Tool(
        name="list_directory",
        description="List the contents of a folder.",
        handler=lambda path, limit=300: ctx.files.list_dir(path, limit=limit),
        category="files", level=LEVEL_AUTO,
        parameters=_obj(
            path=_par("string", "Folder path", _required=True),
            limit=_par("integer", "Maximum entries"),
        ),
    ))
    add(Tool(
        name="file_info",
        description="Get size, timestamps, type and permissions for a path.",
        handler=lambda path: ctx.files.info(path),
        category="files", level=LEVEL_AUTO,
        parameters=_obj(path=_par("string", "File or folder path", _required=True)),
    ))
    add(Tool(
        name="read_text_file",
        description="Read the beginning of a text-based file.",
        handler=lambda path, max_chars=4000: ctx.files.read_text(path, max_chars=max_chars),
        category="files", level=LEVEL_AUTO,
        parameters=_obj(
            path=_par("string", "File path", _required=True),
            max_chars=_par("integer", "How many characters to return"),
        ),
    ))
    add(Tool(
        name="create_folder",
        description="Create a new folder at an absolute path.",
        handler=lambda path: ctx.files.create_folder(path),
        category="files", level=LEVEL_CONFIRM,
        parameters=_obj(path=_par("string", "Full path of the new folder", _required=True)),
        verify=lambda data: (data.get("verified"), "folder exists"),
    ))
    add(Tool(
        name="create_folder_by_name",
        description="Create a folder by name inside a well-known folder (Documents, Desktop, Downloads...).",
        handler=_create_folder_by_name(ctx),
        category="files", level=LEVEL_CONFIRM,
        parameters=_obj(
            name=_par("string", "Folder name", _required=True),
            parent=_par("string", "Where to create it: Documents, Desktop, Downloads or an absolute path"),
        ),
        verify=lambda data: (data.get("verified"), "folder exists"),
    ))
    add(Tool(
        name="rename_path",
        description="Rename a file or folder without moving it.",
        handler=lambda path, new_name: ctx.files.rename(path, new_name),
        category="files", level=LEVEL_CONFIRM,
        parameters=_obj(
            path=_par("string", "Existing path", _required=True),
            new_name=_par("string", "New name", _required=True),
        ),
        verify=lambda data: (data.get("verified"), "renamed item exists"),
    ))
    add(Tool(
        name="copy_path",
        description="Copy a file or folder.",
        handler=lambda source, destination, overwrite=False: ctx.files.copy(source, destination, overwrite),
        category="files", level=LEVEL_CONFIRM,
        parameters=_obj(
            source=_par("string", "Source path", _required=True),
            destination=_par("string", "Destination path", _required=True),
            overwrite=_par("boolean", "Allow overwriting"),
        ),
        verify=lambda data: (data.get("verified"), "destination exists"),
    ))
    add(Tool(
        name="move_path",
        description="Move a file or folder.",
        handler=lambda source, destination, overwrite=False: ctx.files.move(source, destination, overwrite),
        category="files", level=LEVEL_CONFIRM,
        parameters=_obj(
            source=_par("string", "Source path", _required=True),
            destination=_par("string", "Destination path", _required=True),
            overwrite=_par("boolean", "Allow overwriting"),
        ),
        verify=lambda data: (data.get("verified"), "moved and source gone"),
    ))
    add(Tool(
        name="delete_path",
        description="Delete a file or folder. Recoverable by default: the item is moved to RAY's quarantine folder.",
        handler=lambda path, permanent=False: ctx.files.trash(path, permanent),
        category="files", level=LEVEL_APPROVE, destructive=True,
        parameters=_obj(
            path=_par("string", "Path to remove", _required=True),
            permanent=_par("boolean", "Permanently delete instead of quarantining"),
        ),
        verify=lambda data: (data.get("verified"), "item no longer at its old path"),
    ))
    add(Tool(
        name="find_duplicates",
        description="Find duplicate files by SHA-256 hash in a folder.",
        handler=lambda root=None, min_size_kb=32: ctx.files.duplicates(root, min_size_kb),
        category="files", level=LEVEL_AUTO,
        parameters=_obj(
            root=_par("string", "Folder to scan (defaults to Downloads)"),
            min_size_kb=_par("integer", "Ignore files smaller than this"),
        ),
    ))
    add(Tool(
        name="find_large_files",
        description="Find the largest files on this machine.",
        handler=lambda root=None, min_mb=100, limit=20: ctx.files.large_files(root, min_mb, limit),
        category="files", level=LEVEL_AUTO,
        parameters=_obj(
            root=_par("string", "Folder to scan"),
            min_mb=_par("number", "Minimum size in MB"),
            limit=_par("integer", "Maximum results"),
        ),
    ))
    add(Tool(
        name="plan_downloads_organisation",
        description="Propose a plan to sort the Downloads folder into categories. Nothing is moved.",
        handler=lambda folder=None: ctx.files.plan_organisation(folder),
        category="files", level=LEVEL_AUTO,
        parameters=_obj(folder=_par("string", "Folder to organise (defaults to Downloads)")),
    ))
    add(Tool(
        name="apply_organisation",
        description="Apply a previously generated organisation plan (moves files).",
        handler=lambda plan: ctx.files.apply_organisation(plan),
        category="files", level=LEVEL_CONFIRM,
        parameters=_obj(plan=_par("array", "The plan items from plan_downloads_organisation", _required=True)),
    ))
    add(Tool(
        name="backup_path",
        description="Create a verified zip backup of a folder or file.",
        handler=_backup_path(ctx),
        category="files", level=LEVEL_CONFIRM,
        parameters=_obj(
            target=_par("string", "Folder or file to back up (or a well-known folder name)", _required=True),
            destination=_par("string", "Optional destination zip path"),
        ),
        verify=lambda data: (data.get("verified"), "archive CRC check passed"),
    ))
    add(Tool(
        name="create_backup",
        description="Create a verified zip archive of any path.",
        handler=lambda source, destination=None: ctx.files.create_backup(source, destination),
        category="files", level=LEVEL_CONFIRM,
        parameters=_obj(
            source=_par("string", "Path to archive", _required=True),
            destination=_par("string", "Destination zip path"),
        ),
        verify=lambda data: (data.get("verified"), "archive CRC check passed"),
    ))

    # ============================================================ knowledge
    add(Tool(
        name="index_files",
        description="Index local folders for fast full-text search (never uploads anything).",
        handler=lambda roots=None: _index_files(ctx, roots),
        category="knowledge", level=LEVEL_CONFIRM,
        parameters=_obj(roots=_par("array", "Optional list of folders to add and index")),
    ))
    add(Tool(
        name="index_status",
        description="Show how many files are indexed and which roots are being watched.",
        handler=lambda: ctx.indexer.stats(),
        category="knowledge", level=LEVEL_AUTO, parameters=_obj(),
    ))
    add(Tool(
        name="search_index",
        description="Full-text search across indexed documents, including their contents.",
        handler=lambda query="", extension=None, kind=None, limit=30: ctx.search.search(
            query=query, extension=extension, kind=kind, limit=limit
        ),
        category="knowledge", level=LEVEL_AUTO,
        parameters=_obj(
            query=_par("string", "Words to find"),
            extension=_par("string", "Filter by extension"),
            kind=_par("string", "Filter by kind"),
            limit=_par("integer", "Maximum results"),
        ),
    ))
    add(Tool(
        name="extract_document",
        description="Extract readable text from a PDF, DOCX, XLSX, PPTX, CSV or text file.",
        handler=lambda path, max_chars=6000: _extract_tool(path, max_chars),
        category="knowledge", level=LEVEL_AUTO,
        parameters=_obj(
            path=_par("string", "Document path", _required=True),
            max_chars=_par("integer", "How much text to return"),
        ),
    ))
    add(Tool(
        name="summarise_document",
        description="Summarise a document. Uses AI only when you have allowed documents to be sent to the cloud; otherwise it builds a local extractive summary.",
        handler=lambda path, sentences=8: _summarise_document(ctx, path, sentences),
        category="knowledge", level=LEVEL_AUTO,
        parameters=_obj(
            path=_par("string", "Document path", _required=True),
            sentences=_par("integer", "Target length in sentences"),
        ),
    ))
    add(Tool(
        name="ask_document",
        description="Ask a question about one document. Requires permission to send that document's text to the configured AI provider.",
        handler=lambda path, question: _ask_document(ctx, path, question),
        category="knowledge", level=LEVEL_CONFIRM,
        parameters=_obj(
            path=_par("string", "Document path", _required=True),
            question=_par("string", "Your question", _required=True),
        ),
    ))

    # ========================================================= productivity
    add(Tool(
        name="create_task",
        description="Create a task with an optional due date and priority.",
        handler=lambda title, description="", priority="normal", due_at=None, category="general", recurrence=None: ctx.tasks.create(
            title, description, priority, due_at, category, recurrence
        ),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(
            title=_par("string", "Task title", _required=True),
            description=_par("string", "Details"),
            priority=_par("string", "low, normal, high or urgent", enum=["low", "normal", "high", "urgent"]),
            due_at=_par("string", "ISO date/time"),
            category=_par("string", "Category"),
            recurrence=_par("string", "daily, weekdays, weekly, monthly or every:Nd"),
        ),
    ))
    add(Tool(
        name="list_tasks",
        description="List tasks, newest urgency first.",
        handler=lambda status="open", category=None, limit=50: ctx.tasks.list(status=status, category=category, limit=limit),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(
            status=_par("string", "open, in_progress, done or all"),
            category=_par("string", "Category filter"),
            limit=_par("integer", "Maximum results"),
        ),
    ))
    add(Tool(
        name="update_task",
        description="Change a task's title, priority, status or due date.",
        handler=lambda task_id, **fields: ctx.tasks.update(int(task_id), **fields),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(
            task_id=_par("integer", "Task id", _required=True),
            title=_par("string", "New title"),
            priority=_par("string", "New priority"),
            status=_par("string", "New status"),
            due_at=_par("string", "New due date"),
        ),
    ))
    add(Tool(
        name="complete_task",
        description="Mark a task as done (recurring tasks schedule their next occurrence).",
        handler=lambda task_id: ctx.tasks.complete(int(task_id)),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(task_id=_par("integer", "Task id", _required=True)),
    ))
    add(Tool(
        name="delete_task",
        description="Delete a task.",
        handler=lambda task_id: {"deleted": ctx.tasks.delete(int(task_id))},
        category="productivity", level=LEVEL_CONFIRM, destructive=True,
        parameters=_obj(task_id=_par("integer", "Task id", _required=True)),
    ))
    add(Tool(
        name="create_note",
        description="Save a note.",
        handler=lambda body="", title="", category="general": ctx.notes.create(title, body, category),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(
            body=_par("string", "Note content", _required=True),
            title=_par("string", "Title"),
            category=_par("string", "Category"),
        ),
    ))
    add(Tool(
        name="list_notes",
        description="List or search notes.",
        handler=lambda query=None, category=None, limit=100: ctx.notes.list(category=category, query=query, limit=limit),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(
            query=_par("string", "Search text"),
            category=_par("string", "Category"),
            limit=_par("integer", "Maximum results"),
        ),
    ))
    add(Tool(
        name="update_note",
        description="Update a note's body, title, category or pinned state.",
        handler=lambda note_id, **fields: ctx.notes.update(int(note_id), **fields),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(
            note_id=_par("integer", "Note id", _required=True),
            body=_par("string", "New body"),
            title=_par("string", "New title"),
            category=_par("string", "New category"),
            pinned=_par("boolean", "Pin the note"),
        ),
    ))
    add(Tool(
        name="delete_note",
        description="Delete a note.",
        handler=lambda note_id: {"deleted": ctx.notes.delete(int(note_id))},
        category="productivity", level=LEVEL_CONFIRM, destructive=True,
        parameters=_obj(note_id=_par("integer", "Note id", _required=True)),
    ))
    add(Tool(
        name="create_reminder",
        description="Set a reminder for a specific time.",
        handler=lambda title, due_at, recurrence=None: ctx.reminders.create(title, due_at, recurrence),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(
            title=_par("string", "What to be reminded about", _required=True),
            due_at=_par("string", "When (ISO date/time)", _required=True),
            recurrence=_par("string", "Optional recurrence"),
        ),
    ))
    add(Tool(
        name="list_reminders",
        description="List active reminders.",
        handler=lambda: ctx.reminders.list(),
        category="productivity", level=LEVEL_AUTO, parameters=_obj(),
    ))
    add(Tool(
        name="delete_reminder",
        description="Delete a reminder.",
        handler=lambda reminder_id: {"deleted": ctx.reminders.delete(int(reminder_id))},
        category="productivity", level=LEVEL_CONFIRM, destructive=True,
        parameters=_obj(reminder_id=_par("integer", "Reminder id", _required=True)),
    ))
    add(Tool(
        name="snooze_reminder",
        description="Snooze a reminder for a few minutes.",
        handler=lambda reminder_id, minutes=10: ctx.reminders.snooze(int(reminder_id), minutes),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(
            reminder_id=_par("integer", "Reminder id", _required=True),
            minutes=_par("integer", "Minutes to snooze"),
        ),
    ))
    add(Tool(
        name="create_event",
        description="Add a calendar event.",
        handler=lambda title, start_at, end_at=None, description="", location="": ctx.calendar.create(
            title, start_at, end_at=end_at, description=description, location=location
        ),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(
            title=_par("string", "Event title", _required=True),
            start_at=_par("string", "Start (ISO date/time)", _required=True),
            end_at=_par("string", "End (ISO date/time)"),
            description=_par("string", "Details"),
            location=_par("string", "Location"),
        ),
    ))
    add(Tool(
        name="list_schedule",
        description="Read the calendar: today, tomorrow, this week or a specific month.",
        handler=lambda view="today": _schedule(ctx, view),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(view=_par("string", "today, tomorrow, week, month", enum=["today", "tomorrow", "week", "month"])),
    ))
    add(Tool(
        name="delete_event",
        description="Delete a calendar event.",
        handler=lambda event_id: {"deleted": ctx.calendar.delete(int(event_id))},
        category="productivity", level=LEVEL_CONFIRM, destructive=True,
        parameters=_obj(event_id=_par("integer", "Event id", _required=True)),
    ))
    add(Tool(
        name="log_habit",
        description="Log progress on a habit.",
        handler=lambda habit_id, amount=1: ctx.focus.log_habit(int(habit_id), amount),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(
            habit_id=_par("integer", "Habit id", _required=True),
            amount=_par("integer", "Amount to add"),
        ),
    ))
    add(Tool(
        name="start_focus",
        description="Start a focus (Pomodoro) session.",
        handler=lambda minutes=25, label="": ctx.focus.start_focus(minutes, label),
        category="productivity", level=LEVEL_AUTO,
        parameters=_obj(
            minutes=_par("integer", "Session length"),
            label=_par("string", "What you are focusing on"),
        ),
    ))

    # ================================================================ memory
    add(Tool(
        name="remember",
        description="Store a durable memory (preferences, projects, facts). Never stores credentials.",
        handler=lambda title, content="", category="general": ctx.memory.remember(title, content, category),
        category="memory", level=LEVEL_AUTO,
        parameters=_obj(
            title=_par("string", "Short label", _required=True),
            content=_par("string", "Full detail"),
            category=_par("string", "preference, project, person, fact, routine, general"),
        ),
    ))
    add(Tool(
        name="recall_memory",
        description="Search stored memories.",
        handler=lambda query="", limit=20: ctx.memory.recall(query, limit=limit),
        category="memory", level=LEVEL_AUTO,
        parameters=_obj(
            query=_par("string", "Words to look for"),
            limit=_par("integer", "Maximum results"),
        ),
    ))
    add(Tool(
        name="forget_memory",
        description="Delete a stored memory.",
        handler=lambda memory_id: {"deleted": ctx.memory.forget(int(memory_id))},
        category="memory", level=LEVEL_CONFIRM, destructive=True,
        parameters=_obj(memory_id=_par("integer", "Memory id", _required=True)),
    ))

    # =============================================================== browser
    add(Tool(
        name="web_search",
        description="Search the web with DuckDuckGo and return real result links. Set open_browser to also open the results page.",
        handler=lambda query, limit=8, open_browser=False: _web_search_tool(ctx, query, limit, open_browser),
        category="browser", level=LEVEL_AUTO,
        parameters=_obj(
            query=_par("string", "Search query", _required=True),
            limit=_par("integer", "Number of results"),
            open_browser=_par("boolean", "Open the results page in the default browser"),
        ),
        examples=["search the web for the Companies Act 2013", "open chrome and search for the Companies Act"],
    ))
    add(Tool(
        name="fetch_page",
        description="Fetch a web page and return its readable text.",
        handler=lambda url, max_chars=8000: ctx.browser.fetch(url, max_chars).as_dict(),
        category="browser", level=LEVEL_AUTO,
        parameters=_obj(
            url=_par("string", "Page URL", _required=True),
            max_chars=_par("integer", "How much text to return"),
        ),
    ))
    add(Tool(
        name="research",
        description="Search, read the top sources and produce a cited research summary.",
        handler=lambda question, pages=4: ctx.browser.research(question, router=ctx.router, pages=pages),
        category="browser", level=LEVEL_CONFIRM,
        parameters=_obj(
            question=_par("string", "Research question", _required=True),
            pages=_par("integer", "How many sources to read"),
        ),
    ))
    add(Tool(
        name="open_url",
        description="Open a URL in the default browser.",
        handler=lambda url: ctx.browser.open_in_browser(url),
        category="browser", level=LEVEL_AUTO,
        parameters=_obj(url=_par("string", "URL to open", _required=True)),
    ))
    add(Tool(
        name="add_bookmark",
        description="Save a bookmark.",
        handler=lambda url, title="", tags="": ctx.browser.add_bookmark(title or url, url, tags),
        category="browser", level=LEVEL_AUTO,
        parameters=_obj(
            url=_par("string", "URL", _required=True),
            title=_par("string", "Title"),
            tags=_par("string", "Comma separated tags"),
        ),
    ))
    add(Tool(
        name="list_bookmarks",
        description="List saved bookmarks.",
        handler=lambda: ctx.browser.bookmarks(),
        category="browser", level=LEVEL_AUTO, parameters=_obj(),
    ))

    # ============================================================ automation
    add(Tool(
        name="list_workflows",
        description="List automation workflows and their state.",
        handler=lambda: ctx.automation.workflows.list(),
        category="automation", level=LEVEL_AUTO, parameters=_obj(),
    ))
    add(Tool(
        name="create_workflow",
        description="Create a workflow: trigger + optional conditions + actions.",
        handler=lambda name, trigger, actions, conditions=None, description="": ctx.automation.workflows.create(
            name, trigger, actions, conditions, description
        ),
        category="automation", level=LEVEL_CONFIRM,
        parameters=_obj(
            name=_par("string", "Workflow name", _required=True),
            trigger=_par("object", "e.g. {\"type\":\"daily\",\"at\":\"18:00\"}", _required=True),
            actions=_par("array", "List of actions, e.g. [{\"type\":\"notify\",\"title\":\"Hi\"}]", _required=True),
            conditions=_par("array", "Optional condition list"),
            description=_par("string", "What it does"),
        ),
    ))
    add(Tool(
        name="run_workflow",
        description="Run a workflow now.",
        handler=lambda workflow_id: ctx.automation.run(int(workflow_id), trigger="manual"),
        category="automation", level=LEVEL_CONFIRM,
        parameters=_obj(workflow_id=_par("integer", "Workflow id", _required=True)),
    ))
    add(Tool(
        name="workflow_history",
        description="Recent workflow runs and outcomes.",
        handler=lambda workflow_id=None, limit=20: ctx.automation.workflows.runs(
            int(workflow_id) if workflow_id else None, limit
        ),
        category="automation", level=LEVEL_AUTO,
        parameters=_obj(
            workflow_id=_par("integer", "Optional workflow id"),
            limit=_par("integer", "Maximum runs"),
        ),
    ))
    add(Tool(
        name="delete_workflow",
        description="Delete a workflow and its history.",
        handler=lambda workflow_id: {"deleted": ctx.automation.workflows.delete(int(workflow_id))},
        category="automation", level=LEVEL_CONFIRM, destructive=True,
        parameters=_obj(workflow_id=_par("integer", "Workflow id", _required=True)),
    ))

    # ================================================================= media
    add(Tool(
        name="play_media",
        description="Play a local song, track, album folder or playlist.",
        handler=lambda query: ctx.media.play(query),
        category="media", level=LEVEL_AUTO,
        parameters=_obj(query=_par("string", "Track, artist, folder or playlist name", _required=True)),
        examples=["play my REVX playlist"],
    ))
    add(Tool(
        name="search_media",
        description="Search the local music and video library.",
        handler=lambda query="", limit=20: ctx.media.search(query, limit),
        category="media", level=LEVEL_AUTO,
        parameters=_obj(
            query=_par("string", "Search text"),
            limit=_par("integer", "Maximum results"),
        ),
    ))
    add(Tool(
        name="list_playlists",
        description="List local playlist files (.m3u/.pls) found in your media folders.",
        handler=lambda: ctx.media.playlists(),
        category="media", level=LEVEL_AUTO, parameters=_obj(),
    ))

    # ================================================================= voice
    add(Tool(
        name="speak",
        description="Speak a sentence out loud through the configured voice.",
        handler=lambda text: ctx.speak(text),
        category="voice", level=LEVEL_AUTO,
        parameters=_obj(text=_par("string", "What to say", _required=True)),
    ))

    log.info("registered %s tools", len(registry.names()))
    return registry


# ------------------------------------------------------------------ handlers

def _web_search_tool(ctx, query: str, limit: int = 8, open_browser: bool = False) -> dict[str, Any]:
    payload = ctx.browser.search(query, limit)
    if open_browser:
        from urllib.parse import quote_plus

        opened = ctx.browser.open_in_browser(f"https://duckduckgo.com/?q={quote_plus(query)}")
        payload["opened_browser"] = opened
    return payload


def _resolve_search_root(ctx, root: str | None):
    """Accept a path or a friendly folder name (Downloads, Documents, ...)."""
    if not root:
        return None
    candidate = Path(os.path.expandvars(os.path.expanduser(str(root))))
    if candidate.exists():
        return candidate
    try:
        return known_folder(str(root), ctx.config)
    except FileNotFoundError:
        return candidate


def _open_file_by_name(ctx):
    def handler(name: str, open_it: bool = True) -> dict[str, Any]:
        result = ctx.files.search(
            query=name,
            root=None,
            limit=8,
            excluded_names=ctx.config.get("indexing.excluded_names", []),
        )
        if not result["results"]:
            raise FileNotFoundError(f"I could not find a file matching '{name}'")
        best = result["results"][0]
        if open_it:
            ctx.apps.open_path(best["path"])
        return {"file": best, "opened": open_it, "candidates": [item["name"] for item in result["results"][:5]]}

    return handler


def _create_folder_by_name(ctx):
    def handler(name: str, parent: str | None = None) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("a folder needs a name")
        if any(separator in name for separator in ("/", "\\")) or name in (".", ".."):
            raise ValueError("the folder name must not contain path separators")
        base = known_folder(parent or "Documents", ctx.config)
        return ctx.files.create_folder(base / name)

    return handler


def _backup_path(ctx):
    def handler(target: str, destination: str | None = None) -> dict[str, Any]:
        candidate = Path(os.path.expandvars(os.path.expanduser(target)))
        if not candidate.exists():
            try:
                candidate = known_folder(target, ctx.config)
            except FileNotFoundError:
                search = ctx.files.search(query=target, limit=5)
                if search["results"]:
                    candidate = Path(search["results"][0]["path"])
                else:
                    raise FileNotFoundError(
                        f"I could not find '{target}' to back up. Give me a full path or a folder name "
                        "like Documents or Downloads."
                    )
        return ctx.files.create_backup(candidate, destination)

    return handler


def _index_files(ctx, roots: list[str] | None) -> dict[str, Any]:
    added = []
    for root in roots or []:
        entry = ctx.indexer.add_root(root)
        added.append(entry["path"])
    return {"added": added, **ctx.indexer.index()}


def _extract_tool(path: str, max_chars: int = 6000) -> dict[str, Any]:
    document = extract(path, max_chars=max_chars)
    document["text"] = document.get("text", "")[:max_chars]
    return document


def _cloud_allowed(ctx) -> bool:
    return bool(
        ctx.config.get("privacy.send_documents_to_cloud", False)
        or ctx.config.get("ai.allow_cloud_documents", False)
    )


def _summarise_document(ctx, path: str, sentences: int = 8) -> dict[str, Any]:
    document = extract(path, max_chars=60000)
    text = document.get("text", "")
    if not text.strip():
        raise ValueError(
            f"No readable text in {Path(path).name}. "
            f"{document.get('note') or 'It may be a scanned document.'}"
        )
    if _cloud_allowed(ctx):
        try:
            chunks = chunk_text(text, size=9000)
            combined = text[:24000] if len(chunks) <= 3 else "\n\n".join(chunks[:3])
            result = ctx.router.chat(
                "summarize",
                [ChatMessage("user", f"Summarise this document in {sentences} sentences:\n\n{combined}")],
                system="Summarise faithfully. If something is unclear, say so. Do not add facts.",
                temperature=0.2,
                max_tokens=800,
            )
            return {
                "path": str(path), "summary": result.text, "method": f"AI ({result.provider}/{result.model})",
                "chars_read": len(combined), "pages": document.get("pages"),
            }
        except ProviderError as exc:
            return {
                "path": str(path),
                "summary": summarise_locally(text, sentences),
                "method": "local extractive (AI unavailable)",
                "error": exc.message,
                "chars_read": len(text),
                "pages": document.get("pages"),
            }
    return {
        "path": str(path),
        "summary": summarise_locally(text, sentences),
        "method": "local extractive",
        "note": (
            "Cloud document analysis is off, so this summary was made on your machine. "
            "Enable Settings > Privacy > send documents to cloud to use Gemini for deeper analysis."
        ),
        "chars_read": len(text),
        "pages": document.get("pages"),
    }


def _ask_document(ctx, path: str, question: str) -> dict[str, Any]:
    if not _cloud_allowed(ctx):
        raise PermissionError(
            "Answering questions about a document sends its text to the configured AI provider. "
            "Enable Settings > Privacy > 'Send documents to cloud' first, then ask again."
        )
    document = extract(path, max_chars=40000)
    text = document.get("text", "")
    if not text.strip():
        raise ValueError(f"No readable text in {Path(path).name}")
    result = ctx.router.chat(
        "document",
        [ChatMessage("user", f"Document:\n\n{text[:24000]}\n\nQuestion: {question}")],
        system=(
            "Answer using only the document text. Quote short passages when useful. "
            "If the answer is not in the document, say that plainly."
        ),
        temperature=0.2,
        max_tokens=900,
    )
    return {
        "path": str(path),
        "question": question,
        "answer": result.text,
        "provider": result.provider,
        "model": result.model,
    }


def _schedule(ctx, view: str) -> dict[str, Any]:
    view = (view or "today").lower()
    if view == "week":
        return ctx.calendar.week_view()
    if view == "month":
        return ctx.calendar.month_view()
    if view == "tomorrow":
        return {"date": (date.today() + timedelta(days=1)).isoformat(),
                "events": ctx.calendar.day_view(date.today() + timedelta(days=1))["events"]}
    return {"date": date.today().isoformat(), "events": ctx.calendar.today()}
