"""Fish Audio provider - natural text-to-speech.

Endpoint (verified against https://docs.fish.audio/api-reference):

    POST https://api.fish.audio/v1/tts
    Authorization: Bearer <key>
    Content-Type: application/json
    model: s1 | s2-pro | s2.1-pro | s2.1-pro-free | drama-3
    {"text": "...", "reference_id": "<voice model id>", "format": "mp3"}

The response body is the audio file itself.  ``reference_id`` selects a voice
model from fish.audio; when empty the service uses its default voice.

Fish Audio is used for speech only.  RAY never sends transcripts anywhere else
and never claims the service is free - the free tier model is selectable in
Settings, everything else depends on the user's own account.
"""

from __future__ import annotations

import time
from typing import Any

from .base import BaseProvider, HealthStatus, ProviderError, ProviderUnavailable

DEFAULT_MODEL = "s2.1-pro-free"
MODELS = ["s2.1-pro-free", "s2.1-pro", "s2-pro", "s1", "drama-3"]
FORMATS = ["mp3", "wav", "opus", "pcm"]


class FishAudioProvider(BaseProvider):
    name = "fish"
    supports_tools = False
    supports_vision = False

    def __init__(self, config: dict[str, Any], api_key: str | None = None) -> None:
        super().__init__(config, api_key)
        self.base_url = self.base_url or "https://api.fish.audio"
        self.model = self.model or DEFAULT_MODEL

    # ------------------------------------------------------------------- tts
    def synthesize(
        self,
        text: str,
        *,
        reference_id: str | None = None,
        audio_format: str | None = None,
        speed: float | None = None,
    ) -> tuple[bytes, str, int]:
        """Return ``(audio_bytes, format, latency_ms)``."""
        available, detail = self.availability()
        if not available:
            raise ProviderUnavailable(detail)
        text = (text or "").strip()
        if not text:
            raise ProviderError("fish: nothing to speak (empty text)")
        if len(text) > 6000:
            raise ProviderError("fish: text too long for one request (limit 6000 characters)")

        fmt = (audio_format or self.config.get("format") or "mp3").lower()
        if fmt not in FORMATS:
            fmt = "mp3"
        body: dict[str, Any] = {
            "text": text,
            "format": fmt,
            "normalize": True,
            "latency": str(self.config.get("latency", "normal")),
        }
        voice = (reference_id or self.config.get("reference_id") or "").strip()
        if voice:
            body["reference_id"] = voice
        speed_value = self.config.get("speed") if speed is None else speed
        if speed_value:
            try:
                if abs(float(speed_value) - 1.0) > 0.01:
                    body["prosody"] = {
                        "speed": max(0.5, min(2.0, float(speed_value))),
                        "volume": 0,
                        "normalize_loudness": True,
                    }
            except (TypeError, ValueError):
                pass

        started = time.time()
        response = self._request(
            "POST",
            f"{self.base_url}/v1/tts",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "model": self.model or DEFAULT_MODEL,
            },
            json_body=body,
        )
        audio = response.content
        if not audio:
            raise ProviderError("fish: the service returned an empty audio stream")
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            # A 200 with JSON means an error object wrapped in a success code.
            raise ProviderError("fish: unexpected JSON response from the TTS endpoint", detail=audio[:300].decode(errors="replace"))
        return audio, fmt, int((time.time() - started) * 1000)

    # ---------------------------------------------------------------- voices
    def list_voices(self, page_size: int = 20) -> list[dict[str, Any]]:
        """List the user's own voice models (cloned voices and saved ones)."""
        if not self.api_key:
            raise ProviderUnavailable("No Fish Audio API key configured")
        response = self._request(
            "GET",
            f"{self.base_url}/model",
            headers={"Authorization": f"Bearer {self.api_key}"},
            params={"page_size": page_size, "page_number": 1, "self": "true"},
        )
        data = response.json()
        items = data.get("items") if isinstance(data, dict) else None
        if items is None and isinstance(data, dict):
            items = data.get("models", [])
        voices: list[dict[str, Any]] = []
        for item in items or []:
            voices.append(
                {
                    "id": item.get("_id") or item.get("id", ""),
                    "title": item.get("title", "Untitled voice"),
                    "state": item.get("state", ""),
                    "visibility": item.get("visibility", ""),
                    "languages": item.get("languages", []),
                }
            )
        return voices

    # ----------------------------------------------------------------- probe
    def test_connection(self) -> HealthStatus:
        if not self.api_key:
            return HealthStatus(self.name, False, "No API key configured")
        started = time.time()
        try:
            voices = self.list_voices(page_size=1)
            detail = f"Authenticated. {len(voices)} voice model(s) visible, model header '{self.model}'"
            return HealthStatus(self.name, True, detail, int((time.time() - started) * 1000))
        except ProviderError as exc:
            return HealthStatus(self.name, False, exc.message, int((time.time() - started) * 1000))
        except Exception as exc:  # pragma: no cover
            return HealthStatus(self.name, False, f"{exc.__class__.__name__}: {exc}")

    def chat(self, *args: Any, **kwargs: Any):  # pragma: no cover - not a chat model
        raise ProviderError("fish is a speech provider and cannot answer text prompts")
