"""OpenRouter provider - one key, many engines.

OpenRouter exposes an OpenAI-compatible ``/chat/completions`` endpoint, so RAY
speaks to it exactly like any other chat engine while the model behind the slug
can be Gemini, Claude, GPT, Qwen or anything else in their catalogue. Native
function calling is supported, which is what lets the planner use RAY's tools.

Docs: https://openrouter.ai/docs  (the request/response shape is the OpenAI one;
the model slug, e.g. ``google/gemini-2.5-flash``, is configuration, not code.)

Two things beyond plain chat matter here:

* OpenRouter reports the real ``usage.cost`` of a request. RAY records it, which
  is what makes the local credit caps in :mod:`ray.ai.policy` honest.
* ``/key`` reports the account's remaining credit, used by the provider test.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterable

from .base import (
    BaseProvider,
    ChatMessage,
    ChatResult,
    HealthStatus,
    ProviderError,
    ProviderUnavailable,
    ToolCall,
)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-2.5-flash"
SITE_URL = "https://github.com/revanthshankars-dotcom/RAY"


class OpenRouterProvider(BaseProvider):
    name = "openrouter"
    supports_tools = True
    supports_vision = True
    # Documents are routed to Gemini, which accepts inline PDF/audio bytes;
    # OpenRouter is used for text, tools and images.
    supports_documents = False

    def __init__(self, config: dict[str, Any], api_key: str | None = None) -> None:
        super().__init__(config, api_key)
        self.base_url = self.base_url or DEFAULT_BASE_URL
        self.model = self.model or DEFAULT_MODEL
        self.site_url = str(self.config.get("site_url") or SITE_URL)
        self.app_name = str(self.config.get("app_name") or "RAY")
        self.last_cost_usd: float | None = None

    # ---------------------------------------------------------------- headers
    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # OpenRouter uses these for its own rankings; they are optional.
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-Title"] = self.app_name
        return headers

    # ------------------------------------------------------------------- chat
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

        payload_messages: list[dict[str, Any]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        for message in messages:
            payload_messages.append(message.as_openai())

        body: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": float(
                temperature if temperature is not None else self.config.get("temperature", 0.4)
            ),
            "max_tokens": int(max_tokens or self.config.get("max_tokens", 1600)),
            "stream": False,
            # Ask for the real cost so the credit cap is measured, not guessed.
            "usage": {"include": True},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        started = time.time()
        response = self._request(
            "POST", f"{self.base_url}/chat/completions", headers=self._headers(), json_body=body
        )
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("openrouter: response contained no choices", detail=str(data)[:300])

        message = choices[0].get("message", {}) or {}
        text = (message.get("content") or "").strip()
        tool_calls: list[ToolCall] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function", {}) or {}
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                arguments = {"_raw": raw_args}
            tool_calls.append(
                ToolCall(
                    id=call.get("id") or f"call_{index}",
                    name=function.get("name", ""),
                    arguments=arguments,
                    raw=call,
                )
            )

        usage = data.get("usage") or {}
        cost = _as_float(usage.get("cost"))
        self.last_cost_usd = cost
        return ChatResult(
            text=text,
            provider=self.name,
            model=data.get("model", self.model),
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=int((time.time() - started) * 1000),
            tool_calls=tool_calls,
            cost_usd=cost or 0.0,
            cost_reported=cost is not None,
            raw={
                "id": data.get("id", ""),
                "finish_reason": choices[0].get("finish_reason"),
                "model": data.get("model", self.model),
            },
        )

    # ------------------------------------------------------------------ probe
    def test_connection(self) -> HealthStatus:
        if not self.api_key:
            return HealthStatus(self.name, False, "No API key configured")
        started = time.time()
        try:
            response = self._request("GET", f"{self.base_url}/key", headers=self._headers())
            data = response.json().get("data", {}) or {}
            label = str(data.get("label") or "key")
            limit = _as_float(data.get("limit"))
            used = _as_float(data.get("usage")) or 0.0
            if limit is None:
                detail = f"Authenticated ({label}); this key has no credit limit set"
            else:
                remaining = max(0.0, limit - used)
                detail = f"Authenticated ({label}); ${remaining:.2f} of ${limit:.2f} credit left"
            return HealthStatus(self.name, True, detail, int((time.time() - started) * 1000))
        except ProviderError as exc:
            # A rejected key is worth reporting; a broken /key endpoint on an
            # otherwise working account is not fatal for chat.
            return HealthStatus(self.name, False, exc.message, int((time.time() - started) * 1000))
        except Exception as exc:  # pragma: no cover - unexpected
            return HealthStatus(self.name, False, f"{exc.__class__.__name__}: {exc}")

    # ---------------------------------------------------------------- helpers
    def describe(self) -> dict[str, Any]:
        described = super().describe()
        described["site_url"] = self.site_url
        return described

    def list_models(self, limit: int = 200) -> list[dict[str, Any]]:
        """The catalogue, trimmed to what the settings screen needs."""
        if not self.api_key:
            raise ProviderUnavailable("No OpenRouter API key configured")
        response = self._request("GET", f"{self.base_url}/models", headers=self._headers())
        items = response.json().get("data", []) or []
        out: list[dict[str, Any]] = []
        for item in items[:limit]:
            model_id = item.get("id")
            if not model_id:
                continue
            out.append(
                {
                    "id": model_id,
                    "name": item.get("name") or model_id,
                    "context": item.get("context_length"),
                    "tools": "tools" in (item.get("supported_parameters") or []),
                    "pricing": item.get("pricing") or {},
                }
            )
        return out


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
