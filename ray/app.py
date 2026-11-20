"""Application context: builds and owns every RAY subsystem.

One object wires the database, security, providers, tools, services, agents and
background scheduler together.  Both entry points (web UI server and PySide6
desktop shell) use exactly the same engine.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__
from .agents.registry import AgentRegistry
from .ai.base import ProviderError
from .ai.router import ProviderRouter
from .automation.engine import AutomationEngine
from .automation.scheduler import Scheduler
from .brain.orchestrator import Orchestrator
from .brain.planner import Planner
from .browser.search import WebBrowser
from .computer.apps import AppManager
from .computer.controls import InputControl, SystemControl
from .computer.files import FileManager
from .computer.shell import ShellRunner
from .computer.system import SystemMonitor
from .core.config import Config
from .core.database import Database
from .core.events import bus as event_bus
from .core.logging_setup import get_logger, setup_logging
from .core.paths import Paths, paths as default_paths
from .knowledge.indexer import FileIndexer, IndexSearch
from .media.service import MediaService
from .memory.store import MemoryStore
from .productivity.calendar import CalendarService
from .productivity.focus import FocusService
from .productivity.notes import NoteService
from .productivity.reminders import ReminderService
from .productivity.tasks import TaskService
from .security.audit import AuditLog
from .security.credentials import CredentialStore
from .security.permissions import PermissionManager
from .tools.builtin import known_folder, register_tools
from .tools.registry import ToolRegistry

log = get_logger("app")


class RayApp:
    """The running application."""

    def __init__(self, home: Path | str | None = None, log_level: str = "INFO") -> None:
        self.started_at = time.time()
        self.paths = Paths(home) if home else default_paths()
        setup_logging(self.paths.logs, level=log_level)

        self.config = Config(self.paths.config_path)
        self.db = Database(self.paths.db_path)
        self.db.migrate()
        self.bus = event_bus

        self.permissions = PermissionManager(self.db, self.config, self.bus)
        self.audit = AuditLog(self.db)
        self.credentials = CredentialStore(self.db, self.paths.secret_key_path)

        self.router = ProviderRouter(self.db, self.config, self.credentials, self.bus)
        self.tools = ToolRegistry(self.permissions, self.audit, self.bus)

        # services
        self.sysmon = SystemMonitor()
        self.files = FileManager(self.paths.quarantine, self._known_downloads())
        self.apps = AppManager(self.db)
        self.controls = SystemControl(self.paths.screenshots)
        self.input = InputControl()
        self.shell = ShellRunner(bool(self.config.get("permissions.allow_shell", False)))
        self.config.on_change(self._on_config_change)

        self.tasks = TaskService(self.db, self.bus)
        self.notes = NoteService(self.db, self.bus)
        self.reminders = ReminderService(self.db, self.bus)
        self.calendar = CalendarService(self.db, self.bus)
        self.focus = FocusService(self.db, self.bus)
        self.memory = MemoryStore(self.db, self.bus, bool(self.config.get("memory.enabled", True)))

        self.indexer = FileIndexer(self.db, self.config)
        self.search = IndexSearch(self.db)
        self.browser = WebBrowser(self.db)
        self.media = MediaService(self.apps, self.config)

        self.automation = AutomationEngine(
            self.db, self.config, self.tools, self.tasks, self.bus,
            state_path=self.paths.data / "automation_state.json",
            notifier=self._desktop_notify,
        )
        self.scheduler = Scheduler(
            self.config, self.reminders, self.automation,
            cache=self.router.cache, bus=self.bus, indexer=self.indexer,
        )

        self.agents = AgentRegistry()
        self.planner = Planner(self.router, self.tools, self.memory, self.config)
        self.orchestrator = Orchestrator(
            self.db, self.router, self.tools, self.planner, self.memory,
            self.bus, self.config, self.agents,
        )

        register_tools(self.tools, self)
        self._ensure_defaults()
        log.info(
            "RAY %s ready | home=%s | tools=%s | providers=%s",
            __version__, self.paths.home, len(self.tools.names()),
            ", ".join(self.router.available_providers()) or "none configured",
        )

    # ------------------------------------------------------------------ setup
    def _known_downloads(self) -> Path:
        try:
            return known_folder("downloads")
        except FileNotFoundError:
            return Path.home() / "Downloads"

    def _ensure_defaults(self) -> None:
        """Create the small set of automations that make RAY feel alive on day one."""
        try:
            existing = {workflow["name"] for workflow in self.automation.workflows.list()}
        except Exception:  # pragma: no cover
            return
        defaults = [
            {
                "name": "Morning briefing",
                "description": "At 08:00, show today's schedule and any overdue tasks.",
                "trigger": {"type": "daily", "at": "08:00"},
                "actions": [
                    {"type": "tool", "tool": "list_schedule", "args": {"view": "today"}},
                    {"type": "notify", "title": "Good morning",
                     "body": "Your schedule and tasks are ready on the Home dashboard."},
                ],
                "enabled": False,
            },
            {
                "name": "Evening review",
                "description": "At 20:00, check unfinished tasks and preview tomorrow.",
                "trigger": {"type": "daily", "at": "20:00"},
                "actions": [
                    {"type": "tool", "tool": "list_tasks", "args": {"status": "open"}},
                    {"type": "notify", "title": "Evening review",
                     "body": "Open RAY to see what carried over and what tomorrow looks like."},
                ],
                "enabled": False,
            },
            {
                "name": "Downloads watcher",
                "description": "When something new lands in Downloads, offer to organise it.",
                "trigger": {"type": "file_added", "folder": str(self._known_downloads())},
                "actions": [
                    {"type": "tool", "tool": "plan_downloads_organisation", "args": {}},
                    {"type": "notify", "title": "New download",
                     "body": "A new file arrived. RAY can suggest where it belongs."},
                ],
                "enabled": False,
            },
            {
                "name": "Startup health check",
                "description": "When RAY starts, read system health and today's agenda.",
                "trigger": {"type": "on_start"},
                "actions": [
                    {"type": "tool", "tool": "system_report", "args": {}},
                    {"type": "tool", "tool": "list_schedule", "args": {"view": "today"}},
                ],
                "enabled": False,
            },
        ]
        for template in defaults:
            if template["name"] in existing:
                continue
            try:
                self.automation.workflows.create(
                    name=template["name"],
                    trigger=template["trigger"],
                    actions=template["actions"],
                    description=template["description"],
                    enabled=template["enabled"],
                )
            except Exception as exc:  # pragma: no cover
                log.warning("could not create default workflow '%s': %s", template["name"], exc)

    def _on_config_change(self, key: str, value: Any) -> None:
        if key == "permissions.allow_shell":
            self.shell.enabled = bool(value)
        if key == "memory.enabled":
            self.memory.enabled = bool(value)

    # -------------------------------------------------------------- lifecycle
    def start(self, run_startup_workflows: bool = True) -> None:
        self.scheduler.start()
        self.config.mark_started()
        self.audit.record("app_start", target=__version__, detail="RAY started")
        if run_startup_workflows:
            threading.Thread(target=self._startup_workflows, name="ray-startup", daemon=True).start()

    def _startup_workflows(self) -> None:
        time.sleep(1.0)
        try:
            runs = self.automation.run_on_start()
            if runs:
                log.info("startup workflows executed: %s", len(runs))
        except Exception:  # pragma: no cover
            log.exception("startup workflows failed")

    def stop(self) -> None:
        try:
            self.scheduler.stop()
        except Exception:
            pass
        try:
            self.audit.record("app_stop", detail="RAY stopped cleanly")
        except Exception:
            pass
        try:
            self.db.backup(self.paths.backups / "ray-last-session.db")
        except Exception as exc:  # pragma: no cover
            log.warning("shutdown backup failed: %s", exc)
        self.db.close()
        log.info("RAY stopped")

    # ---------------------------------------------------------------- helpers
    def power(self, action: str, delay_seconds: int = 5) -> dict[str, Any]:
        action = (action or "").strip().lower()
        if action == "lock":
            return self.controls.lock()
        if action == "sleep":
            return self.controls.sleep()
        if action == "restart":
            return self.controls.restart(delay_seconds)
        if action == "shutdown":
            return self.controls.shutdown(delay_seconds)
        raise ValueError(f"unknown power action '{action}'")

    def speak(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("nothing to say")
        self.bus.publish("speak_request", text=text, origin="tool")
        return {"queued": True, "text": text[:400]}

    def _desktop_notify(self, title: str, body: str = "") -> None:
        """Best-effort native notification; the UI toast always fires."""
        try:
            if _is_windows():
                from win10toast import ToastNotifier  # type: ignore

                ToastNotifier().show_toast(title, body, duration=6, threaded=True)
            elif _is_mac():
                import subprocess

                subprocess.run(
                    ["osascript", "-e", f'display notification "{body}" with title "{title}"'],
                    capture_output=True, timeout=5,
                )
            else:
                import shutil
                import subprocess

                if shutil.which("notify-send"):
                    subprocess.run(["notify-send", title, body], capture_output=True, timeout=5)
        except Exception:
            pass  # UI handles it; native notifications are a bonus

    # ----------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        try:
            health = self.router.health()
        except Exception as exc:  # pragma: no cover
            health = {"error": str(exc)}
        return {
            "name": "RAY",
            "version": __version__,
            "uptime_seconds": int(time.time() - self.started_at),
            "home": str(self.paths.home),
            "platform": self.sysmon.identity(),
            "providers": health,
            "offline": self.router.is_offline(),
            "provider_detail": self.router.describe(),
            "tools": self.tools.describe(),
            "security": self.permissions.describe(),
            "scheduler": self.scheduler.status(),
            "automation": self.automation.describe(),
            "database": {
                "path": str(self.db.path),
                "size_bytes": self.db.size_bytes(),
                "counts": self.db.table_counts(),
                "integrity": self.db.integrity_check()[0],
            },
            "index": self.indexer.stats(),
            "agents": self.agents.list(),
            "counts": {
                "tasks_open": self.tasks.stats()["open"],
                "notes": self.notes.stats()["total"],
                "reminders": self.reminders.stats()["active"],
                "memories": self.memory.stats()["total"],
                "workflows": self.automation.describe()["workflows"],
            },
        }

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "database": self.db.integrity_check()[0],
            "scheduler": self.scheduler.running,
            "tools": len(self.tools.names()),
            "config_problems": self.config.validate(),
        }


def _is_windows() -> bool:
    import sys

    return sys.platform.startswith("win")


def _is_mac() -> bool:
    import sys

    return sys.platform == "darwin"
