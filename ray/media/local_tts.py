"""Speaking without the internet.

Fish Audio is a service: with no key, no credit or a cut connection it cannot say
a word. Every desktop RAY runs on already has a speech engine built into the
operating system, so there is no honest reason for silence.

    Windows   SAPI 5 through PowerShell  (the voice Windows already uses)
    macOS     /usr/bin/say
    Linux     espeak-ng, espeak or pico2wave, whichever is installed

Everything here is local and free. Nothing is downloaded, nothing is uploaded,
and the text never leaves the machine. If none of these exist, RAY says so and
names the package to install rather than failing with a stack trace.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

#: Length guard: local engines are synchronous, so a careless paste should not
#: lock the request thread for a minute.
MAX_CHARACTERS = 1200
TIMEOUT_SECONDS = 60


def _is_windows() -> bool:
    return platform.system().lower().startswith("win")


def _is_macos() -> bool:
    return platform.system().lower() == "darwin"


def linux_engine() -> str:
    for candidate in ("espeak-ng", "espeak", "pico2wave"):
        if shutil.which(candidate):
            return candidate
    return ""


def detect() -> tuple[bool, str, str]:
    """(available, engine label, how to fix it if not).

    Checking is cheap and side-effect free: no speech is generated, no package is
    installed, nothing is assumed.
    """
    if _is_windows():
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return False, "", "Windows speech needs PowerShell, which this machine does not expose on PATH."
        # System.Speech is part of .NET Framework; it ships with Windows itself.
        return True, "Windows speech (SAPI)", ""
    if _is_macos():
        if shutil.which("say"):
            return True, "macOS `say`", ""
        return False, "", "macOS speech needs /usr/bin/say, which is missing from this system."
    engine = linux_engine()
    if engine:
        label = {"espeak-ng": "espeak-ng", "espeak": "espeak", "pico2wave": "pico2wave"}[engine]
        return True, label, ""
    return False, "", (
        "No local speech engine found. Install one: `sudo apt install espeak-ng` (Debian/Ubuntu) "
        "or `sudo dnf install espeak-ng` (Fedora), then reload the page."
    )


def describe() -> dict[str, str | bool]:
    """What the UI shows: is there a local voice, what is it, what to do otherwise."""
    available, label, fix = detect()
    return {
        "available": available,
        "engine": label,
        "fix": fix,
        "platform": platform.system(),
        "note": (
            "Local speech is produced by this computer and never uploaded. It is used when "
            "Fish Audio is not configured or the request fails."
        ),
    }


def _write_windows_wav(text: str, path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    # Single-quoted PowerShell strings escape a quote by doubling it.
    spoken = text.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{path}'); "
        f"$s.Speak('{spoken}'); "
        "$s.Dispose();"
    )
    subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        timeout=TIMEOUT_SECONDS,
        capture_output=True,
    )


def synthesize(text: str) -> tuple[bytes, str]:
    """Render ``text`` to WAV bytes with the local engine. Returns (audio, engine)."""
    spoken = (text or "").strip()
    if not spoken:
        raise ValueError("nothing to speak")
    spoken = spoken[:MAX_CHARACTERS]
    available, label, fix = detect()
    if not available:
        raise RuntimeError(fix or "no local speech engine is available")

    with tempfile.TemporaryDirectory(prefix="ray-voice-") as folder:
        out = Path(folder) / "speech.wav"
        try:
            if _is_windows():
                _write_windows_wav(spoken, out)
            elif _is_macos():
                subprocess.run(
                    ["say", "--data-format=LEI16@22050", "-o", str(out), spoken],
                    check=True,
                    timeout=TIMEOUT_SECONDS,
                    capture_output=True,
                )
            else:
                engine = linux_engine()
                if engine == "pico2wave":
                    subprocess.run(
                        [engine, "-l", "en-US", "-w", str(out), spoken],
                        check=True,
                        timeout=TIMEOUT_SECONDS,
                        capture_output=True,
                    )
                else:
                    subprocess.run(
                        [engine, "-w", str(out), spoken],
                        check=True,
                        timeout=TIMEOUT_SECONDS,
                        capture_output=True,
                    )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"the local speech engine did not finish within {TIMEOUT_SECONDS}s"
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"")[:200].decode(errors="replace").strip()
            raise RuntimeError(f"{label} failed{': ' + detail if detail else ''}") from exc

        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(f"{label} produced no audio")
        audio = out.read_bytes()

    if not audio[:4] == b"RIFF":
        # WAV is what /api/voice/speak advertises; refuse to mislabel anything else
        raise RuntimeError(f"{label} produced audio RAY cannot label as WAV")
    return audio, label


def wav_seconds(audio: bytes) -> float:
    """Duration of a WAV payload, for the log line. Never raises."""
    try:
        import io

        with wave.open(io.BytesIO(audio), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 1
            return round(frames / float(rate), 2)
    except Exception:
        return 0.0
