"""Google Gemini provider - knowledge, documents and multimodal engine.

Talks to the public Generative Language REST API:

    POST {base}/models/{model}:generateContent?key=...

Supports plain chat, native function declarations, inline images and inline
PDF/audio documents (so a local PDF can be analysed without uploading it to an
intermediate service).  Transmission only happens with the user's permission,
enforced in :mod:`ray.knowledge.analysis`.
"""

from __future__ import annotations

import base64
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


class GeminiProvider(BaseProvider):
    name = "gemini"
    supports_tools = True
    supports_vision = True
    supports_documents = True

    def __init__(self, config: dict[str, Any], api_key: str | None = None) -> None:
        super().__init__(config, api_key)
        self.base_url = self.base_url or "https://generativelanguage.googleapis.com/v1beta"
        self.model = self.model or "gemini-2.5-flash"

    # ------------------------------------------------------------- payloads
    @staticmethod
    def _convert_messages(messages: Iterable[ChatMessage]) -> tuple[list[dict], str | None]:
        contents: list[dict[str, Any]] = []
        system_parts: list[str] = []
        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)
                continue
            role = "model" if message.role == "assistant" else "user"
            if message.tool_calls:
                parts = [
                    {"functionCall": {"name": call.name, "args": call.arguments}}
                    for call in message.tool_calls
                ]
                contents.append({"role": "model", "parts": parts})
                continue
            if message.role == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": message.name or "tool",
                                    "response": {"result": message.content},
                                }
                            }
                        ],
                    }
                )
                continue
            contents.append({"role": role, "parts": [{"text": message.content}]})
        return contents, ("\n\n".join(system_parts) if system_parts else None)

    def build_generate_body(
        self,
        messages: Iterable[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
        inline_files: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        contents, system_text = self._convert_messages(messages)
        if system:
            system_text = f"{system_text}\n\n{system}" if system_text else system
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": float(
                    temperature if temperature is not None else self.config.get("temperature", 0.35)
                ),
                "maxOutputTokens": int(max_tokens or self.config.get("max_tokens", 1600)),
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
            ],
        }
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}
        if inline_files:
            inline_parts = [
                {
                    "inlineData": {
                        "mimeType": item.get("mime_type", "application/octet-stream"),
                        "data": base64.b64encode(item["bytes"]).decode("ascii")
                        if isinstance(item.get("bytes"), (bytes, bytearray))
                        else item.get("data", ""),
                    }
                }
                for item in inline_files
            ]
            if contents:
                contents[-1].setdefault("parts", []).extend(inline_parts)
        if tools:
            declarations = [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "parameters": self._clean_schema(tool["function"].get("parameters", {})),
                }
                for tool in tools
                if isinstance(tool, dict) and "function" in tool
            ]
            if declarations:
                body["tools"] = [{"functionDeclarations": declarations}]
        return body

    @staticmethod
    def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
        """Gemini rejects several JSON-Schema keywords OpenAI accepts."""
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}}
        allowed = {"type", "format", "description", "nullable", "enum", "items", "properties", "required"}
        cleaned: dict[str, Any] = {}
        for key, value in schema.items():
            if key not in allowed:
                continue
            if key == "properties" and isinstance(value, dict):
                cleaned[key] = {k: GeminiProvider._clean_schema(v) for k, v in value.items()}
            elif key == "items":
                cleaned[key] = GeminiProvider._clean_schema(value)
            else:
                cleaned[key] = value
        cleaned.setdefault("type", "object")
        if cleaned["type"] == "object":
            cleaned.setdefault("properties", {})
        return cleaned

    # ------------------------------------------------------------------ chat
    def chat(
        self,
        messages: Iterable[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
        inline_files: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        available, detail = self.availability()
        if not available:
            raise ProviderUnavailable(detail)
        body = self.build_generate_body(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            system=system,
            inline_files=inline_files,
        )
        started = time.time()
        response = self._request(
            "POST",
            f"{self.base_url}/models/{self.model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": str(self.api_key)},
            params={"key": self.api_key},
            json_body=body,
        )
        data = response.json()

        candidates = data.get("candidates") or []
        if not candidates:
            blocked = (data.get("promptFeedback") or {}).get("blockReason")
            if blocked:
                raise ProviderError(f"gemini: the request was blocked ({blocked})")
            raise ProviderError("gemini: response contained no candidates", detail=str(data)[:300])

        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts", []) or []
        texts: list[str] = []
        tool_calls: list[ToolCall] = []
        for index, part in enumerate(parts):
            if "text" in part:
                texts.append(part["text"])
            if "functionCall" in part:
                call = part["functionCall"]
                tool_calls.append(
                    ToolCall(
                        id=f"gem_{index}_{call.get('name', 'tool')}",
                        name=call.get("name", ""),
                        arguments=call.get("args", {}) or {},
                        raw=call,
                    )
                )
        usage = data.get("usageMetadata") or {}
        return ChatResult(
            text="\n".join(texts).strip(),
            provider=self.name,
            model=self.model,
            input_tokens=int(usage.get("promptTokenCount", 0) or 0),
            output_tokens=int(usage.get("candidatesTokenCount", 0) or 0),
            latency_ms=int((time.time() - started) * 1000),
            tool_calls=tool_calls,
            raw={"finish_reason": candidate.get("finishReason", "")},
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
                params={"key": self.api_key, "pageSize": 50},
            )
            data = response.json()
            models = [
                item.get("name", "").replace("models/", "")
                for item in data.get("models", [])
                if "generateContent" in (item.get("supportedGenerationMethods") or [])
            ]
            detail = f"Authenticated. {len(models)} text models available"
            if self.model and models and self.model not in models:
                detail += f"; configured model '{self.model}' not listed"
            return HealthStatus(self.name, True, detail, int((time.time() - started) * 1000))
        except ProviderError as exc:
            return HealthStatus(self.name, False, exc.message, int((time.time() - started) * 1000))
        except Exception as exc:  # pragma: no cover
            return HealthStatus(self.name, False, f"{exc.__class__.__name__}: {exc}")

    # -------------------------------------------------------------- helpers
    def describe_documents(self, pdf_bytes: bytes, prompt: str, mime_type: str = "application/pdf") -> ChatResult:
        """Analyse an inline document (PDF/image) without uploading it anywhere else."""
        return self.chat(
            [ChatMessage("user", prompt)],
            inline_files=[{"bytes": pdf_bytes, "mime_type": mime_type}],
        )
