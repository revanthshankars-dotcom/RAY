"""The local safety net: speech without a key, without the internet.

Two promises are under test here:
  * when Fish Audio cannot speak, RAY uses the operating system's own voice
    instead of failing, and says which one was used;
  * when there is no local engine either, RAY explains exactly what to install
    rather than pretending or crashing.
"""

from __future__ import annotations

import struct
import subprocess
import wave
from pathlib import Path

import pytest

from ray.media import local_tts


def _wav_bytes(seconds: float = 0.3, rate: int = 22050) -> bytes:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = int(rate * seconds)
        handle.writeframes(b"".join(struct.pack("<h", (i * 97) % 2000 - 1000) for i in range(frames)))
    return buffer.getvalue()


def test_detect_is_honest_when_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(local_tts.shutil, "which", lambda name: None)
    monkeypatch.setattr(local_tts.platform, "system", lambda: "Linux")
    available, label, fix = local_tts.detect()
    assert available is False and label == ""
    assert "espeak-ng" in fix  # says what to install, not just "unavailable"


def test_linux_prefers_espeak_ng_then_espeak(monkeypatch):
    monkeypatch.setattr(local_tts.platform, "system", lambda: "Linux")
    monkeypatch.setattr(local_tts.shutil, "which", lambda name: "/usr/bin/" + name if name == "espeak" else None)
    assert local_tts.detect()[1] == "espeak"
    monkeypatch.setattr(local_tts.shutil, "which", lambda name: "/usr/bin/" + name if name == "espeak-ng" else None)
    assert local_tts.detect()[1] == "espeak-ng"


def test_synthesize_runs_the_engine_and_returns_labelled_wav(monkeypatch, tmp_path):
    monkeypatch.setattr(local_tts.platform, "system", lambda: "Linux")
    monkeypatch.setattr(local_tts.shutil, "which", lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None)
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        out = Path(cmd[cmd.index("-w") + 1])
        out.write_bytes(_wav_bytes())
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(local_tts.subprocess, "run", fake_run)
    audio, label = local_tts.synthesize("Good evening, Revanth.")
    assert label == "espeak-ng"
    assert audio[:4] == b"RIFF"
    assert calls["cmd"][-1] == "Good evening, Revanth."
    assert local_tts.wav_seconds(audio) == pytest.approx(0.3, abs=0.05)


def test_synthesize_refuses_to_mislabel_audio(monkeypatch):
    monkeypatch.setattr(local_tts.platform, "system", lambda: "Linux")
    monkeypatch.setattr(local_tts.shutil, "which", lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None)

    def fake_run(cmd, **kwargs):
        Path(cmd[cmd.index("-w") + 1]).write_bytes(b"OggS not a wav")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(local_tts.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="WAV"):
        local_tts.synthesize("hello")


def test_an_engine_that_crashes_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(local_tts.platform, "system", lambda: "Linux")
    monkeypatch.setattr(local_tts.shutil, "which", lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None)

    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(2, cmd, b"", b"cannot open output")

    monkeypatch.setattr(local_tts.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="cannot open output"):
        local_tts.synthesize("hello")


def test_empty_text_is_rejected():
    with pytest.raises(ValueError):
        local_tts.synthesize("   ")


# --------------------------------------------------------------- the endpoint
def test_speaking_falls_back_to_the_local_voice(client, monkeypatch):
    """No Fish key: the request must still return audio, labelled as local."""
    monkeypatch.setattr(
        "ray.media.local_tts.detect", lambda: (True, "espeak-ng", "")
    )
    monkeypatch.setattr(
        "ray.media.local_tts.synthesize", lambda text: (_wav_bytes(), "espeak-ng")
    )
    import urllib.request

    request = urllib.request.Request(
        f"{client.base}/api/voice/speak",
        data=b'{"text": "hello there", "force": true}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    assert response.status == 200
    assert response.headers["Content-Type"] == "audio/wav"
    assert response.headers["X-Audio-Engine"] == "local"
    assert body[:4] == b"RIFF"


def test_with_no_engine_at_all_the_user_is_told_what_to_do(client, monkeypatch):
    monkeypatch.setattr(
        "ray.media.local_tts.detect",
        lambda: (False, "", "Install one: sudo apt install espeak-ng"),
    )
    monkeypatch.setattr(
        "ray.media.local_tts.describe",
        lambda: {"available": False, "engine": "", "fix": "Install one: sudo apt install espeak-ng"},
    )
    payload = client.post("/api/voice/speak", {"text": "hello", "force": True})
    assert payload["ok"] is True
    assert payload["data"]["spoken"] is False
    assert "espeak-ng" in payload["data"]["local_voice"]["fix"]


def test_voice_status_names_the_engine_that_will_speak(client, monkeypatch):
    monkeypatch.setattr(
        "ray.media.local_tts.detect", lambda: (True, "espeak-ng", "")
    )
    data = client.get("/api/voice/status")["data"]
    assert data["speech_engine"] == "espeak-ng"
    assert data["local_voice"]["available"] is True
