"""Engine selection and credit caps - a rule, not a vibe.

RAY can talk to more than one engine (OpenRouter, Gemini, Fish for speech). Which
one answers a given request is decided here, once, in a place that can be read,
tested and audited - never inside a provider adapter.

The rule, in order:

1. **Preference.** A per-request ``engine`` wins. Otherwise the pinned engine
   (``ai.pinned_engine``), otherwise the configured order (``ai.engine_order``).
   Routes that need tool calling or document reading keep their own ranking
   (planning prefers Gemini, chat prefers OpenRouter) and the preference only
   decides between engines that are equally suitable.
2. **Capability.** An engine that cannot do the job (no tool calling, no
   document reading) is filtered out, with a reason.
3. **Credit cap.** Each engine may have a local monthly cap
   (``ai.credits.<engine>.monthly_usd`` and/or ``monthly_calls``). Spend is
   measured from the ``usage`` table, so the cap is enforced on this machine
   even if the provider never says a word. ``ai.cap_behaviour`` decides what
   happens when the *preferred* engine is capped:

   * ``stop``   - do not spend elsewhere behind the user's back; return a message
     naming the engine and the cap that was hit.
   * ``switch`` - move to the next engine that still has credit, and record the
     switch (audit row ``ai_engine_switch``).

4. **Health.** An engine with no key, or one that just failed, is skipped and
   the next candidate is tried. This is the existing fallback, unchanged -
   availability is not the same thing as a spending decision.

Nothing here grants a permission: an engine change never changes what a tool is
allowed to do (see SECURITY.md, "Engine selection and credit caps").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

CHAT_ENGINES: tuple[str, ...] = ("openrouter", "gemini", "ollama")
SPEECH_ENGINES: tuple[str, ...] = ("fish",)
ENGINES: tuple[str, ...] = CHAT_ENGINES + SPEECH_ENGINES

#: Engines that are a process on this machine rather than an account somewhere.
#: They have no key, cost nothing, and keep working with the network unplugged.
LOCAL_ENGINES: tuple[str, ...] = ("ollama",)

ENGINE_LABELS = {
    "openrouter": "OpenRouter",
    "gemini": "Gemini",
    "ollama": "Ollama (local)",
    "fish": "Fish Audio",
}

POLICIES = ("preference", "credits", "manual")
CAP_BEHAVIOURS = ("stop", "switch")


# --------------------------------------------------------------------- results
@dataclass
class Blocked:
    engine: str
    code: str  # "no_key" | "unauthorised" | "not_text" | "no_tools" | "no_files" | "cap" | "unknown"
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"engine": self.engine, "code": self.code, "reason": self.reason}


@dataclass
class Switch:
    """A decision to move from one engine to another, with the reason."""

    source: str
    target: str
    code: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "from": self.source,
            "to": self.target,
            "code": self.code,
            "reason": self.reason,
        }


@dataclass
class RoutePlan:
    kind: str
    policy: str
    cap_behaviour: str
    order: list[str] = field(default_factory=list)
    usable: list[str] = field(default_factory=list)
    blocked: list[Blocked] = field(default_factory=list)
    switches: list[Switch] = field(default_factory=list)
    halt: str = ""
    requested: str = ""

    @property
    def primary(self) -> str:
        return self.usable[0] if self.usable else ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "policy": self.policy,
            "cap_behaviour": self.cap_behaviour,
            "order": self.order,
            "usable": self.usable,
            "primary": self.primary,
            "blocked": [item.as_dict() for item in self.blocked],
            "switches": [item.as_dict() for item in self.switches],
            "halt": self.halt,
            "requested": self.requested,
        }


class EnginePolicy:
    """Reads config + spend and answers "which engine should take this?"."""

    def __init__(self, config, usage=None, credentials=None, fallback_order: Iterable[str] | None = None) -> None:
        self.config = config
        self.usage = usage
        self.credentials = credentials
        self._fallback_order = list(fallback_order or CHAT_ENGINES)

    # ------------------------------------------------------------ config bits
    def name(self) -> str:
        value = str(self.config.get("ai.engine_policy", "preference") or "preference").lower()
        return value if value in POLICIES else "preference"

    def cap_behaviour(self) -> str:
        value = str(self.config.get("ai.cap_behaviour", "stop") or "stop").lower()
        return value if value in CAP_BEHAVIOURS else "stop"

    def pinned(self) -> str:
        value = str(self.config.get("ai.pinned_engine", "") or "").strip().lower()
        return value if value in CHAT_ENGINES else ""

    def preference(self) -> list[str]:
        """The user's order, cleaned: known chat engines only, no duplicates."""
        raw = self.config.get("ai.engine_order", []) or []
        order: list[str] = []
        for item in raw if isinstance(raw, (list, tuple)) else [raw]:
            engine = str(item or "").strip().lower()
            if engine in CHAT_ENGINES and engine not in order:
                order.append(engine)
        for engine in self._fallback_order:
            if engine not in order:
                order.append(engine)
        # The local engine goes last by default: it is the safety net that keeps
        # RAY answering when the internet, the key or the credit runs out.
        if self.local_fallback_enabled():
            for engine in LOCAL_ENGINES:
                if engine not in order:
                    order.append(engine)
        return order

    def local_fallback_enabled(self) -> bool:
        return bool(self.config.get("ai.local_fallback", True))

    def cap_for(self, engine: str) -> dict[str, float]:
        raw = self.config.get(f"ai.credits.{engine}", {}) or {}
        if not isinstance(raw, dict):
            return {"monthly_usd": 0.0, "monthly_calls": 0.0}
        return {
            "monthly_usd": _positive(raw.get("monthly_usd")),
            "monthly_calls": _positive(raw.get("monthly_calls")),
        }

    # ------------------------------------------------------------- spend data
    def spend_for(self, engine: str) -> dict[str, Any]:
        if self.usage is None:
            return {"cost_usd": 0.0, "calls": 0, "reported_calls": 0}
        try:
            return self.usage.month_spend(engine)
        except Exception:  # pragma: no cover - a broken meter must not break chat
            return {"cost_usd": 0.0, "calls": 0, "reported_calls": 0}

    # ---------------------------------------------------------------- engines
    def credits(self, engine: str) -> dict[str, Any]:
        cap = self.cap_for(engine)
        spend = self.spend_for(engine)
        spent_usd = round(float(spend.get("cost_usd") or 0.0), 4)
        used_calls = int(spend.get("calls") or 0)
        capped_by: list[str] = []
        if cap["monthly_usd"] and spent_usd >= cap["monthly_usd"]:
            capped_by.append("usd")
        if cap["monthly_calls"] and used_calls >= cap["monthly_calls"]:
            capped_by.append("calls")
        usd_percent = _percent(spent_usd, cap["monthly_usd"])
        calls_percent = _percent(used_calls, cap["monthly_calls"])
        percent = max(usd_percent, calls_percent)
        return {
            "engine": engine,
            "label": ENGINE_LABELS.get(engine, engine),
            "monthly_usd": cap["monthly_usd"],
            "monthly_calls": cap["monthly_calls"],
            "spent_usd": spent_usd,
            "used_calls": used_calls,
            "estimated": bool(spend.get("estimated")),
            "usd_percent": usd_percent,
            "calls_percent": calls_percent,
            "percent": percent,
            "capped": bool(capped_by),
            "capped_by": capped_by,
            "cap_reason": self._cap_message(engine, cap, spent_usd, used_calls, capped_by)
            if capped_by
            else "",
        }

    def configured(self, engine: str) -> tuple[bool, str]:
        if engine in LOCAL_ENGINES:
            # There is no account to have a key for. Whether the daemon is
            # actually running is a health question, answered by the router.
            return True, "local"
        source = "unset"
        if self.credentials is not None:
            try:
                source = str(self.credentials.source(engine))
            except Exception:  # pragma: no cover - defensive
                source = "unset"
            if source != "unset":
                return True, "vault" if source == "vault" else source
        return False, source

    def state(self, engine: str, health: dict[str, dict] | None = None) -> dict[str, Any]:
        ok, source = self.configured(engine)
        probe = (health or {}).get(engine) or {}
        detail = str(probe.get("detail") or "")
        if "available" in probe:
            # The router has a real provider object for this engine: it knows
            # better than the credential table (a key can be absent while the
            # engine still works through another route).
            available = bool(probe.get("available"))
            ok = ok or available
        elif engine in LOCAL_ENGINES:
            # No probe means nobody has asked the daemon yet. A local engine
            # must never be assumed to be running.
            available = False
            detail = detail or "the local engine has not been checked yet"
        else:
            available = ok
        credit = self.credits(engine)
        return {
            "name": engine,
            "label": ENGINE_LABELS.get(engine, engine),
            "configured": ok,
            "local": engine in LOCAL_ENGINES,
            "key_source": source,
            "available": available and ok,
            "detail": detail,
            # A local model is not assumed to handle tool calling: that is a
            # property of the model, and small ones get it wrong. Opt-in only.
            "supports_tools": bool(
                probe.get("supports_tools", engine in CHAT_ENGINES and engine not in LOCAL_ENGINES)
            ),
            "supports_documents": bool(probe.get("supports_documents", engine == "gemini")),
            "model": str(probe.get("model") or ""),
            "credit": credit,
            "capped": bool(credit["capped"]),
            "cap_reason": str(credit["cap_reason"] or ""),
        }

    def engines(self, health: dict[str, dict] | None = None) -> list[dict[str, Any]]:
        return [self.state(engine, health) for engine in ENGINE_LABELS]

    # ----------------------------------------------------------------- chain
    def chain(
        self,
        kind: str,
        *,
        route: Iterable[str],
        preferred: str = "",
        needs_tools: bool = False,
        needs_files: bool = False,
        health: dict[str, dict] | None = None,
    ) -> RoutePlan:
        """The engines that may take this request, best first, with the reasons."""
        policy = self.name()
        behaviour = self.cap_behaviour()
        requested = (preferred or "").strip().lower()
        plan = RoutePlan(kind=kind, policy=policy, cap_behaviour=behaviour, requested=requested)

        candidates = self._candidates(list(route), policy, requested, health)
        plan.order = candidates

        skipped_for_cap: list[str] = []
        for engine in candidates:
            state = self.state(engine, health)
            if not state["configured"]:
                plan.blocked.append(Blocked(engine, "no_key", f"{state['label']} has no API key"))
                continue
            if not state["available"]:
                reason = state["detail"] or f"{state['label']} is not responding"
                code = "offline" if engine in LOCAL_ENGINES else "unauthorised"
                plan.blocked.append(Blocked(engine, code, reason))
                continue
            if needs_tools and not state["supports_tools"]:
                plan.blocked.append(
                    Blocked(engine, "no_tools", f"{state['label']} cannot call tools")
                )
                continue
            if needs_files and not state["supports_documents"]:
                plan.blocked.append(
                    Blocked(engine, "no_files", f"{state['label']} cannot read attached documents")
                )
                continue
            if state["capped"]:
                skipped_for_cap.append(engine)
                plan.blocked.append(Blocked(engine, "cap", state["cap_reason"]))
                if behaviour == "stop" and not plan.usable:
                    # The engine we would have used is over its cap. Stop and say
                    # so rather than quietly spending the user's other credit.
                    plan.halt = self._blocked_message(plan.blocked)
                    break
                continue
            plan.usable.append(engine)

        if plan.halt:
            plan.switches.clear()
            return plan

        if skipped_for_cap and plan.usable and behaviour == "switch":
            source = skipped_for_cap[0]
            if candidates.index(source) < candidates.index(plan.usable[0]):
                plan.switches.append(
                    Switch(
                        source=source,
                        target=plan.usable[0],
                        code="cap",
                        reason=self.credits(source)["cap_reason"],
                    )
                )
        if not plan.usable:
            # Nothing can take it: say exactly why for each engine, not just the
            # first one that failed.
            plan.halt = self._blocked_message(plan.blocked)
        if policy == "manual" and plan.usable:
            # Manual means manual: the chosen engine answers, or nothing does.
            plan.usable = plan.usable[:1]
        return plan

    # -------------------------------------------------------------- internals
    def _candidates(
        self,
        route: list[str],
        policy: str,
        requested: str,
        health: dict[str, dict] | None,
    ) -> list[str]:
        ordered: list[str] = []
        for engine in route:
            name = str(engine or "").strip().lower()
            if name in CHAT_ENGINES and name not in ordered:
                ordered.append(name)
        for engine in self.preference():
            if engine not in ordered:
                ordered.append(engine)

        if policy == "credits":
            # Most headroom first (lowest share of the cap used); the route
            # order breaks ties and a capped engine sorts last. The local engine
            # is deliberately *after* every cloud engine: "save my credit"
            # must not mean "spend it" - the free engine is the safety net, not
            # the first choice.
            def headroom(engine: str) -> float:
                if engine in LOCAL_ENGINES:
                    return 2000.0
                credit = self.credits(engine)
                if credit["capped"]:
                    return 1000.0
                return credit["percent"]

            ordered.sort(key=headroom)

        front = requested if requested in CHAT_ENGINES else self.pinned()
        if front:
            ordered = [front] + [engine for engine in ordered if engine != front]
        return ordered

    def _cap_message(
        self,
        engine: str,
        cap: dict[str, float],
        spent_usd: float,
        used_calls: int,
        capped_by: list[str],
    ) -> str:
        label = ENGINE_LABELS.get(engine, engine)
        if "usd" in capped_by:
            return (
                f"{label} reached its monthly cap "
                f"(${spent_usd:.2f} of ${cap['monthly_usd']:.2f} spent)"
            )
        return (
            f"{label} reached its monthly call cap "
            f"({used_calls} of {int(cap['monthly_calls'])} calls)"
        )

    def _blocked_message(self, blocked: list[Blocked]) -> str:
        """Why nothing can take the request, engine by engine, with a next step."""
        reasons = "; ".join(item.reason.rstrip(". ") for item in blocked[:3]) or "no engine is configured"
        codes = {item.code for item in blocked}
        if codes == {"cap"}:
            hint = (
                "Raise a cap in Settings -> AI providers, pin another engine, "
                "or set the cap behaviour to 'switch'."
            )
        elif "offline" in codes and codes <= {"cap", "offline", "no_key"}:
            # Nothing cloud-shaped answered and the local engine is not running.
            hint = (
                "Add a provider key, or start Ollama on this machine (`ollama serve`) "
                "so RAY can keep working without one."
            )
        else:
            hint = (
                "Add a key in Settings -> AI providers, or start Ollama on this machine "
                "(`ollama serve`) to work offline."
            )
        return f"No engine can take this request: {reasons}. {hint}"

    # --------------------------------------------------------------- describe
    def describe(self, health: dict[str, dict] | None = None) -> dict[str, Any]:
        return {
            "policy": self.name(),
            "cap_behaviour": self.cap_behaviour(),
            "pinned": self.pinned(),
            "preference": self.preference(),
            "credits": {engine: self.credits(engine) for engine in CHAT_ENGINES},
            "engines": self.engines(health),
        }


# ------------------------------------------------------------------- helpers
def _positive(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _percent(used: float, cap: float) -> float:
    if not cap:
        return 0.0
    return round(min(100.0, max(0.0, (float(used) / float(cap)) * 100.0)), 1)
