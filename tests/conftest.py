"""Shared fixtures.

Every test runs against a throwaway RAY home so nothing touches the developer's
real database, credentials or files.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "ray-home"
    target.mkdir()
    monkeypatch.setenv("RAY_HOME", str(target))
    for key in ("XAI_API_KEY", "GROK_API_KEY", "RAY_GROK_API_KEY", "GEMINI_API_KEY",
                "GOOGLE_API_KEY", "RAY_GEMINI_API_KEY", "FISH_API_KEY", "FISH_AUDIO_API_KEY",
                "RAY_FISH_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    return target


@pytest.fixture()
def app(home: Path):
    from ray.app import RayApp

    instance = RayApp(home=home)
    yield instance
    try:
        instance.scheduler.stop()
    except Exception:
        pass
    instance.db.close()


@pytest.fixture()
def server(app):
    from ray.server.app import create_server

    srv = create_server(app, host="127.0.0.1", port=0).start()
    yield srv
    srv.stop()


class Client:
    """Tiny JSON client for the local API."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def get(self, path: str):
        with urllib.request.urlopen(f"{self.base}{path}", timeout=20) as response:
            return json.loads(response.read())

    def post(self, path: str, body: dict | None = None):
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            return json.loads(error.read())

    def patch(self, path: str, body: dict | None = None):
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            return json.loads(error.read())

    def delete(self, path: str, body: dict | None = None):
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json"},
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            return json.loads(error.read())


@pytest.fixture()
def client(server):
    return Client(server.url)
