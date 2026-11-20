"""Shared provider contracts.

Every provider speaks the same small interface so the router can swap them
without special cases.  Failures are mapped onto a handful of exception types
so the UI can explain *why* something did not work:

    ProviderUnavailable  not configured / disabled / offline
    ProviderAuthError    the key is missing, wrong, or revoked
    ProviderQuotaError   rate limited or out of credit
    ProviderTimeout      the request took too long
    ProviderError        anything else, with a readable message
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx


class ProviderError(RuntimeError):
    """Base class for provider failures."""

    def __init__(self, message: str, *, status: int | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


class ProviderUnavailable(ProviderError):
    pass


class ProviderAuthError(ProviderError):
    pass


class ProviderQuotaError(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


@dataclass
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list["ToolCall"] = field(default_factory=list)

    def as_openai(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            payload["tool_calls"] = [call.as_openai() for call in self.tool_calls]
        return payload


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)

    def as_openai(self) -> dict[str, Any]:
        import json

        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": json.dumps(self.arguments)},
        }


@dataclass
class ChatResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cached: bool = False
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    # Money actually spent on this request, when the provider reports it, plus
    # whether it was reported (True) or estimated locally (False).
    cost_usd: float = 0.0
    cost_reported: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
            "cost_usd": round(float(self.cost_usd or 0.0), 6),
            "cost_reported": bool(self.cost_reported),
            "tool_calls": [
                {"id": c.id, "name": c.name, "arguments": c.arguments, "raw": c.raw}
                for c in self.tool_calls
            ],
        }


@dataclass
class HealthStatus:
    provider: str
    available: bool
    detail: str = ""
    latency_ms: int | None = None
    checked_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "available": self.available,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at,
        }


def map_http_error(provider: str, response: httpx.Response) -> ProviderError:
    """Translate an HTTP failure into a useful, non-secret-bearing exception."""
    status = response.status_code
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = str(
                payload.get("error", {}).get("message")
                if isinstance(payload.get("error"), dict)
                else payload.get("message")
                or payload.get("error")
                or payload.get("detail")
                or ""
            )[:400]
    except Exception:
        detail = (response.text or "")[:200]
    if status in (401, 403):
        return ProviderAuthError(
            f"{provider}: the API key was rejected (HTTP {status}). Check it in Settings.",
            status=status,
            detail=detail,
        )
    if status == 402:
        return ProviderQuotaError(f"{provider}: no credit remaining on this account.", status=status, detail=detail)
    if status == 429:
        return ProviderQuotaError(
            f"{provider}: rate limit or quota reached (HTTP 429). RAY will slow down and retry later.",
            status=status,
            detail=detail,
        )
    if status in (400, 404, 422):
        return ProviderError(f"{provider}: request rejected (HTTP {status}). {detail}".strip(), status=status, detail=detail)
    if 500 <= status < 600:
        return ProviderError(f"{provider}: service error (HTTP {status}). Try again shortly.", status=status, detail=detail)
    return ProviderError(f"{provider}: request failed (HTTP {status}). {detail}".strip(), status=status, detail=detail)


class BaseProvider:
    """Common plumbing: timeouts, retries, health probes."""

    name = "base"
    supports_tools = False
    supports_vision = False
    supports_documents = False

    def __init__(self, config: dict[str, Any], api_key: str | None = None) -> None:
        self.config = config or {}
        self.api_key = api_key
        self.model = str(self.config.get("model", ""))
        self.base_url = str(self.config.get("base_url", "")).rstrip("/")
        self.timeout = float(self.config.get("timeout", 60) or 60)
        self.max_retries = int(self.config.get("max_retries", 2) or 0)

    # ------------------------------------------------------------------ status
    @property
    def configured(self) -> bool:
        return bool(self.api_key) and bool(self.config.get("enabled", True))

    def availability(self) -> tuple[bool, str]:
        if not self.config.get("enabled", True):
            return False, f"{self.name} is disabled in Settings"
        if not self.api_key:
            return False, f"No {self.name} API key configured"
        return True, "ready"

    # ------------------------------------------------------------------ http
    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(self.timeout, connect=10.0))

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._client() as client:
                    response = client.request(
                        method, url, headers=headers, json=json_body, params=params
                    )
                if response.status_code in (429,) or response.status_code >= 500:
                    error = map_http_error(self.name, response)
                    if attempt < self.max_retries:
                        time.sleep(min(2 ** attempt, 5))
                        last_error = error
                        continue
                    raise error
                if response.status_code >= 400:
                    raise map_http_error(self.name, response)
                return response
            except httpx.TimeoutException as exc:
                last_error = ProviderTimeout(
                    f"{self.name}: request timed out after {self.timeout:g}s", detail=str(exc)
                )
                if attempt < self.max_retries:
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = ProviderError(f"{self.name}: network error ({exc.__class__.__name__})", detail=str(exc))
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 5))
                    continue
                raise last_error from exc
        raise last_error or ProviderError(f"{self.name}: request failed")

    # -------------------------------------------------------------- interface
    def chat(
        self,
        messages: Iterable[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
    ) -> ChatResult:
        raise NotImplementedError

    async def achat(self, messages: Iterable[ChatMessage], **kwargs: Any) -> ChatResult:
        return await asyncio.to_thread(self.chat, list(messages), **kwargs)

    def test_connection(self) -> HealthStatus:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        available, detail = self.availability()
        return {
            "name": self.name,
            "model": self.model,
            "enabled": bool(self.config.get("enabled", True)),
            "configured": bool(self.api_key),
            "available": available,
            "detail": detail,
            "supports_tools": self.supports_tools,
            "supports_vision": self.supports_vision,
            "supports_documents": self.supports_documents,
        }
