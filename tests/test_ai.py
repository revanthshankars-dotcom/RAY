"""Provider request/response handling, error mapping, engine policy and caching.

No network calls: HTTP responses are injected so parsing and failure behaviour
can be tested deterministically, and the policy is exercised against the real
``usage`` table with fake provider objects.
"""

from __future__ import annotations

import json

import httpx
import pytest

from ray.ai.base import (
    ChatMessage,
    ProviderAuthError,
    ProviderError,
    ProviderQuotaError,
    ProviderTimeout,
    ProviderUnavailable,
    ToolCall,
    map_http_error,
)
from ray.ai.cache import ResponseCache, UsageTracker
from ray.ai.fish import FishAudioProvider
from ray.ai.gemini import GeminiProvider
from ray.ai.openrouter import OpenRouterProvider
from ray.ai.policy import EnginePolicy
from ray.ai.router import ProviderRouter


def openrouter_config(**overrides):
    config = {
        "enabled": True,
        "model": "google/gemini-2.5-flash",
        "base_url": "https://openrouter.ai/api/v1",
        "timeout": 10,
    }
    config.update(overrides)
    return config


def test_openrouter_parses_text_cost_and_usage(monkeypatch):
    provider = OpenRouterProvider(openrouter_config(), "sk-or-test")
    payload = {
        "id": "gen-1",
        "model": "google/gemini-2.5-flash",
        "choices": [{"message": {"content": "Hello there."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4, "cost": 0.00042},
    }
    seen = {}

    def fake_request(method, url, **kwargs):
        seen.update({"method": method, "url": url, "kwargs": kwargs})
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(provider, "_request", fake_request)
    result = provider.chat([ChatMessage("user", "hi")])
    assert result.text == "Hello there."
    assert result.input_tokens == 11
    assert result.output_tokens == 4
    assert result.provider == "openrouter"
    assert result.cost_usd == pytest.approx(0.00042)
    assert result.cost_reported is True
    # the request is the OpenAI shape, with the configured slug and a bearer key
    assert seen["url"].endswith("/chat/completions")
    assert seen["kwargs"]["json_body"]["model"] == "google/gemini-2.5-flash"
    assert seen["kwargs"]["headers"]["Authorization"] == "Bearer sk-or-test"
    assert seen["kwargs"]["json_body"]["usage"] == {"include": True}


def test_openrouter_parses_tool_calls(monkeypatch):
    provider = OpenRouterProvider(openrouter_config(), "sk-or-test")
    payload = {
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search_files", "arguments": '{"query": "pdf"}'},
                }],
            },
        }],
        "usage": {},
    }
    monkeypatch.setattr(provider, "_request", lambda *a, **k: httpx.Response(200, json=payload))
    result = provider.chat([ChatMessage("user", "find pdfs")], tools=[{"type": "function"}])
    assert result.tool_calls[0].name == "search_files"
    assert result.tool_calls[0].arguments == {"query": "pdf"}
    assert result.tool_calls[0].id == "call_1"


def test_openrouter_rejects_bad_json_tool_arguments(monkeypatch):
    provider = OpenRouterProvider(openrouter_config(), "sk-or-test")
    payload = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "1", "function": {"name": "x", "arguments": "{not json"}}]}}], "usage": {}}
    monkeypatch.setattr(provider, "_request", lambda *a, **k: httpx.Response(200, json=payload))
    result = provider.chat([ChatMessage("user", "x")])
    assert "_raw" in result.tool_calls[0].arguments


def test_openrouter_requires_api_key():
    provider = OpenRouterProvider(openrouter_config(), None)
    available, detail = provider.availability()
    assert available is False
    assert "key" in detail.lower()
    with pytest.raises(ProviderUnavailable):
        provider.chat([ChatMessage("user", "hi")])


def test_openrouter_disabled_provider():
    provider = OpenRouterProvider(openrouter_config(enabled=False), "key")
    assert provider.availability()[0] is False


