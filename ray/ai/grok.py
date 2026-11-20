"""Grok (xAI) provider - primary reasoning engine.

Uses xAI's OpenAI-compatible ``/chat/completions`` endpoint, including native
function calling so the orchestrator can plan with real tools.

Docs: https://docs.x.ai/api  (endpoint shape verified against the public API
reference; model names are configurable because xAI rotates them).
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
    ToolCall,
)


class GrokProvider(BaseProvider):
    name = "grok"
    supports_tools = True
    supports_vision = True

    def __init__(self, config: dict[str, Any], api_key: str | None = None) -> None:
        super().__init__(config, api_key)
        self.base_url = self.base_url or "https://api.x.ai/v1"
        self.model = self.model or "grok-4"

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
            from .base import ProviderUnavailable

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
            "max_tokens": int(max_tokens or self.config.get("max_tokens", 1200)),
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        started = time.time()
        response = self._request(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json_body=body,
        )
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("grok: response contained no choices", detail=str(data)[:300])

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
        return ChatResult(
            text=text,
            provider=self.name,
            model=data.get("model", self.model),
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=int((time.time() - started) * 1000),
            tool_calls=tool_calls,
            raw={"id": data.get("id", ""), "finish_reason": choices[0].get("finish_reason")},
        )

    # ----------------------------------------------------------------- probe
    def test_connection(self) -> HealthStatus:
        if not self.api_key:
            return HealthStatus(self.name, False, "No API key configured")
        started = time.time()
        try:
            response = self._request(
                "GET",
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            data = response.json()
            models = [item.get("id", "") for item in data.get("data", []) if item.get("id")]
            detail = f"Authenticated. {len(models)} models available"
            if self.model and models and self.model not in models:
                detail += f"; configured model '{self.model}' not listed"
            return HealthStatus(self.name, True, detail, int((time.time() - started) * 1000))
        except ProviderError as exc:
            return HealthStatus(self.name, False, exc.message, int((time.time() - started) * 1000))
        except Exception as exc:  # pragma: no cover - unexpected
            return HealthStatus(self.name, False, f"{exc.__class__.__name__}: {exc}")
