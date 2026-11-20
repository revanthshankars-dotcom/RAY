"""Credential vault, permission enforcement and auditing."""

from __future__ import annotations

import os
import stat

import pytest

from ray.security.credentials import CredentialError


def test_credentials_encrypted_at_rest(app):
    app.credentials.set("openrouter", "sk-or-test-key-1234567890")
    row = app.db.query_one("SELECT ciphertext FROM credentials WHERE name = 'openrouter'")
    assert row is not None
    assert b"sk-or-test-key" not in bytes(row["ciphertext"])
    assert app.credentials.get("openrouter") == "sk-or-test-key-1234567890"


def test_key_file_permissions(app):
    app.credentials.set("gemini", "AIza-test-key")
    mode = oct(os.stat(app.paths.secret_key_path).st_mode & 0o777)
    if os.name != "nt":
        assert mode == "0o600"


def test_credentials_never_returned_by_status(app):
    app.credentials.set("fish", "fish-secret-value")
    status = app.credentials.status()
    blob = str(status)
    assert "fish-secret-value" not in blob
    fish = next(item for item in status if item["name"] == "fish")
    assert fish["configured"] is True
    assert fish["source"] == "vault"


def test_credentials_env_fallback(app, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-from-env")
    assert app.credentials.get("gemini") == "AIza-from-env"
    assert app.credentials.source("gemini") == "env:GEMINI_API_KEY"


def test_credentials_reject_whitespace(app):
    with pytest.raises(CredentialError):
        app.credentials.set("openrouter", "has space")


def test_credentials_delete(app):
    app.credentials.set("openrouter", "key-1")
    assert app.credentials.delete("openrouter") is True
    assert app.credentials.get("openrouter") is None
    assert app.credentials.delete("openrouter") is False


def test_key_rotation_keeps_values(app):
    app.credentials.set("openrouter", "key-a")
    app.credentials.set("gemini", "key-b")
    app.credentials.rotate_key()
    assert app.credentials.get("openrouter") == "key-a"
    assert app.credentials.get("gemini") == "key-b"


def test_level1_runs_without_approval(app):
    decision = app.permissions.check("search_files", 1, {})
    assert decision.allowed is True


def test_level2_requests_approval_then_resumes(app):
    decision = app.permissions.check("copy_path", 2, {"source": "a", "destination": "b"})
    assert decision.allowed is False
    assert decision.approval_id is not None
    assert app.permissions.pending()

    assert app.permissions.decide(decision.approval_id, True) is True
    assert app.permissions.is_approved(decision.approval_id) is True
    assert app.permissions.pending() == []


def test_denied_approval_stays_denied(app):
    decision = app.permissions.check("move_path", 2, {"source": "a", "destination": "b"})
    app.permissions.decide(decision.approval_id, False)
    assert app.permissions.is_approved(decision.approval_id) is False


def test_emergency_stop_blocks_everything(app):
    app.permissions.emergency_stop(True)
    try:
        decision = app.permissions.check("search_files", 1, {})
        assert decision.allowed is False
        assert "emergency" in decision.reason.lower()
    finally:
        app.permissions.emergency_stop(False)
    assert app.permissions.check("search_files", 1, {}).allowed is True


def test_tool_call_blocked_records_audit(app):
    result = app.tools.call("delete_path", {"path": "/tmp/definitely-not-there"})
    assert result.ok is False
    assert result.requires_approval is not None
    entries = app.audit.recent(limit=10)
    assert any(entry["action"] == "tool_blocked" for entry in entries)


def test_approved_tool_executes(app, tmp_path):
    target = tmp_path / "delete-me.txt"
    target.write_text("bye", encoding="utf-8")
    first = app.tools.call("delete_path", {"path": str(target)})
    assert first.ok is False and first.requires_approval

    app.permissions.decide(first.requires_approval, True)
    second = app.tools.call("delete_path", {"path": str(target)},
                            approval_id=first.requires_approval)
    assert second.ok is True, second.error
    assert not target.exists()
    assert second.data["verified"] is True


def test_audit_search_and_stats(app):
    app.audit.record("unit_test", tool="tester", target="/tmp/x", result="ok")
    assert any(row["action"] == "unit_test" for row in app.audit.search("unit_test"))
    assert app.audit.stats()["total"] >= 1
