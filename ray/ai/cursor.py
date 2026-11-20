"""Cursor CLI provider — an alternative reasoning engine instead of Grok.

Cursor does not publish an OpenAI-compatible ``/chat/completions`` endpoint, so
RAY talks to the *official* Cursor CLI instead (the path Cursor documents for
use outside the editor).  The CLI runs the same agent as the IDE, in print mode:

    cursor-agent --print --mode ask "<prompt>"

Design rules that keep this safe and honest:

* ``--mode ask`` (read-only) is always used and ``--force``/``--yolo`` never is,
  so a chat reply can never edit files or run shell commands on this PC.
* The binary is discovered from Settings, ``PATH``, ``~/.local/bin`` or the
  Windows Programs folder; if it is missing the provider simply reports that it
  is unavailable and the router falls back to Gemini.  Nothing is invented.
* An API key is optional: the CLI can use ``cursor-agent login`` or the key RAY
  stores encrypted in Settings (passed as ``CURSOR_API_KEY``, the variable the
  CLI documents).
* RAY's native tool-calling agents stay on a provider that supports function
  calling; Cursor is skipped whenever tools are requested.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from .base import (
    BaseProvider,
    ChatMessage,
    ChatResult,
    HealthStatus,
    ProviderAuthError,
    ProviderError,
    ProviderQuotaError,
    ProviderTimeout,
    ProviderUnavailable,
)

# Keys a spawned CLI must never inherit: only Cursor's own key is forwarded.
ENV_SCRUBBED = (
    "RAY_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "RAY_FISH_API_KEY", "FISH_API_KEY", "FISH_AUDIO_API_KEY",
    "RAY_GROK_API_KEY", "XAI_API_KEY", "GROK_API_KEY",
    "RAY_CURSOR_API_KEY", "RAY_API_TOKEN",
)

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CANDIDATE_BINARIES = ("cursor-agent", "agent")
CANDIDATE_PATHS = (
    "~/.local/bin/cursor-agent",
    "~/.local/bin/agent",
    "~/.cursor/bin/cursor-agent",
    "~/AppData/Local/Programs/cursor-agent/cursor-agent.exe",
    "%LOCALAPPDATA%/Programs/cursor-agent/cursor-agent.exe",
)
JSON_TEXT_KEYS = ("result", "text", "response", "content", "message", "output", "answer")
MAX_PROMPT_CHARS = 16000


class CursorProvider(BaseProvider):
    name = "cursor"
    supports_tools = False
    supports_vision = False

    def __init__(self, config: dict[str, Any], api_key: str | None = None) -> None:
        super().__init__(config, api_key)
        self.timeout = float(self.config.get("timeout", 180) or 180)
        self.workspace = str(self.config.get("workspace", "") or "").strip()
        self.output_format = str(self.config.get("output_format", "text") or "text").strip().lower()

    # ---------------------------------------------------------------- binary
    def binary(self) -> str | None:
        configured = str(self.config.get("binary", "") or "").strip()
        if configured:
            candidate = Path(os.path.expandvars(os.path.expanduser(configured)))
            if candidate.exists():
                return str(candidate)
            return shutil.which(configured)
        for name in CANDIDATE_BINARIES:
            found = shutil.which(name)
            if found:
                return found
        for raw in CANDIDATE_PATHS:
            candidate = Path(os.path.expandvars(os.path.expanduser(raw)))
            if candidate.exists():
                return str(candidate)
        return None

    @property
    def configured(self) -> bool:
        """True when the CLI itself is present; no API key is required."""
        return bool(self.config.get("enabled", True)) and self.binary() is not None

    def availability(self) -> tuple[bool, str]:
        if not self.config.get("enabled", True):
            return False, "cursor is disabled in Settings"
        binary = self.binary()
        if not binary:
            return (
                False,
                "Cursor CLI not found. Install it (cursor.com/cli) and run 'cursor-agent login', "
                "or set the binary path in Settings > AI providers.",
            )
        key_note = "API key stored" if self.api_key else "uses 'cursor-agent login'"
        return True, f"Cursor CLI at {binary} ({key_note})"

    # ------------------------------------------------------------------ chat
    def chat(
        self,
        messages: Iterable[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
    ) -> ChatResult:
        available, detail = self.availability()
        if not available:
            raise ProviderUnavailable(detail)

        prompt = self._prompt(list(messages), system)
        args: list[str] = [self.binary() or "cursor-agent", "--print", "--mode", "ask"]
        args += ["--output-format", self.output_format]
        if self.model:
            args += ["--model", self.model]
        args += [str(item) for item in (self.config.get("extra_args") or [])]
        args.append(prompt)

        env = self._env()

        started = time.time()
        try:
            completed = subprocess.run(  # noqa: S603 - explicit binary, no shell
                self._command(args),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.workspace or None,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeout(
                f"cursor: the CLI did not answer within {self.timeout:g}s", detail=str(exc)
            ) from exc
        except FileNotFoundError as exc:
            raise ProviderUnavailable(
                "Cursor CLI not found. Install it (cursor.com/cli) or set the binary path in Settings."
            ) from exc
        except OSError as exc:
            raise ProviderError(f"cursor: could not start the CLI ({exc.__class__.__name__})", detail=str(exc)) from exc

        if completed.returncode != 0:
            raise self._failure(completed)

        text = self._extract(completed.stdout)
        return ChatResult(
            text=text,
            provider=self.name,
            model=self.model or "cursor-cli",
            latency_ms=int((time.time() - started) * 1000),
            raw={"returncode": completed.returncode},
        )

    # ----------------------------------------------------------------- probe
    def test_connection(self) -> HealthStatus:
        available, detail = self.availability()
        if not available:
            return HealthStatus(self.name, False, detail)
        binary = self.binary() or "cursor-agent"
        started = time.time()
        version = self._run([binary, "--version"], timeout=25)
        version_text = (version.stdout or version.stderr or "").strip().splitlines()
        version_label = version_text[0].strip()[:60] if version_text else "unknown version"
        latency = int((time.time() - started) * 1000)
        if version.returncode != 0:
            return HealthStatus(self.name, False, f"Cursor CLI found but '--version' failed ({version_label})", latency)

        status = self._run([binary, "status"], timeout=30)
        blob = f"{status.stdout}\n{status.stderr}".lower()
        signed_out = any(
            marker in blob
            for marker in ("not logged in", "logged out", "not signed in", "sign in", "login required", "unauthenticated")
        )
        if signed_out and not self.api_key:
            return HealthStatus(
                self.name,
                False,
                f"{version_label} installed but not signed in — run 'cursor-agent login' or add an API key",
                latency,
            )
        if status.returncode != 0 and not self.api_key:
            return HealthStatus(
                self.name,
                False,
                f"{version_label} installed; 'cursor-agent status' failed ({self._clean(status.stderr)[:120]})",
                latency,
            )
        return HealthStatus(self.name, True, f"{version_label} ready ({'API key' if self.api_key else 'signed in'})", latency)

    def version(self) -> str:
        """First line of ``--version``, or an empty string when it cannot run."""
        binary = self.binary()
        if not binary:
            return ""
        completed = self._run([binary, "--version"], timeout=20)
        text = self._clean(f"{completed.stdout or ''}\n{completed.stderr or ''}")
        return text.splitlines()[0].strip()[:80] if text else ""

    def describe(self) -> dict[str, Any]:
        available, detail = self.availability()
        return {
            "name": self.name,
            "model": self.model or "cursor-cli (account default)",
            "enabled": bool(self.config.get("enabled", True)),
            "configured": self.configured,
            "available": available,
            "detail": detail,
            "supports_tools": self.supports_tools,
            "supports_vision": self.supports_vision,
            "supports_documents": self.supports_documents,
            "binary": self.binary() or "",
            "workspace": self.workspace,
        }

    # --------------------------------------------------------------- helpers
    def _env(self) -> dict[str, str]:
        """Environment for the Cursor CLI: no other provider's key.

        RAY spawns a third-party binary; handing it every API key in the
        environment would let it (or a dependency it loads) read Gemini, Fish,
        xAI and the local API token.  Only Cursor's own key is forwarded.
        """
        env = dict(os.environ)
        for name in ENV_SCRUBBED:
            env.pop(name, None)
        if self.api_key:
            env["CURSOR_API_KEY"] = self.api_key
        return env

    def _command(self, args: list[str]) -> list[str]:
        """Windows .cmd/.bat shims must go through the command interpreter."""
        if args and (args[0].lower().endswith((".cmd", ".bat"))):
            return [os.environ.get("COMSPEC", "cmd.exe"), "/c", *args]
        return args

    def _run(self, args: list[str], *, timeout: float) -> subprocess.CompletedProcess:
        env = self._env()
        try:
            return subprocess.run(  # noqa: S603 - explicit binary, no shell
                self._command(args),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.workspace or None,
                env=env,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            return subprocess.CompletedProcess(args, 1, "", f"{exc.__class__.__name__}: {exc}")

    def _prompt(self, messages: list[ChatMessage], system: str | None) -> str:
        parts: list[str] = []
        if system and system.strip():
            parts.append("System instructions for this conversation:\n" + system.strip())
        history = [message for message in messages if (message.content or "").strip()]
        if len(history) > 13:
            history = history[-13:]
        for message in history:
            label = {
                "user": "User",
                "assistant": "Assistant",
                "system": "System",
                "tool": "Tool result",
            }.get(message.role, message.role.title())
            parts.append(f"{label}: {message.content.strip()}")
        parts.append(
            "Reply to the last message above as RAY, a helpful personal desktop assistant. "
            "Answer in plain text; do not change files or run commands."
        )
        prompt = "\n\n".join(parts)
        if len(prompt) > MAX_PROMPT_CHARS:
            prompt = prompt[:400] + "\n\n[...earlier conversation trimmed...]\n\n" + prompt[-MAX_PROMPT_CHARS + 600 :]
        return prompt

    def _extract(self, stdout: str) -> str:
        raw = self._clean(stdout)
        if not raw:
            raise ProviderError("cursor: the CLI returned no text")
        text = raw
        if self.output_format in ("json", "stream-json") or raw.lstrip().startswith("{"):
            parsed = self._parse_json(raw)
            if parsed:
                text = parsed
        if not text.strip():
            raise ProviderError("cursor: the CLI returned an empty answer")
        return text.strip()

    def _parse_json(self, raw: str) -> str:
        candidates: list[dict[str, Any]] = []
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                candidates.append(payload)
            elif isinstance(payload, list):
                candidates.extend(item for item in payload if isinstance(item, dict))
        except json.JSONDecodeError:
            for line in raw.splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    candidates.append(item)
        for payload in reversed(candidates):
            found = self._find_text(payload)
            if found:
                return found
        return ""

    def _find_text(self, payload: dict[str, Any], depth: int = 0) -> str:
        if depth > 3:
            return ""
        for key in JSON_TEXT_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                nested = self._find_text(value, depth + 1)
                if nested:
                    return nested
            if isinstance(value, list):
                chunks = []
                for item in value:
                    if isinstance(item, str) and item.strip():
                        chunks.append(item)
                    elif isinstance(item, dict):
                        nested = self._find_text(item, depth + 1)
                        if nested:
                            chunks.append(nested)
                if chunks:
                    return "\n".join(chunks)
        return ""

    def _failure(self, completed: subprocess.CompletedProcess) -> ProviderError:
        blob = f"{completed.stderr or ''}\n{completed.stdout or ''}".lower()
        clean = self._clean(f"{completed.stderr or ''}\n{completed.stdout or ''}")[:300]
        if any(word in blob for word in ("not logged", "login", "unauthorized", "401", "invalid api key", "authenticat")):
            return ProviderAuthError(
                "cursor: sign in with 'cursor-agent login' or add a Cursor API key in Settings.",
                detail=clean,
            )
        if any(word in blob for word in ("rate limit", "quota", "usage limit", "429", "limit reached")):
            return ProviderQuotaError("cursor: the account hit a usage limit. Try again later.", detail=clean)
        return ProviderError(f"cursor: the CLI exited with code {completed.returncode}", detail=clean)

    @staticmethod
    def _clean(text: str) -> str:
        return ANSI.sub("", text or "").strip()