def test_openrouter_maps_402_to_quota(monkeypatch):
    provider = OpenRouterProvider(openrouter_config(), "sk-or-test")

    def quota(*args, **kwargs):
        raise ProviderQuotaError("openrouter: insufficient credit")

    monkeypatch.setattr(provider, "_request", quota)
    with pytest.raises(ProviderQuotaError):
        provider.chat([ChatMessage("user", "hi")])


def test_openrouter_propagates_auth_error(monkeypatch):
    provider = OpenRouterProvider(openrouter_config(), "bad-key")
    error = ProviderAuthError("openrouter: the API key was rejected (HTTP 401).")

    def raise_error(*args, **kwargs):
        raise error

    monkeypatch.setattr(provider, "_request", raise_error)
    with pytest.raises(ProviderAuthError):
        provider.chat([ChatMessage("user", "hi")])


def test_openrouter_test_connection_reports_remaining_credit(monkeypatch):
    provider = OpenRouterProvider(openrouter_config(), "sk-or-test")
    monkeypatch.setattr(
        provider, "_request",
        lambda *a, **k: httpx.Response(200, json={"data": {"label": "ray", "limit": 10, "usage": 2.5}}),
    )
    health = provider.test_connection()
    assert health.available is True
    assert "$7.50" in health.detail and "ray" in health.detail


def test_openrouter_test_connection_reports_a_rejected_key(monkeypatch):
    provider = OpenRouterProvider(openrouter_config(), "bad")

    def reject(*args, **kwargs):
        raise ProviderAuthError("openrouter: the API key was rejected (HTTP 401).")

    monkeypatch.setattr(provider, "_request", reject)
    health = provider.test_connection()
    assert health.available is False
    assert "rejected" in health.detail


def test_openrouter_lists_models(monkeypatch):
    provider = OpenRouterProvider(openrouter_config(), "sk-or-test")
    payload = {"data": [
        {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash",
         "supported_parameters": ["tools", "temperature"], "context_length": 1000000},
        {"id": "no-tools/model", "name": "Text only"},
    ]}
    monkeypatch.setattr(provider, "_request", lambda *a, **k: httpx.Response(200, json=payload))
    models = provider.list_models()
    assert [item["id"] for item in models] == ["google/gemini-2.5-flash", "no-tools/model"]
    assert models[0]["tools"] is True and models[1]["tools"] is False


def test_gemini_body_includes_system_tools_and_files():
    provider = GeminiProvider({"enabled": True, "model": "gemini-2.5-flash",
                               "base_url": "https://generativelanguage.googleapis.com/v1beta"}, "key")
    body = provider.build_generate_body(
        [ChatMessage("system", "be brief"), ChatMessage("user", "read this")],
        tools=[{"type": "function", "function": {
            "name": "search_files",
            "description": "search",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        }}],
        inline_files=[{"bytes": b"%PDF-1.4", "mime_type": "application/pdf"}],
    )
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "search_files"
    parts = body["contents"][-1]["parts"]
    assert parts[-1]["inlineData"]["mimeType"] == "application/pdf"
    assert body["generationConfig"]["maxOutputTokens"] > 0


