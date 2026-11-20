"""Application configuration.

Settings live in ``<RAY_HOME>/config.json``.  Nothing secret is stored here -
API credentials go through :mod:`ray.security.credentials`.

Access is dotted: ``config.get("ai.providers.grok.model")``.  Unknown keys are
preserved on save so settings written by newer builds are not lost.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from ..core.logging_setup import get_logger

log = get_logger("core.config")

DEFAULTS: dict[str, Any] = {
    "app": {
        "version": "0.1.0",
        "first_run": True,
        "user_name": "",
        "theme": "midnight",
        "accent": "cyan",
        "reduce_motion": False,
        "launch_on_startup": False,
        "language": "en",
    },
    "ui": {
        "host": "127.0.0.1",
        "port": 8765,
        "open_browser": True,
        "dashboard_widgets": [
            "system",
            "clock",
            "schedule",
            "tasks",
            "reminders",
            "recent_files",
            "automation",
            "quick_actions",
        ],
    },
    "ai": {
        "default_provider": "cursor",
        "routing_version": 2,
        "stream": False,
        "cache_ttl_seconds": 1800,
        "request_timeout_seconds": 60,
        "max_retries": 2,
        "allow_cloud_documents": False,
        "daily_budget": {"cursor": 300, "grok": 300, "gemini": 400, "fish": 300},
        "providers": {
            "cursor": {
                "enabled": True,
                "binary": "",
                "workspace": "",
                "model": "",
                "timeout": 180,
                "output_format": "text",
                "extra_args": [],
            },
            "grok": {
                "enabled": True,
                "model": "grok-4",
                "base_url": "https://api.x.ai/v1",
                "temperature": 0.4,
                "max_tokens": 1200,
            },
            "gemini": {
                "enabled": True,
                "model": "gemini-2.5-flash",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
                "max_tokens": 1600,
            },
            "fish": {
                "enabled": True,
                "model": "s2.1-pro-free",
                "base_url": "https://api.fish.audio",
                "reference_id": "",
                "format": "mp3",
                "latency": "normal",
            },
        },
    },
    "voice": {
        "enabled": False,
        "mode": "push_to_talk",
        "input_device": "",
        "wake_word_enabled": False,
        "wake_word": "ray",
        "stt_provider": "auto",
        "auto_speak": True,
        "speed": 1.0,
        "volume": 0.9,
        "voice_id": "",
        "interruptible": True,
    },
    "permissions": {
        "mode": "standard",
        "level2_confirmation": True,
        "level3_confirmation": True,
        "emergency_stop": False,
        "session_lock_minutes": 0,
        "allow_shell": False,
        "allow_interpreter_eval": False,
        "allow_sensitive_reads": False,
    },
    "browser": {
        "allow_private_addresses": False,
    },
    "indexing": {
        "roots": [],
        "excluded_names": [
            "$RECYCLE.BIN", "System Volume Information", "node_modules", ".git",
            "__pycache__", ".venv", "venv", "AppData", ".cache", "Windows",
            "Program Files", "Program Files (x86)",
        ],
        "max_file_mb": 25,
        "index_content": True,
        "auto_index_minutes": 0,
    },
    "automation": {
        "enabled": True,
        "tick_seconds": 20,
        "max_steps": 25,
        "allow_destructive": False,
        "dry_run": False,
    },
    "memory": {
        "enabled": True,
        "retention_days": 365,
        "auto_capture": True,
    },
    "notifications": {"enabled": True, "sound": False},
    "privacy": {
        "telemetry": False,
        "log_prompts": False,
        "clear_cache_on_exit": False,
        "send_documents_to_cloud": False,
    },
    "paths": {"downloads": "", "documents": "", "music": "", "projects": ""},
    "shortcuts": {
        "command_center": "Ctrl+Space",
        "toggle_voice": "Ctrl+Shift+Space",
        "emergency_stop": "Ctrl+Shift+Esc",
    },
    "agents": {"max_concurrent": 2, "max_steps": 20, "max_seconds": 180},
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# Providers whose model may stay blank (the account or CLI decides).
MODEL_OPTIONAL_PROVIDERS = {"cursor"}


class Config:
    """Thread-safe JSON settings store with change notification."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        self._listeners: list[Callable[[str, Any], None]] = []
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        if self.path and self.path.exists():
            self.load()

    # ------------------------------------------------------------------ load
    def load(self) -> dict:
        if not self.path:
            return self._data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._data
        except (json.JSONDecodeError, OSError) as exc:
            log.error("config file unreadable (%s); using defaults", exc)
            backup = self.path.with_suffix(".json.corrupt")
            try:
                self.path.replace(backup)
                log.warning("moved damaged config to %s", backup)
            except OSError:
                pass
            return self._data
        stored = raw if isinstance(raw, dict) else {}
        with self._lock:
            self._data = _deep_merge(DEFAULTS, stored)
            migrated = self._migrate(stored)
        if migrated:
            try:
                self.save()
            except OSError:  # a read-only config must not stop the app
                pass
        return self._data

    def _migrate(self, stored: dict[str, Any]) -> bool:
        """One-off upgrades so existing installs pick up better defaults."""
        ai = self._data.setdefault("ai", {})
        saved = stored.get("ai") if isinstance(stored.get("ai"), dict) else {}
        version = int(saved.get("routing_version", 1) or 1)
        changed = False
        if version < 2:
            if str(saved.get("default_provider", "")) == "grok":
                # Grok used to be the primary engine; Cursor replaced it.
                ai["default_provider"] = "cursor"
            ai["routing_version"] = 2
            changed = True
        return changed

    def save(self) -> None:
        if not self.path:
            return
        with self._lock:
            payload = json.dumps(self._data, indent=2, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                try:
                    os.chmod(tmp, 0o600)
                except OSError:  # pragma: no cover
                    pass
            os.replace(tmp, self.path)
        except OSError as exc:
            log.error("could not save config: %s", exc)
            raise
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # ----------------------------------------------------------------- access
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return copy.deepcopy(node) if isinstance(node, (dict, list)) else node

    def set(self, dotted: str, value: Any, save: bool = True) -> None:
        parts = dotted.split(".")
        with self._lock:
            node = self._data
            for part in parts[:-1]:
                if not isinstance(node.get(part), dict):
                    node[part] = {}
                node = node[part]
            node[parts[-1]] = value
        if save:
            self.save()
        for listener in list(self._listeners):
            try:
                listener(dotted, value)
            except Exception:  # pragma: no cover - listener isolation
                log.exception("config listener failed")

    def update(self, values: dict[str, Any], save: bool = True) -> None:
        for key, value in values.items():
            self.set(key, value, save=False)
        if save:
            self.save()

    def all(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def section(self, name: str) -> dict:
        value = self.get(name, {})
        return value if isinstance(value, dict) else {}

    def on_change(self, callback: Callable[[str, Any], None]) -> None:
        self._listeners.append(callback)

    # ------------------------------------------------------------ validation
    def validate(self) -> list[str]:
        """Return human-readable problems; empty list means healthy."""
        problems: list[str] = []
        port = self.get("ui.port")
        if not isinstance(port, int) or not (1 <= port <= 65535):
            problems.append("ui.port must be an integer between 1 and 65535")
        tick = self.get("automation.tick_seconds")
        if not isinstance(tick, (int, float)) or tick < 5:
            problems.append("automation.tick_seconds must be >= 5")
        for name, provider in self.section("ai.providers").items():
            if not isinstance(provider, dict):
                problems.append(f"ai.providers.{name} must be an object")
                continue
            if name not in MODEL_OPTIONAL_PROVIDERS and not str(provider.get("model", "")).strip():
                problems.append(f"ai.providers.{name}.model must not be empty")
            base = str(provider.get("base_url", ""))
            if base and not base.startswith("http"):
                problems.append(f"ai.providers.{name}.base_url must start with http(s)")
        speed = self.get("voice.speed", 1.0)
        if not isinstance(speed, (int, float)) or not (0.5 <= speed <= 2.0):
            problems.append("voice.speed must be between 0.5 and 2.0")
        return problems

    # ---------------------------------------------------------------- helpers
    def provider(self, name: str) -> dict:
        return self.section(f"ai.providers.{name}")

    def mark_started(self) -> None:
        if self.get("app.first_run", True):
            self.set("app.first_run", False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Config {self.path}>"
