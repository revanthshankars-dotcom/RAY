"""Intelligent engine routing.

Not every request goes to every engine.  RAY classifies the work, asks
:class:`ray.ai.policy.EnginePolicy` which engines may take it, falls back only
when that engine is unavailable, and records what happened.

    chat / reasoning / code / research  -> OpenRouter first, Gemini fallback,
                                           then Ollama on this machine
    planning / tool selection           -> Gemini first (native tool calls)
    documents / images / long analysis  -> Gemini first (inline documents)
    speech                              -> Fish Audio only

Which engine actually answers is decided by the policy (preference, capability,
local credit cap, health) - never by an adapter.  Every switch is written to the
audit log as ``ai_engine_switch`` and published on the bus, so a change of
engine is always visible after the fact.

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
from ..security.audit import AuditLog
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
from .fish import FishAudioProvider
from .gemini import GeminiProvider
from .ollama import OllamaProvider
from .openrouter import OpenRouterProvider
from .policy import ENGINE_LABELS, EnginePolicy, RoutePlan

log = get_logger("ai.router")

TASK_ROUTES: dict[str, list[str]] = {
    "chat": ["openrouter", "gemini"],
    "reasoning": ["openrouter", "gemini"],
    "code": ["openrouter", "gemini"],
    # Gemini keeps the planning route: native function calling, and it is the
    # engine that can read attached documents in the same call.
    "planning": ["gemini", "openrouter"],
    "tool_selection": ["gemini", "openrouter"],
    "document": ["gemini", "openrouter"],
    "document_long": ["gemini"],
    "image": ["gemini"],
    "summarize": ["gemini", "openrouter"],
    "research": ["openrouter", "gemini"],
    "tts": ["fish"],
}

PROVIDER_CLASSES = {
    "openrouter": OpenRouterProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
    "fish": FishAudioProvider,
}

DEFAULT_CHAIN = ["openrouter", "gemini"]

# Kinds where the user's engine order is the ranking (the route in TASK_ROUTES
# is only the default). Planning and document work keep their own ranking.
GENERIC_KINDS = {"chat", "reasoning", "code", "research", "summarize"}

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
        self.audit = AuditLog(db)
        self.policy = EnginePolicy(config, self.usage, credentials, fallback_order=DEFAULT_CHAIN)
        self.last_plan: RoutePlan | None = None
        # (kind, source, target) pairs already audited; cleared when config
        # changes so a recurring switch is not logged on every single request.
        self._switch_seen: set[tuple[str, str, str]] = set()
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
            self._switch_seen.clear()

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
            out[name] = self._health_entry(name, provider, status)
        return out

    @staticmethod
    def _health_entry(name: str, provider: Any, status: HealthStatus) -> dict[str, Any]:
        """Health plus the capability facts the engine policy reasons about."""
        entry = status.as_dict()
        entry["name"] = name
        entry["ok"] = status.available
        entry["model"] = str(getattr(provider, "model", "") or "")
        entry["supports_tools"] = bool(getattr(provider, "supports_tools", False))
        entry["supports_documents"] = bool(getattr(provider, "supports_documents", False))
        return entry

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
    def route_for(self, kind: str) -> list[str]:
        """The ranking for a kind of work, before capability and caps are applied.

        General chat follows the user's order (so "Gemini first" means exactly
        that); document and planning work keeps its own ranking because those
        jobs need inline-file support and native tool calls respectively.
        """
        if kind in GENERIC_KINDS:
            return self.policy.preference()
        chain = list(TASK_ROUTES.get(kind, DEFAULT_CHAIN))
        # Offline safety net: after the engines that suit this job best, a local
        # model is still better than an error. Speech is excluded - a text model
        # cannot talk.
        if kind != "tts" and self.policy.local_fallback_enabled() and "ollama" not in chain:
            chain.append("ollama")
        return chain

    def plan(
        self,
        kind: str,
        *,
        preferred: str | None = None,
        needs_tools: bool = False,
        needs_files: bool = False,
        health: dict[str, dict[str, Any]] | None = None,
    ) -> RoutePlan:
        """Ask the engine policy which engines may serve this request, and why."""
        plan = self.policy.chain(
            kind,
            route=self.route_for(kind),
            preferred=preferred or "",
            needs_tools=needs_tools,
            needs_files=needs_files,
            health=self.health() if health is None else health,
        )
        self.last_plan = plan
        return plan

    def resolve_chain(
        self,
        kind: str,
        preferred: str | None = None,
        *,
        needs_tools: bool = False,
        needs_files: bool = False,
    ) -> list[str]:
        """Engines that may take this request, best first (caps applied)."""
        return self.plan(
            kind, preferred=preferred, needs_tools=needs_tools, needs_files=needs_files
        ).usable

    def audit_switch(
        self,
        kind: str,
        source: str,
        target: str,
        *,
        code: str = "cap",
        reason: str = "",
        policy: str = "",
    ) -> None:
        """One audit row + one bus event for one engine change. Never raises."""
        try:
            self.audit.record(
                "ai_engine_switch",
                tool="ai",
                target=f"{source} -> {target}",
                level=1,
                result="ok",
                detail={"reason": reason, "code": code, "kind": kind, "policy": policy},
            )
        except Exception as exc:  # pragma: no cover - audit must not block chat
            log.error("could not audit engine switch: %s", exc)
        self.bus.publish(
            "ai_engine_switch", source=source, target=target, code=code, reason=reason, kind=kind
        )
        log.info("engine switch %s -> %s for '%s' (%s)", source, target, kind, reason)

    def record_switch(self, plan: RoutePlan) -> list[tuple[str, str]]:
        """Audit the switches the policy decided; return the pairs recorded."""
        pairs: list[tuple[str, str]] = []
        for switch in plan.switches:
            key = (plan.kind, switch.source, switch.target)
            if key in self._switch_seen:
                pairs.append((switch.source, switch.target))
                continue
            self._switch_seen.add(key)
            self.audit_switch(
                plan.kind,
                switch.source,
                switch.target,
                code=switch.code,
                reason=switch.reason,
                policy=plan.policy,
            )
            pairs.append((switch.source, switch.target))
        return pairs

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
        plan = self.plan(
            kind,
            preferred=provider,
            needs_tools=bool(tools),
            needs_files=bool(inline_files),
        )
        audited = self.record_switch(plan)
        if plan.halt:
            raise ProviderUnavailable(plan.halt)
        chain = plan.usable
        errors: list[str] = [f"{item.engine}: {item.reason}" for item in plan.blocked]
        cache_key = None
        if use_cache:
            cache_key = ResponseCache.make_key("", "", message_list, extra=f"{kind}:{system or ''}")
            cached = self.cache.get(cache_key)
            if cached:
                self.usage.record(provider or (chain[0] if chain else "cache"), kind=kind, cached=True)
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
                cost_usd=getattr(result, "cost_usd", 0.0),
            )
            if cache_key and result.text:
                self.cache.put(cache_key, result.text, name, result.model, cache_ttl)
            if errors:
                # What the user expected to answer is the head of the order, not
                # necessarily chain[0]: when the preferred engine was already
                # unusable the chain starts at its replacement, and that change
                # still has to be visible in the audit log.
                expected = plan.order[0] if plan.order else chain[0]
                if expected != name and (expected, name) not in audited:
                    blocked = next(
                        (item.reason for item in plan.blocked if item.engine == expected), ""
                    )
                    self.audit_switch(
                        kind,
                        expected,
                        name,
                        code="fallback",
                        reason=blocked
                        or "; ".join(item for item in errors[:2] if item.startswith(expected))
                        or errors[0],
                        policy=plan.policy,
                    )
                log.info("routed '%s' to %s after fallback (%s)", kind, name, "; ".join(errors[:2]))
            return result

        message = "No AI provider could handle this request. " + " | ".join(errors[:4])
        if not self.available_providers():
            message = (
                "No AI provider is configured yet. Add an OpenRouter key (one key, many models), "
                "a Gemini key in Settings > AI Providers, or start Ollama on this machine "
                "(`ollama serve`) to answer locally with no key at all. Local features (files, "
                "tasks, notes, system monitor, automation) keep working offline regardless."
            )
        elif not chain:
            message = "No engine may take this request. " + " | ".join(errors[:4])
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

    # ----------------------------------------------------------- engine views
    def routes(self) -> dict[str, list[str]]:
        """The effective chain per kind - including the local safety net."""
        return {kind: self.route_for(kind) for kind in TASK_ROUTES}

    def engine_summary(self, health: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """What the Home screen needs: which engine answers, and how close to the cap."""
        health = self.health() if health is None else health
        plan = self.plan("chat", health=health)
        primary = plan.primary
        credit = self.policy.credits(primary) if primary else {}
        return {
            "primary": primary,
            "label": ENGINE_LABELS.get(primary, primary or "none"),
            "chain": plan.usable,
            "policy": plan.policy,
            "cap_behaviour": plan.cap_behaviour,
            "halt": plan.halt,
            "blocked": [
                item.as_dict() for item in plan.blocked if item.code in ("cap", "no_key", "offline")
            ],
            "percent": credit.get("percent", 0.0),
            "spent_usd": credit.get("spent_usd", 0.0),
            "monthly_usd": credit.get("monthly_usd", 0.0),
            "used_calls": credit.get("used_calls", 0),
            "monthly_calls": credit.get("monthly_calls", 0),
            "capped": bool(credit.get("capped", False)),
        }

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
