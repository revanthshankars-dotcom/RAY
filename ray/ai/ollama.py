"""Ollama - the engine that keeps working when the internet does not.

Everything else RAY talks to is a service on somebody else's computer. Ollama is
a daemon on *this* machine, so it costs nothing, needs no key, and answers when
the network is down or a cloud bill runs out.

It speaks its own HTTP API on ``http://127.0.0.1:11434``:

    GET  /api/tags   the models installed here
    POST /api/chat   {"model", "messages", "stream": false, ...}

Two deliberate limits, because pretending otherwise would be worse than being
slow:

* **No API key.** ``configured`` means "the daemon is enabled on this machine",
  and ``availability`` actually probes it. If Ollama is not running, RAY says so
  and names the command that starts it - it does not silently fall back to
  "something went wrong".
* **Tools are opt-in.** Small local models are unreliable at function calling,
  so ``supports_tools`` is False unless the user turns it on for a model they
  trust. Planning therefore keeps using a cloud engine even in local mode.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from .base import (
    BaseProvider,
    ChatMessage,
    ChatResult,
    HealthStatus,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)

DEFAULT_BASE_URL = "http://127.0.0.1:11434"

#: How long an availability probe is trusted. Restarting Ollama should show up
#: quickly without shelling out to the daemon on every keystroke.
PROBE_TTL_SECONDS = 10.0


class OllamaProvider(BaseProvider):
    name = "ollama"
    # local models are rarely good at function calling; see the module docstring
    supports_tools = False
    supports_vision = False
    supports_documents = False

    def __init__(self, config: dict[str, Any], api_key: str | None = None) -> None:
        super().__init__(config, api_key)
        self.base_url = self.base_url or DEFAULT_BASE_URL
        # local models are slow to warm up; a 60s ceiling would cut real answers off
        self.timeout = float(self.config.get("timeout", 120) or 120)
        self.supports_tools = bool(self.config.get("tools", False))
        self.model = str(self.config.get("model", "") or "")
        self._probe: tuple[float, bool, str, list[str]] | None = None

    # ------------------------------------------------------------------ status
    @property
    def configured(self) -> bool:
        return bool(self.config.get("enabled", True))

    def availability(self) -> tuple[bool, str]:
        ok, detail = self._probe_daemon()
        return ok, detail

    def _probe_daemon(self) -> tuple[bool, str]:
        """Ask the daemon what it has installed. Cached for a few seconds."""
        if not self.config.get("enabled", True):
            return False, "Ollama is turned off in Settings"
        now = time.time()
        if self._probe and (now - self._probe[0]) < PROBE_TTL_SECONDS:
            return self._probe[1], self._probe[2]
        try:
            with self._client() as client:
                response = client.get(f"{self.base_url}/api/tags", timeout=2.0)
            response.raise_for_status()
            payload = response.json()
            models = [str(item.get("name", "")) for item in payload.get("models", []) or []]
        except Exception:
            detail = (
                f"Ollama is not answering on {self.base_url}. Start it with "
                "`ollama serve`, or turn the engine off in Settings."
            )
            self._probe = (now, False, detail, [])
            return False, detail
        if not models:
            detail = (
                "Ollama is running but has no models. Pull one first: "
                "`ollama pull llama3.2:3b` (about 2 GB)."
            )
            self._probe = (now, False, detail, [])
            return False, detail
        wanted = self.model or models[0]
        if self.model and not any(name.split(":")[0] == self.model.split(":")[0] for name in models):
            detail = (
                f"Ollama is running, but '{self.model}' is not installed. "
                f"Installed: {', '.join(models[:4])}. Run `ollama pull {self.model}`."
            )
            self._probe = (now, False, detail, models)
            return False, detail
        detail = f"local · {wanted}"
        self._probe = (now, True, detail, models)
        return True, detail

    def models_installed(self) -> list[str]:
        return list(self._probe[3]) if self._probe else []

    def resolved_model(self) -> str:
        """The model to use: the configured one, or whatever is installed."""
        if self.model:
            return self.model
        ok, _ = self._probe_daemon()
        if ok and self._probe and self._probe[3]:
            return self._probe[3][0]
        return ""

    def test_connection(self) -> HealthStatus:
        started = time.time()
        self._probe = None  # a test must not read a stale answer
        ok, detail = self._probe_daemon()
        return HealthStatus(
            provider=self.name,
            available=ok,
            detail=detail,
            latency_ms=int((time.time() - started) * 1000),
        )

    # -------------------------------------------------------------------- chat
    def chat(
        self,
        messages: Iterable[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
        stream: bool = False,
        **_ignored: Any,
    ) -> ChatResult:
        model = self.resolved_model()
        if not model:
            raise ProviderUnavailable(
                "Ollama has no model to use. Pull one (`ollama pull llama3.2:3b`) "
                "or name an installed model in Settings > AI providers.",
            )
        if tools and not self.supports_tools:
            raise ProviderError(
                "ollama: this model is not enabled for tool calling in RAY. "
                "Set ai.providers.ollama.tools to true only if your model handles it."
            )
        payload_messages: list[dict[str, Any]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(message.as_openai() for message in messages)
        options: dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = float(temperature)
        if max_tokens:
            options["num_predict"] = int(max_tokens)
        payload: dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "stream": False,
        }
        if options:
            payload["options"] = options
        started = time.time()
        try:
            response = self._request("POST", f"{self.base_url}/api/chat", json_body=payload)
        except ProviderTimeout:
            raise
        except ProviderError:
            raise
        except Exception as exc:  # connection refused, DNS, anything local
            raise ProviderUnavailable(
                f"ollama: could not reach {self.base_url} ({exc.__class__.__name__}). "
                "Is the daemon running?",
            ) from exc
        try:
            data = response.json()
        except Exception as exc:  # pragma: no cover - defensive
            raise ProviderError("ollama: the daemon returned something that was not JSON") from exc
        message = data.get("message") or {}
        text = str(message.get("content", "") or "").strip()
        if not text:
            raise ProviderError("ollama: the model returned an empty answer", detail=str(data)[:300])
        return ChatResult(
            text=text,
            provider=self.name,
            model=model,
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            latency_ms=int((time.time() - started) * 1000),
            # a local model costs nothing; saying otherwise would be a lie about money
            cost_usd=0.0,
            cost_reported=False,
            raw={"done": data.get("done"), "done_reason": data.get("done_reason")},
        )

    # ------------------------------------------------------------------- other
    def speak(self, text: str, **_: Any) -> tuple[bytes, str]:
        raise ProviderError("ollama is a text engine and cannot speak; RAY uses Fish Audio or the local voice")

    def describe_vision(self, *_args: Any, **_kwargs: Any) -> str:
        raise ProviderError("ollama: image understanding is not enabled in RAY")
