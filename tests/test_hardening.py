"""Regression tests for the security hardening described in SECURITY.md.

Each test pins one defence that keeps untrusted input (a URL, a file, a model
reply) from turning into a leak or a code-execution path.
"""

from __future__ import annotations

import http.client
import json
import os
import urllib.request
import stat
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest


# --------------------------------------------------------------------- network
def test_fetch_target_blocks_local_and_private_targets():
    from ray.browser.search import UnsafeTarget, check_fetch_target

    blocked = [
        "http://127.0.0.1:8765/api/health",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/admin",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://user:pass@example.com/",
        "http://router/",
        "http://printer.local/",
    ]
    for url in blocked:
        with pytest.raises(UnsafeTarget):
            check_fetch_target(url)

    assert check_fetch_target("https://example.com/page") == "https://example.com/page"
    assert check_fetch_target("example.com/page") == "https://example.com/page"
    # An operator can opt in for LAN devices; it is never the default.
    assert check_fetch_target("http://192.168.1.1/", allow_private=True) == "http://192.168.1.1/"


def test_fetch_never_opens_a_socket_to_loopback():
    from ray.browser.search import WebBrowser

    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # pragma: no cover - must not be reached
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        page = WebBrowser(None, timeout=2).fetch(f"http://127.0.0.1:{port}/secrets")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert page.ok is False
    assert "blocked" in page.error
    assert hits == []


# ----------------------------------------------------------------------- files
def test_sensitive_files_cannot_be_read(tmp_path):
    from ray.computer.files import FileManager

    files = FileManager(tmp_path / "q", tmp_path)
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    key = ssh / "id_rsa"
    key.write_text("PRIVATE KEY", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=abc", encoding="utf-8")
    bundle = tmp_path / "cert.pem"
    bundle.write_text("PEM", encoding="utf-8")

    for path in (key, env, bundle):
        with pytest.raises(PermissionError):
            files.read_text(path)

    plain = tmp_path / "notes.txt"
    plain.write_text("hello", encoding="utf-8")
    assert files.read_text(plain)["text"] == "hello"

    # The opt-in exists for people who know what they are doing.
    permitted = FileManager(tmp_path / "q2", tmp_path, allow_sensitive_reads=True)
    assert permitted.read_text(key)["text"] == "PRIVATE KEY"


def test_backups_leave_credentials_out(tmp_path):
    from ray.computer.files import FileManager

    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "notes.txt").write_text("ok", encoding="utf-8")
    (folder / ".env").write_text("TOKEN=1", encoding="utf-8")
    files = FileManager(tmp_path / "q", tmp_path)
    result = files.create_backup(folder)

    with zipfile.ZipFile(result["archive"]) as bundle:
        names = bundle.namelist()
    assert any(name.endswith("notes.txt") for name in names)
    assert not any(name.endswith(".env") for name in names)
    assert result["skipped_sensitive"]


# ----------------------------------------------------------------------- shell
def test_inline_interpreter_code_is_refused():
    from ray.computer.shell import ShellRunner

    runner = ShellRunner(enabled=True)
    with pytest.raises(PermissionError):
        runner.validate(["python3", "-c", "print(1)"])
    with pytest.raises(PermissionError):
        runner.validate(["node", "--eval", "process.exit(0)"])
    assert runner.validate(["python3", "--version"])[0] == "python3"
    # Explicit opt-in still works (it is a Level 3, approved action).
    allowed = ShellRunner(enabled=True, allow_interpreter_eval=True)
    assert allowed.validate(["python3", "-c", "print(1)"])[:2] == ["python3", "-c"]


# ------------------------------------------------------------------- documents
def test_ooxml_entity_declarations_are_not_expanded(tmp_path):
    from ray.knowledge.extract import extract

    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lol [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
    )
    document = tmp_path / "bomb.docx"
    with zipfile.ZipFile(document, "w") as bundle:
        bundle.writestr(
            "word/document.xml",
            bomb + "<w:document><w:body><w:p><w:t>hi</w:t></w:p></w:body></w:document>",
        )
    text = extract(document)["text"]
    assert "skipped" in text and "lollol" not in text


def test_zip_bomb_entries_are_refused(tmp_path, monkeypatch):
    from ray.knowledge import extract as module

    document = tmp_path / "big.docx"
    with zipfile.ZipFile(document, "w") as bundle:
        bundle.writestr("word/document.xml", "<w:document><w:t>x</w:t></w:document>" * 100)
    monkeypatch.setattr(module, "MAX_ENTRY_BYTES", 10)
    text = module.extract(document)["text"]
    assert "could not read docx" in text


# ------------------------------------------------------------------- at rest
def test_database_files_are_owner_only(home):
    from ray.core.database import Database

    folder = home / "perm"
    db = Database(folder / "ray.db")
    db.connect()
    db.close()
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(folder / "ray.db").st_mode) == 0o600


