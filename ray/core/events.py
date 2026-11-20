"""In-process event bus.

The engine publishes progress, notifications and status changes; the UI polls
``/api/events`` and replays everything newer than its cursor.  A bounded
in-memory ring buffer keeps memory use flat on an 8 GB machine.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_EVENTS = 500


@dataclass
class Event:
    id: int
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "payload": self.payload, "ts": self.ts}


class EventBus:
    def __init__(self, max_events: int = MAX_EVENTS) -> None:
        self._counter = itertools.count(1)
        self._lock = threading.RLock()
        self._events: deque[Event] = deque(maxlen=max_events)
        self._subscribers: dict[str, list[Callable[[Event], None]]] = {}

    def publish(self, topic: str, **payload: Any) -> Event:
        """Publish an event.

        The first argument is the event *topic* (``task_created``, ``security``…)
        while ``payload`` is free-form, so payload keys such as ``kind`` do not
        collide with the topic name.
        """
        event = Event(id=next(self._counter), kind=topic, payload=payload)
        with self._lock:
            self._events.append(event)
            subscribers = list(self._subscribers.get(topic, [])) + list(self._subscribers.get("*", []))
        for callback in subscribers:
            try:
                callback(event)
            except Exception:  # pragma: no cover - subscriber isolation
                pass
        return event

    def subscribe(self, kind: str, callback: Callable[[Event], None]) -> None:
        with self._lock:
            self._subscribers.setdefault(kind, []).append(callback)

    def unsubscribe(self, kind: str, callback: Callable[[Event], None]) -> None:
        with self._lock:
            if callback in self._subscribers.get(kind, []):
                self._subscribers[kind].remove(callback)

    def since(self, after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            events = [event for event in self._events if event.id > after_id]
        return [event.as_dict() for event in events[-limit:]]

    def latest_id(self) -> int:
        with self._lock:
            return self._events[-1].id if self._events else 0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


bus = EventBus()
