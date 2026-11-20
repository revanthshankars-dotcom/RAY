"""Intelligent provider routing.

Not every request goes to every provider.  RAY classifies the work, picks the
best engine for it, falls back only when that engine is unavailable, and
records what happened.

    chat / reasoning / code             -> Cursor CLI first, Gemini fallback
    planning / tool selection           -> Gemini first (native tool calls)
    documents / images / long analysis  -> Gemini first, Cursor fallback
    speech                              -> Fish Audio only

All failures surface as ``ProviderError`` subclasses with a message a human can
act on.  Nothing is invented when a provider is down.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterable

from ..core.config import Config
from ..core.database import Database
from ..core.events import EventBus, bus as default_bus
from ..core.logging_setup import get_logger
from ..security.credentials import CredentialStore
from .base import (
    ChatMessage,
    ChatResult,
    HealthStatus,
    ProviderAuthError,
    ProviderError,
    ProviderQuotaError,
    ProviderUnavailable,
)
from .cache import ResponseCache, UsageTracker
from .cursor import CursorProvider
from .fish import FishAudioProvider
from .gemini import GeminiProvider
from .grok import GrokProvider

log = get_logger("ai.router")

TASK_ROUTES: dict[str, list[str]] = {
    "chat": ["cursor", "gemini"],
    "reasoning": ["cursor", "gemini"],
    "code": ["cursor", "gemini"],
    # the planner needs native function calling, which the CLI cannot provide
    "planning": ["gemini", "cursor"],
    "tool_selection": ["gemini", "cursor"],
    "document": ["gemini", "cursor"],
    "document_long": ["gemini"],
    "image": ["gemini"],
    "summarize": ["gemini", "cursor"],
    "research": ["gemini", "cursor"],
    "tts": ["fish"],
}

PROVIDER_CLASSES = {
    "cursor": CursorProvider,
    "grok": GrokProvider,
    "gemini": GeminiProvider,
    "fish": FishAudioProvider,
}

DEFAULT_CHAIN = ["cursor", "gemini"]

HEALTH_TTL_SECONDS = 45


class ProviderRouter:
    def __init__(
        self,
        db: Database,
        config: Config,
        credentials: CredentialStore,
        bus: EventBus | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.credentials = credentials
        self.bus = bus or default_bus
        self.cache = ResponseCache(db, int(config.get("ai.cache_ttl_seconds", 1800) or 1800))
        self.usage = UsageTracker(db)
        self._providers: dict[str, Any] = {}
        self._health: dict[str, HealthStatus] = {}
        self._lock = threading.RLock()
        config.on_change(lambda key, value: self.rebuild() if key.startswith("ai.") else None)

    # -------------------------------------------------------------- lifecycle
    def rebuild(self) -> None:
        with self._lock:
            self._providers = {}
            for name, provider_config in self.config.section("ai.providers").items():
                cls = PROVIDER_CLASSES.get(name)
                if not cls:
                    continue
                try:
                    key = self.credentials.get(name)
                except Exception as exc:  # decryption failure should not kill the app
                    log.error("credential read failed for %s: %s", name, exc)
                    key = None
                self._providers[name] = cls(provider_config, key)
            self._health.clear()

    def provider(self, name: str):
        with self._lock:
            if name not in self._providers:
                self.rebuild()
            return self._providers.get(name)

    def providers(self) -> dict[str, Any]:
        with self._lock:
            if not self._providers:
                self.rebuild()
            return dict(self._providers)

    # ------------------------------------------------------------------ health
    def health(self, refresh: bool = False) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name, provider in self.providers().items():
            cached = self._health.get(name)
            if not refresh and cached and (time.time() - cached.checked_at) < HEALTH_TTL_SECONDS:
                out[name] = cached.as_dict()
                continue
            available, detail = provider.availability()
            if not available:
                status = HealthStatus(name, False, detail)
            elif refresh:
                status = provider.test_connection()
                self.credentials.record_test(name, status.available, status.detail)
                if status.available:
                    self.cache.clear()  # config may have changed under us
            else:
                status = HealthStatus(name, True, "credentials present (not probed)")
            self._health[name] = status
            out[name] = status.as_dict()
        return out

    def available_providers(self) -> list[str]:
        return [name for name, provider in self.providers().items() if provider.availability()[0]]

    def is_offline(self) -> bool:
        return not self.available_providers()

    # ------------------------------------------------------------------ budget
    def _budget_ok(self, provider: str) -> tuple[bool, str]:
        budget = int(self.config.get(f"ai.daily_budget.{provider}", 0) or 0)
        if budget <= 0:
            return True, ""
        used = self.usage.calls_today(provider)
        if used >= budget:
            return False, f"daily call budget for {provider} reached ({used}/{budget})"
        return True, ""

    # ------------------------------------------------------------------- chat
    def resolve_chain(self, kind: str, preferred: str | None = None) -> list[str]:
        if preferred:
            chain = [preferred] + [p for p in TASK_ROUTES.get(kind, DEFAULT_CHAIN) if p != preferred]
        else:
            chain = list(TASK_ROUTES.get(kind, DEFAULT_CHAIN))
        default = str(self.config.get("ai.default_provider", "cursor") or "cursor")
        if default not in chain and default != "fish":
            chain.append(default)
        return chain

    def chat(
        self,
        kind: str,
        messages: Iterable[ChatMessage],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        provider: str | None = None,
        use_cache: bool = False,
        cache_ttl: int | None = None,
        inline_files: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        message_list = list(messages)
        chain = self.resolve_chain(kind, provider)
        errors: list[str] = []
        cache_key = None
        if use_cache:
            cache_key = ResponseCache.make_key("", "", message_list, extra=f"{kind}:{system or ''}")
            cached = self.cache.get(cache_key)
            if cached:
                self.usage.record(provider or chain[0], kind=kind, cached=True)
                return ChatResult(text=cached, provider="cache", model="cached", cached=True)

        for name in chain:
            engine = self.provider(name)
            if engine is None:
                errors.append(f"{name}: unknown provider")
                continue
            available, detail = engine.availability()
            if not available:
                errors.append(f"{name}: {detail}")
                continue
            ok, budget_detail = self._budget_ok(name)
            if not ok:
                errors.append(f"{name}: {budget_detail}")
                continue
            supports_files = getattr(engine, "supports_documents", False)
            if inline_files and not supports_files:
                errors.append(f"{name}: cannot read inline documents")
                continue
            if tools and not getattr(engine, "supports_tools", False):
                errors.append(f"{name}: cannot call RAY tools")
                continue
            try:
                result = engine.chat(
                    message_list,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system=system,
                    **({"inline_files": inline_files} if inline_files and supports_files else {}),
                )
            except ProviderAuthError as exc:
                errors.append(f"{name}: {exc.message}")
                self.usage.record(name, engine.model, kind, ok=False, error=exc.message)
                self.bus.publish("ai_error", provider=name, message=exc.message, kind=kind)
                continue
            except ProviderQuotaError as exc:
                errors.append(f"{name}: {exc.message}")
                self.usage.record(name, engine.model, kind, ok=False, error=exc.message)
                self.bus.publish("ai_error", provider=name, message=exc.message, kind=kind)
                continue
            except ProviderError as exc:
                errors.append(f"{name}: {exc.message}")
                self.usage.record(name, engine.model, kind, ok=False, error=exc.message)
                self.bus.publish("ai_error", provider=name, message=exc.message, kind=kind)
                continue
            except Exception as exc:  # unexpected - keep the app alive
                log.exception("unexpected provider failure (%s)", name)
                errors.append(f"{name}: unexpected error ({exc.__class__.__name__})")
                continue

            self.usage.record(
                name,
                result.model,
                kind,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                ok=True,
            )
            if cache_key and result.text:
                self.cache.put(cache_key, result.text, name, result.model, cache_ttl)
            if len(errors):
                log.info("routed '%s' to %s after fallback (%s)", kind, name, "; ".join(errors[:2]))
            return result

        message = "No AI provider could handle this request. " + " | ".join(errors[:4])
        if not self.available_providers():
            message = (
                "No AI provider is configured yet. Install the Cursor CLI (cursor.com/cli) or add a "
                "Gemini, Grok or Fish Audio key in Settings > AI Providers. Local features (files, "
                "tasks, notes, system monitor, automation) keep working offline."
            )
        raise ProviderUnavailable(message)

    async def achat(self, kind: str, messages: Iterable[ChatMessage], **kwargs: Any) -> ChatResult:
        import asyncio

        return await asyncio.to_thread(self.chat, kind, list(messages), **kwargs)

    # -------------------------------------------------------------------- tts
    def speak(self, text: str, *, voice_id: str | None = None, speed: float | None = None) -> tuple[bytes, str]:
        engine = self.provider("fish")
        if engine is None:
            raise ProviderUnavailable("Fish Audio provider is not configured")
        audio, fmt, latency = engine.synthesize(text, reference_id=voice_id, speed=speed)
        self.usage.record("fish", engine.model, "tts", characters=len(text), ok=True)
        log.info("synthesised %s characters of speech in %sms", len(text), latency)
        return audio, fmt

    def voices(self) -> list[dict[str, Any]]:
        engine = self.provider("fish")
        if engine is None:
            raise ProviderUnavailable("Fish Audio provider is not configured")
        return engine.list_voices()

    # ------------------------------------------------------------------ probes
    def test_provider(self, name: str) -> dict[str, Any]:
        engine = self.provider(name)
        if engine is None:
            return {"provider": name, "available": False, "detail": "unknown provider", "latency_ms": None}
        status = engine.test_connection()
        self.credentials.record_test(name, status.available, status.detail)
        self._health[name] = status
        self.bus.publish(
            "provider_test",
            provider=name,
            available=status.available,
            detail=status.detail,
            latency_ms=status.latency_ms,
        )
        return status.as_dict()

    def describe(self) -> dict[str, Any]:
        return {
            "providers": [provider.describe() for provider in self.providers().values()],
            "routes": TASK_ROUTES,
            "offline": self.is_offline(),
            "cache": self.cache.stats(),
            "usage_today": self.usage.today(),
        }
