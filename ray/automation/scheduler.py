"""One lightweight background thread that drives everything time-based.

Every tick (20s by default, configurable) it:
    * fires due reminders,
    * runs workflows whose trigger is due,
    * prunes expired cache rows,
    * publishes a heartbeat the UI uses for its "engine alive" indicator.

Deliberately a single thread with tiny work per tick - RAY must stay comfortable
on a 2-core Celeron with 8 GB of RAM.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from ..core.config import Config
from ..core.events import EventBus, bus as default_bus
from ..core.logging_setup import get_logger
from ..productivity.reminders import ReminderService
from .engine import AutomationEngine

log = get_logger("automation.scheduler")


class Scheduler:
    def __init__(
        self,
        config: Config,
        reminders: ReminderService,
        automation: AutomationEngine,
        cache=None,
        bus: EventBus | None = None,
        indexer=None,
    ) -> None:
        self.config = config
        self.reminders = reminders
        self.automation = automation
        self.cache = cache
        self.bus = bus or default_bus
        self.indexer = indexer
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.ticks = 0
        self.last_tick: float | None = None
        self._last_index = 0.0

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ray-scheduler", daemon=True)
        self._thread.start()
        log.info("scheduler started")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        log.info("scheduler stopped")

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def _loop(self) -> None:
        while not self._stop.is_set():
            interval = max(5, int(self.config.get("automation.tick_seconds", 20) or 20))
            try:
                self.tick()
            except Exception:  # pragma: no cover - the loop must survive
                log.exception("scheduler tick failed")
            self._stop.wait(interval)

    # -------------------------------------------------------------------- tick
    def tick(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"ts": time.time(), "reminders": 0, "workflows": 0}
        due = self.reminders.due()
        for reminder in due:
            summary["reminders"] += 1
            self.automation.notify(f"Reminder: {reminder['title']}", reminder.get("due_at", ""), kind="reminder")
            self.bus.publish("speak_request", text=f"Reminder. {reminder['title']}", origin="reminder")

        runs = self.automation.tick()
        summary["workflows"] = len(runs)

        self.ticks += 1
        if self.cache and self.ticks % 60 == 0:
            self.cache.purge_expired()

        interval_minutes = int(self.config.get("indexing.auto_index_minutes", 0) or 0)
        if self.indexer and interval_minutes > 0 and not self.indexer.running:
            if (time.time() - self._last_index) > interval_minutes * 60:
                self._last_index = time.time()
                threading.Thread(target=self._safe_index, name="ray-index", daemon=True).start()

        self.last_tick = time.time()
        self.bus.publish("heartbeat", ticks=self.ticks, reminders=summary["reminders"], workflows=summary["workflows"])
        return summary

    def _safe_index(self) -> None:
        try:
            result = self.indexer.index()
            log.info("background index: %s", result)
        finally:
            self._last_index = time.time()

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "ticks": self.ticks,
            "last_tick": self.last_tick,
            "interval": int(self.config.get("automation.tick_seconds", 20) or 20),
        }
