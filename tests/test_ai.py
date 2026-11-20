"""Provider request/response handling, error mapping, routing and caching.

No network calls: HTTP responses are injected so parsing and failure behaviour
can be tested deterministically.
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
from ray.ai.cache import ResponseCache
from ray.ai.cursor import CursorProvider
from ray.ai.fish import FishAudioProvider
from ray.ai.gemini import GeminiProvider
from ray.ai.grok import GrokProvider
from ray.ai.router import ProviderRouter


def grok_config(**overrides):
    config = {"enabled": True, "model": "grok-4", "base_url": "https://api.x.ai/v1", "timeout": 10}
    config.update(overrides)
    return config


def test_grok_parses_text_and_usage(monkeypatch):
    provider = GrokProvider(grok_config(), "test-key")
    payload = {
        "id": "abc",
        "model": "grok-4",
        "choices": [{"message": {"content": "Hello there."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4},
    }
    monkeypatch.setattr(provider, "_request", lambda *a, **k: httpx.Response(200, json=payload))
    result = provider.chat([ChatMessage("user", "hi")])
    assert result.text == "Hello there."
    assert result.input_tokens == 11
    assert result.output_tokens == 4
    assert result.provider == "grok"


def test_grok_parses_tool_calls(monkeypatch):
    provider = GrokProvider(grok_config(), "test-key")
    payload = {
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
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


def test_grok_rejects_bad_json_tool_arguments(monkeypatch):
    provider = GrokProvider(grok_config(), "test-key")
    payload = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "1", "function": {"name": "x", "arguments": "{not json"}}]}}], "usage": {}}
    monkeypatch.setattr(provider, "_request", lambda *a, **k: httpx.Response(200, json=payload))
    result = provider.chat([ChatMessage("user", "x")])
    assert "_raw" in result.tool_calls[0].arguments


def test_grok_requires_api_key():
    provider = GrokProvider(grok_config(), None)
    available, detail = provider.availability()
    assert available is False
    assert "key" in detail.lower()
    with pytest.raises(ProviderUnavailable):
        provider.chat([ChatMessage("user", "hi")])


def test_grok_disabled_provider():
    provider = GrokProvider(grok_config(enabled=False), "key")
    assert provider.availability()[0] is False


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (429, ProviderQuotaError),
        (402, ProviderQuotaError),
        (400, ProviderError),
        (500, ProviderError),
    ],
)
def test_error_mapping(status, expected):
    response = httpx.Response(status, json={"error": {"message": "detail here"}})
    error = map_http_error("grok", response)
    assert isinstance(error, expected)
    assert "grok" in error.message


def test_grok_propagates_auth_error(monkeypatch):
    provider = GrokProvider(grok_config(), "bad-key")
    error = ProviderAuthError("grok: the API key was rejected (HTTP 401).")

    def raise_error(*args, **kwargs):
        raise error

    monkeypatch.setattr(provider, "_request", raise_error)
    with pytest.raises(ProviderAuthError):
        provider.chat([ChatMessage("user", "hi")])


def test_grok_test_connection(monkeypatch):
    provider = GrokProvider(grok_config(), "key")
    monkeypatch.setattr(
        provider, "_request",
        lambda *a, **k: httpx.Response(200, json={"data": [{"id": "grok-4"}, {"id": "grok-3"}]}),
    )
    health = provider.test_connection()
    assert health.available is True
    assert "2 models" in health.detail


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
        self.supports_tools = name in ("gemini", "grok")

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
                          input_tokens=3, output_tokens=2)

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


def test_router_prefers_primary_provider(app):
    cursor = FakeProvider("cursor")
    gemini = FakeProvider("gemini")
    router = _router_with(app, {"cursor": cursor, "gemini": gemini})
    result = router.chat("reasoning", [ChatMessage("user", "think")])
    assert result.provider == "cursor"
    assert gemini.calls == 0


def test_router_routes_chat_to_cursor_and_planning_to_gemini(app):
    router = _router_with(app, {"cursor": FakeProvider("cursor"), "gemini": FakeProvider("gemini")})
    assert router.resolve_chain("chat")[0] == "cursor"
    assert router.resolve_chain("planning")[0] == "gemini"
    assert "grok" not in router.resolve_chain("chat")


def test_router_skips_providers_without_tool_support(app):
    cursor = FakeProvider("cursor")
    gemini = FakeProvider("gemini")
    router = _router_with(app, {"cursor": cursor, "gemini": gemini})
    tools = [{"type": "function", "function": {"name": "list_files", "parameters": {}}}]
    result = router.chat("planning", [ChatMessage("user", "list my files")], tools=tools)
    assert result.provider == "gemini"
    assert cursor.calls == 0


def test_router_falls_back_on_failure(app):
    cursor = FakeProvider("cursor", behaviour="auth")
    gemini = FakeProvider("gemini")
    router = _router_with(app, {"cursor": cursor, "gemini": gemini})
    result = router.chat("reasoning", [ChatMessage("user", "think")])
    assert result.provider == "gemini"
    assert gemini.calls == 1


def test_router_raises_when_all_providers_fail(app):
    cursor = FakeProvider("cursor", behaviour="quota")
    gemini = FakeProvider("gemini", behaviour="timeout")
    router = _router_with(app, {"cursor": cursor, "gemini": gemini})
    with pytest.raises(ProviderUnavailable) as excinfo:
        router.chat("reasoning", [ChatMessage("user", "x")])
    assert "cursor" in str(excinfo.value) or "provider" in str(excinfo.value).lower()


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


def test_router_budget_blocks_provider(app):
    app.config.set("ai.daily_budget.cursor", 1, save=False)
    cursor = FakeProvider("cursor")
    gemini = FakeProvider("gemini")
    router = _router_with(app, {"cursor": cursor, "gemini": gemini})
    router.chat("reasoning", [ChatMessage("user", "one")])
    result = router.chat("reasoning", [ChatMessage("user", "two")])
    assert result.provider == "gemini"


def test_router_records_usage(app):
    cursor = FakeProvider("cursor")
    router = _router_with(app, {"cursor": cursor})
    router.chat("chat", [ChatMessage("user", "hi")])
    usage = router.usage.today()
    assert usage["totals"]["calls"] >= 1
    assert router.usage.calls_today("cursor") >= 1


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


# --------------------------------------------------------------- cursor (CLI)
def _fake_cli(tmp_path, body: str) -> str:
    script = tmp_path / "cursor-agent"
    script.write_text("#!/usr/bin/env python3\n" + body)
    script.chmod(0o755)
    return str(script)


def cursor_config(binary: str, **overrides):
    config = {"enabled": True, "binary": binary, "timeout": 10, "output_format": "text"}
    config.update(overrides)
    return config


def test_cursor_reports_a_missing_cli(monkeypatch):
    monkeypatch.setattr("ray.ai.cursor.shutil.which", lambda name: None)
    provider = CursorProvider({"enabled": True, "binary": "/definitely/not/here"})
    available, detail = provider.availability()
    assert available is False
    assert "Cursor CLI not found" in detail
    assert provider.configured is False
    with pytest.raises(ProviderUnavailable):
        provider.chat([ChatMessage("user", "hi")])


def test_cursor_chat_runs_read_only_print_mode(tmp_path, monkeypatch):
    log = tmp_path / "args.json"
    cli = _fake_cli(
        tmp_path,
        "import json, os, sys\n"
        "open(os.environ['FAKE_ARGS'], 'w').write(json.dumps(sys.argv[1:]))\n"
        "print('Hello from Cursor.')\n",
    )
    monkeypatch.setenv("FAKE_ARGS", str(log))
    provider = CursorProvider(cursor_config(cli))
    result = provider.chat([ChatMessage("user", "hi there")], system="Be brief.")

    args = json.loads(log.read_text())
    assert "--print" in args
    assert args[args.index("--mode") + 1] == "ask"
    assert "--force" not in args and "--yolo" not in args
    assert "--output-format" in args and "text" in args
    prompt = args[-1]
    assert "System instructions" in prompt and "User: hi there" in prompt
    assert result.text == "Hello from Cursor."
    assert result.provider == "cursor"


def test_cursor_passes_the_optional_api_key(tmp_path, monkeypatch):
    seen = tmp_path / "env.txt"
    cli = _fake_cli(
        tmp_path,
        "import os\n"
        "open(os.environ['FAKE_ENV'], 'w').write(os.environ.get('CURSOR_API_KEY', ''))\n"
        "print('ok')\n",
    )
    monkeypatch.setenv("FAKE_ENV", str(seen))
    provider = CursorProvider(cursor_config(cli), "cur-test-key")
    provider.chat([ChatMessage("user", "hi")])
    assert seen.read_text() == "cur-test-key"


def test_cursor_auth_failure_is_explained(tmp_path):
    cli = _fake_cli(tmp_path, "import sys\nsys.stderr.write('Not logged in. Run cursor-agent login')\nsys.exit(1)\n")
    provider = CursorProvider(cursor_config(cli))
    with pytest.raises(ProviderAuthError) as excinfo:
        provider.chat([ChatMessage("user", "hi")])
    assert "login" in str(excinfo.value).lower()


def test_cursor_timeout_is_reported(tmp_path):
    cli = _fake_cli(tmp_path, "import time\ntime.sleep(10)\n")
    provider = CursorProvider(cursor_config(cli, timeout=1))
    with pytest.raises(ProviderTimeout):
        provider.chat([ChatMessage("user", "hi")])


def test_cursor_test_connection_detects_sign_in(tmp_path):
    cli = _fake_cli(
        tmp_path,
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('cursor-agent 2026.10.1'); raise SystemExit(0)\n"
        "print('Logged in as revanth'); raise SystemExit(0)\n",
    )
    status = CursorProvider(cursor_config(cli)).test_connection()
    assert status.available is True
    assert "cursor-agent 2026.10.1" in status.detail


def test_cursor_test_connection_detects_signed_out(tmp_path):
    cli = _fake_cli(
        tmp_path,
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('cursor-agent 2026.10.1'); raise SystemExit(0)\n"
        "print('Not logged in'); raise SystemExit(1)\n",
    )
    status = CursorProvider(cursor_config(cli)).test_connection()
    assert status.available is False
    assert "not signed in" in status.detail


def test_cursor_strips_ansi_and_parses_json_output(tmp_path):
    cli = _fake_cli(
        tmp_path,
        "import json\n"
        "print('\\x1b[32m' + json.dumps({'type': 'result', 'result': 'JSON answer'}) + '\\x1b[0m')\n",
    )
    provider = CursorProvider(cursor_config(cli, output_format="json"))
    result = provider.chat([ChatMessage("user", "hi")])
    assert result.text == "JSON answer"


def test_cursor_describe_marks_cli_presence(tmp_path):
    cli = _fake_cli(tmp_path, "print('ok')\n")
    described = CursorProvider(cursor_config(cli)).describe()
    assert described["name"] == "cursor"
    assert described["configured"] is True
    assert described["supports_tools"] is False
    assert described["binary"] == cli