def test_secret_detection_keeps_notes_usable():
    from ray.security.secrets import looks_secret

    assert looks_secret("sk-live-abcdefghijklmnop") is True
    assert looks_secret("kJ8sQ2vX9pLmN4rT7wYzB1cD5eF6gH0iJ3kL") is True
    assert looks_secret("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08") is False
    assert looks_secret("my_long_file_name_for_the_tax_documents_2024_final") is False


# --------------------------------------------------------------------- server
def test_static_requests_cannot_escape_the_ui_folder(server, tmp_path, monkeypatch):
    from ray.server import app as module

    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("INDEX", encoding="utf-8")
    sibling = tmp_path / "ui-evil"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("SECRET", encoding="utf-8")
    monkeypatch.setattr(module, "UI_DIR", ui)

    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        conn.request("GET", "/..%2Fui-evil%2Fsecret.txt")
        body = conn.getresponse().read()
    finally:
        conn.close()
    assert b"SECRET" not in body
    assert b"INDEX" in body


def test_internal_errors_do_not_echo_details(server):
    from ray.server import app as module

    def boom(application, **kwargs):
        raise RuntimeError("SQL: SELECT * FROM credentials at /etc/passwd token=abc123")

    module.ROUTES[("GET", "/api/__boom")] = boom
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        try:
            conn.request("GET", "/api/__boom")
            response = conn.getresponse()
            payload = json.loads(response.read())
        finally:
            conn.close()
    finally:
        module.ROUTES.pop(("GET", "/api/__boom"), None)

    assert response.status == 500
    assert payload["error"].startswith("internal error (reference inc-")
    assert "passwd" not in payload["error"] and "token=abc123" not in payload["error"]


def test_api_token_is_enforced_when_configured(server, monkeypatch):
    from ray.server import app as module
    from tests.conftest import Client

    monkeypatch.setenv("RAY_API_TOKEN", "s3cret-token")
    module._RATE_HITS.clear()
    client = Client(server.url)

    denied = client.get("/api/health")
    assert denied["ok"] is False and "token" in denied["error"].lower()

    allowed = client.get("/api/health", headers={"X-RAY-Token": "s3cret-token"})
    assert allowed["ok"] is True
    bearer = client.get("/api/health", headers={"Authorization": "Bearer s3cret-token"})
    assert bearer["ok"] is True

    # The page itself still loads (it cannot know the token beforehand) and it
    # carries the token so the interface can replay it on every API call.
    page = urllib.request.urlopen(f"{server.url}", timeout=10).read().decode("utf-8")
    assert '<meta name="ray-token" content="s3cret-token">' in page


def test_rate_limit_stops_a_runaway_script(server):
    from ray.server import app as module
    from tests.conftest import Client

    module._RATE_HITS.clear()
    server.app.config.set("ui.rate_limit_per_minute", 5)
    client = Client(server.url)
    try:
        results = [client.get("/api/health") for _ in range(7)]
    finally:
        server.app.config.set("ui.rate_limit_per_minute", module.DEFAULT_RATE_LIMIT)
        module._RATE_HITS.clear()

    assert all(item["ok"] is True for item in results[:5])
    assert any(item["ok"] is False and "too many requests" in item["error"] for item in results)


# ------------------------------------------------------------ engine upgrades
def test_retired_engines_are_gone_from_the_code(app):
    """No Cursor CLI or Grok adapter should survive the switch to OpenRouter."""
    import importlib

    import pytest

    for module in ("ray.ai.cursor", "ray.ai.grok"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)
    assert "openrouter" in app.router.routes()["chat"]
    assert "cursor" not in app.router.routes()["chat"]
    assert "grok" not in app.router.routes()["planning"]


def test_provider_descriptions_never_carry_key_material(app):
    from ray.ai.openrouter import OpenRouterProvider

    provider = OpenRouterProvider({"enabled": True, "model": "google/gemini-2.5-flash"}, "sk-or-secret-value")
    described = str(provider.describe())
    assert "sk-or-secret-value" not in described
    assert "openrouter" in described


def test_engine_switch_audit_does_not_repeat_forever(app):
    """A recurring cap switch is logged once per config generation, not per call."""
    from ray.ai.base import ChatMessage

    from tests.test_ai import FakeProvider, _router_with  # noqa: PLC0415

    from ray.ai.cache import UsageTracker

    UsageTracker(app.db).record("openrouter", "m", "chat", cost_usd=9.0)
    app.config.set("ai.cap_behaviour", "switch", save=False)
    app.config.set("ai.credits.openrouter.monthly_usd", 5, save=False)
    router = _router_with(app, {"openrouter": FakeProvider("openrouter"), "gemini": FakeProvider("gemini")})
    for _ in range(3):
        router.chat("chat", [ChatMessage("user", "hi")])
    rows = app.db.query("SELECT * FROM audit_log WHERE action = 'ai_engine_switch'")
    assert len(rows) == 1


def test_app_home_and_config_are_owner_only(app):
    app.config.set("app.user_name", "Test")
    if os.name == "nt":
        return
    assert stat.S_IMODE(os.stat(app.paths.data).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(app.paths.logs).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(app.paths.config_path).st_mode) == 0o600