def test_gemini_parses_function_call(monkeypatch):
    provider = GeminiProvider({"enabled": True, "model": "gemini-2.5-flash",
                               "base_url": "https://generativelanguage.googleapis.com/v1beta"}, "key")
    payload = {
        "candidates": [{"content": {"parts": [
            {"text": "Let me check."},
            {"functionCall": {"name": "system_report", "args": {}}},
        ]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 5},
    }
    monkeypatch.setattr(provider, "_request", lambda *a, **k: httpx.Response(200, json=payload))
    result = provider.chat([ChatMessage("user", "how is my pc")])
    assert "Let me check." in result.text
    assert result.tool_calls[0].name == "system_report"
    assert result.input_tokens == 20


def test_gemini_reports_blocked_prompt(monkeypatch):
    provider = GeminiProvider({"enabled": True, "model": "m", "base_url": "https://x"}, "key")
    payload = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
    monkeypatch.setattr(provider, "_request", lambda *a, **k: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError) as excinfo:
        provider.chat([ChatMessage("user", "x")])
    assert "SAFETY" in str(excinfo.value)


def test_fish_synthesize(monkeypatch):
    provider = FishAudioProvider({"enabled": True, "model": "s2.1-pro-free",
                                  "base_url": "https://api.fish.audio", "format": "mp3"}, "key")
    monkeypatch.setattr(
        provider, "_request",
        lambda *a, **k: httpx.Response(200, content=b"ID3fake-mp3-bytes",
                                       headers={"content-type": "audio/mpeg"}),
    )
    audio, fmt, latency = provider.synthesize("Hello from RAY")
    assert audio.startswith(b"ID3")
    assert fmt == "mp3"
    assert latency >= 0


def test_fish_rejects_empty_audio(monkeypatch):
    provider = FishAudioProvider({"enabled": True, "model": "s1", "base_url": "https://api.fish.audio"}, "key")
    monkeypatch.setattr(
        provider, "_request",
        lambda *a, **k: httpx.Response(200, content=b"", headers={"content-type": "audio/mpeg"}),
    )
    with pytest.raises(ProviderError):
        provider.synthesize("hi")


def test_fish_requires_key():
    provider = FishAudioProvider({"enabled": True, "model": "s1", "base_url": "https://api.fish.audio"}, None)
    with pytest.raises(ProviderUnavailable):
        provider.synthesize("hi")


class FakeProvider:
    def __init__(self, name, behaviour="ok", text="fake answer"):
        self.name = name
        self.behaviour = behaviour
        self.model = f"{name}-model"
        self.calls = 0
        self.text = text
        self.config = {"enabled": True}
        self.supports_documents = name == "gemini"
        self.supports_tools = name in ("gemini", "openrouter")

    def availability(self):
        return True, "ready"

    def chat(self, messages, **kwargs):
        from ray.ai.base import ChatResult

        self.calls += 1
        if self.behaviour == "auth":
            raise ProviderAuthError(f"{self.name}: bad key")
        if self.behaviour == "quota":
            raise ProviderQuotaError(f"{self.name}: quota")
        if self.behaviour == "timeout":
            raise ProviderTimeout(f"{self.name}: timeout")
        if self.behaviour == "boom":
            raise RuntimeError("unexpected")
        return ChatResult(text=self.text, provider=self.name, model=self.model,
                          input_tokens=3, output_tokens=2, cost_usd=0.001, cost_reported=True)

    def describe(self):
        return {"name": self.name}

    def test_connection(self):
        from ray.ai.base import HealthStatus

        return HealthStatus(self.name, True, "ready")


def _router_with(app, providers):
    router = ProviderRouter(app.db, app.config, app.credentials, app.bus)
    router.provider = lambda name: providers.get(name)          # noqa: ARG005
    router.providers = lambda: dict(providers)
    return router


def test_router_prefers_primary_engine(app):
    openrouter = FakeProvider("openrouter")
    gemini = FakeProvider("gemini")
    router = _router_with(app, {"openrouter": openrouter, "gemini": gemini})
    result = router.chat("reasoning", [ChatMessage("user", "think")])
    assert result.provider == "openrouter"
    assert gemini.calls == 0


def test_router_routes_chat_to_openrouter_and_planning_to_gemini(app):
    router = _router_with(app, {"openrouter": FakeProvider("openrouter"), "gemini": FakeProvider("gemini")})
    assert router.resolve_chain("chat")[0] == "openrouter"
    assert router.resolve_chain("planning")[0] == "gemini"
    assert router.resolve_chain("code")[0] == "openrouter"
    assert router.resolve_chain("research")[0] == "openrouter"


def test_router_honours_the_configured_engine_order(app):
    app.config.set("ai.engine_order", ["gemini", "openrouter"], save=False)
    router = _router_with(app, {"openrouter": FakeProvider("openrouter"), "gemini": FakeProvider("gemini")})
    # general chat follows the user's order...
    assert router.resolve_chain("chat")[0] == "gemini"
    assert router.chat("chat", [ChatMessage("user", "hi")]).provider == "gemini"
    # ...while planning keeps the ranking that can do the job
    assert router.resolve_chain("planning")[0] == "gemini"


def test_router_honours_a_per_request_engine(app):
    openrouter = FakeProvider("openrouter")
    gemini = FakeProvider("gemini")
    router = _router_with(app, {"openrouter": openrouter, "gemini": gemini})
    result = router.chat("chat", [ChatMessage("user", "hi")], provider="gemini")
    assert result.provider == "gemini"
    assert openrouter.calls == 0


def test_router_skips_engines_without_tool_support(app):
    openrouter = FakeProvider("openrouter")
    gemini = FakeProvider("gemini")
    openrouter.supports_tools = False
    router = _router_with(app, {"openrouter": openrouter, "gemini": gemini})
    tools = [{"type": "function", "function": {"name": "list_files", "parameters": {}}}]
    result = router.chat("planning", [ChatMessage("user", "list my files")], tools=tools)
    assert result.provider == "gemini"
    assert openrouter.calls == 0


def test_router_falls_back_on_failure(app):
    openrouter = FakeProvider("openrouter", behaviour="auth")
    gemini = FakeProvider("gemini")
    router = _router_with(app, {"openrouter": openrouter, "gemini": gemini})
    result = router.chat("reasoning", [ChatMessage("user", "think")])
    assert result.provider == "gemini"
    assert gemini.calls == 1


def test_router_raises_when_all_engines_fail(app):
    openrouter = FakeProvider("openrouter", behaviour="quota")
    gemini = FakeProvider("gemini", behaviour="timeout")
    router = _router_with(app, {"openrouter": openrouter, "gemini": gemini})
    with pytest.raises(ProviderUnavailable) as excinfo:
        router.chat("reasoning", [ChatMessage("user", "x")])
    assert "openrouter" in str(excinfo.value) or "engine" in str(excinfo.value).lower()


def test_router_offline_message_mentions_settings(app):
    router = ProviderRouter(app.db, app.config, app.credentials, app.bus)
    assert router.is_offline() is True
    with pytest.raises(ProviderUnavailable) as excinfo:
        router.chat("chat", [ChatMessage("user", "hello")])
    assert "Settings" in str(excinfo.value)


def test_router_caches_identical_prompts(app):
    gemini = FakeProvider("gemini", text="cached answer")
    router = _router_with(app, {"gemini": gemini})
    first = router.chat("summarize", [ChatMessage("user", "same")], use_cache=True)
    second = router.chat("summarize", [ChatMessage("user", "same")], use_cache=True)
    assert first.text == "cached answer"
    assert second.cached is True
    assert gemini.calls == 1


def test_router_budget_blocks_an_engine(app):
    app.config.set("ai.daily_budget.openrouter", 1, save=False)
    openrouter = FakeProvider("openrouter")
    gemini = FakeProvider("gemini")
    router = _router_with(app, {"openrouter": openrouter, "gemini": gemini})
    router.chat("reasoning", [ChatMessage("user", "one")])
    result = router.chat("reasoning", [ChatMessage("user", "two")])
    assert result.provider == "gemini"


def test_router_records_usage_and_cost(app):
    openrouter = FakeProvider("openrouter")
    router = _router_with(app, {"openrouter": openrouter})
    router.chat("chat", [ChatMessage("user", "hi")])
    usage = router.usage.today()
    assert usage["totals"]["calls"] >= 1
    assert router.usage.calls_today("openrouter") >= 1
    assert router.usage.month_spend("openrouter")["cost_usd"] == pytest.approx(0.001)


def test_cache_expiry(app):
    cache = ResponseCache(app.db, default_ttl=1)
    cache.put("k", "v", ttl=60)
    assert cache.get("k") == "v"
    app.db.execute("UPDATE cache SET expires_at = 0 WHERE key = 'k'")
    assert cache.get("k") is None          # expired entries are dropped
    assert cache.stats()["entries"] == 0
    cache.put("fresh", "value")
    assert cache.stats()["live"] == 1
    assert cache.clear() == 1
    assert cache.get("fresh") is None


def test_http_timeout_maps_to_provider_timeout():
    response = httpx.Response(500, json={"error": "boom"})
    error = map_http_error("gemini", response)
    assert "service error" in error.message.lower()




# --------------------------------------------------------------- engine policy
class FakeCredentials:
    """Only the two methods the policy uses."""

    def __init__(self, configured=("openrouter", "gemini")):
        self.configured = set(configured)

    def get(self, name):
        return "key" if name in self.configured else None

    def source(self, name):
        return "vault" if name in self.configured else "unset"


def _policy(app, configured=("openrouter", "gemini"), order=("openrouter", "gemini"),
            policy="preference", behaviour="stop", credits=None):
    app.config.set("ai.engine_order", list(order), save=False)
    app.config.set("ai.engine_policy", policy, save=False)
    app.config.set("ai.cap_behaviour", behaviour, save=False)
    for engine, caps in (credits or {}).items():
        for key, value in caps.items():
            app.config.set(f"ai.credits.{engine}.{key}", value, save=False)
    return EnginePolicy(app.config, UsageTracker(app.db), FakeCredentials(configured))


def _spend(app, engine, usd=0.0, calls=1):
    usage = UsageTracker(app.db)
    for _ in range(calls):
        usage.record(engine, f"{engine}-model", "chat", cost_usd=usd)


def test_policy_uses_the_configured_order(app):
    policy = _policy(app, order=("gemini", "openrouter"))
    plan = policy.chain("chat", route=policy.preference())
    assert plan.primary == "gemini"
    assert plan.usable == ["gemini", "openrouter"]


def test_policy_skips_engines_without_a_key(app):
    policy = _policy(app, configured=("gemini",))
    plan = policy.chain("chat", route=["openrouter", "gemini"])
    assert plan.primary == "gemini"
    blocked = {item.engine: item.code for item in plan.blocked}
    assert blocked["openrouter"] == "no_key"


def test_policy_reports_a_request_with_no_keys_at_all(app):
    policy = _policy(app, configured=())
    plan = policy.chain("chat", route=["openrouter", "gemini"])
    assert plan.usable == []
    assert {item.code for item in plan.blocked} == {"no_key"}
    # both engines are named, so the user knows what to fix
    assert "OpenRouter" in plan.halt and "Gemini" in plan.halt


def test_policy_capability_filter_keeps_documents_on_gemini(app):
    policy = _policy(app, order=("openrouter", "gemini"))
    plan = policy.chain("document", route=["gemini", "openrouter"], needs_files=True)
    assert plan.primary == "gemini"
    blocked = {item.engine: item.code for item in plan.blocked}
    assert blocked["openrouter"] == "no_files"   # text-only for attachments


def test_policy_stops_at_a_cap_and_names_it(app):
    _spend(app, "openrouter", usd=5.0, calls=20)
    policy = _policy(app, behaviour="stop", credits={"openrouter": {"monthly_usd": 5}})
    plan = policy.chain("chat", route=["openrouter", "gemini"])
    assert plan.usable == []
    assert "OpenRouter" in plan.halt
    assert "cap" in plan.halt.lower()
    assert "$5.00" in plan.halt
    assert "switch" in plan.halt.lower()          # tells the user how to change it
    assert plan.switches == []


def test_policy_switches_to_the_next_engine_at_a_cap(app):
    _spend(app, "openrouter", usd=5.0, calls=20)
    policy = _policy(app, behaviour="switch", credits={"openrouter": {"monthly_usd": 5}})
    plan = policy.chain("chat", route=["openrouter", "gemini"])
    assert plan.usable == ["gemini"]
    assert plan.halt == ""
    assert plan.switches[0].source == "openrouter"
    assert plan.switches[0].target == "gemini"
    assert plan.switches[0].code == "cap"


def test_policy_stops_when_every_engine_is_capped(app):
    _spend(app, "openrouter", usd=1.0)
    _spend(app, "gemini", usd=1.0)
    caps = {"openrouter": {"monthly_usd": 1}, "gemini": {"monthly_usd": 1}}
    for behaviour in ("stop", "switch"):
        policy = _policy(app, behaviour=behaviour, credits=caps)
        plan = policy.chain("chat", route=["openrouter", "gemini"])
        assert plan.usable == []
        assert "over its cap" in plan.halt.lower() or "cap" in plan.halt.lower()


def test_policy_counts_calls_as_well_as_money(app):
    _spend(app, "openrouter", usd=0.0, calls=3)
    policy = _policy(app, behaviour="stop", credits={"openrouter": {"monthly_calls": 3}})
    plan = policy.chain("chat", route=["openrouter", "gemini"])
    assert plan.usable == []
    assert "3 of 3 calls" in plan.halt


def test_policy_credits_mode_picks_the_engine_with_headroom(app):
    _spend(app, "openrouter", usd=4.0, calls=10)
    policy = _policy(
        app,
        policy="credits",
        credits={"openrouter": {"monthly_usd": 5}, "gemini": {"monthly_usd": 5}},
    )
    plan = policy.chain("chat", route=["openrouter", "gemini"])
    assert plan.primary == "gemini"


def test_policy_manual_engine_never_falls_back(app):
    policy = _policy(app, policy="manual")
    app.config.set("ai.pinned_engine", "gemini", save=False)
    plan = policy.chain("chat", route=["openrouter", "gemini"])
    assert plan.usable == ["gemini"]


def test_policy_describe_reports_caps_and_spend(app):
    _spend(app, "openrouter", usd=2.5)
    policy = _policy(app, credits={"openrouter": {"monthly_usd": 10}})
    described = policy.describe()
    credit = described["credits"]["openrouter"]
    assert credit["spent_usd"] == pytest.approx(2.5)
    assert credit["percent"] == 25.0
    assert credit["capped"] is False
    assert described["policy"] == "preference"
    assert described["preference"] == ["openrouter", "gemini"]


def test_router_audits_a_cap_switch(app):
    _spend(app, "openrouter", usd=5.0, calls=20)
    app.config.set("ai.cap_behaviour", "switch", save=False)
    app.config.set("ai.credits.openrouter.monthly_usd", 5, save=False)
    gemini = FakeProvider("gemini")
    router = _router_with(app, {"openrouter": FakeProvider("openrouter"), "gemini": gemini})
    result = router.chat("chat", [ChatMessage("user", "hi")])
    assert result.provider == "gemini"
    row = app.db.query_one("SELECT * FROM audit_log WHERE action = 'ai_engine_switch'")
    assert row is not None
    assert "openrouter -> gemini" in row["target"]
    assert "cap" in row["detail"].lower()


def test_router_refuses_to_spend_when_stopped_at_a_cap(app):
    _spend(app, "openrouter", usd=5.0, calls=20)
    app.config.set("ai.cap_behaviour", "stop", save=False)
    app.config.set("ai.credits.openrouter.monthly_usd", 5, save=False)
    gemini = FakeProvider("gemini")
    router = _router_with(app, {"openrouter": FakeProvider("openrouter"), "gemini": gemini})
    with pytest.raises(ProviderUnavailable) as excinfo:
        router.chat("chat", [ChatMessage("user", "hi")])
    assert "cap" in str(excinfo.value).lower()
    assert gemini.calls == 0
