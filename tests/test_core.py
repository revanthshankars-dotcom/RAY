"""Config, database and persistence basics."""

from __future__ import annotations

import json

from ray.core.config import DEFAULTS, Config
from ray.core.database import Database


def test_config_defaults_and_dotted_access(tmp_path):
    config = Config(tmp_path / "config.json")
    assert config.get("ui.port") == DEFAULTS["ui"]["port"]
    assert config.get("ai.providers.openrouter.model")
    assert config.get("does.not.exist", "fallback") == "fallback"


def test_config_persists_and_merges_unknown_keys(tmp_path):
    path = tmp_path / "config.json"
    config = Config(path)
    config.set("app.user_name", "Revanth")
    config.set("custom.future_key", {"kept": True})
    saved = json.loads(path.read_text())
    assert saved["app"]["user_name"] == "Revanth"

    reloaded = Config(path)
    assert reloaded.get("app.user_name") == "Revanth"
    assert reloaded.get("custom.future_key.kept") is True
    # defaults survive a partial file
    assert reloaded.get("ui.port") == DEFAULTS["ui"]["port"]


def test_config_recovers_from_corrupt_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not valid json", encoding="utf-8")
    config = Config(path)
    assert config.get("ui.port") == DEFAULTS["ui"]["port"]
    assert path.with_suffix(".json.corrupt").exists()


def test_config_validation_flags_bad_values(tmp_path):
    config = Config(tmp_path / "config.json")
    assert config.validate() == []
    config.set("ui.port", 99999, save=False)
    config.set("automation.tick_seconds", 1, save=False)
    config.set("voice.speed", 9, save=False)
    problems = " ".join(config.validate())
    assert "ui.port" in problems
    assert "tick_seconds" in problems
    assert "voice.speed" in problems


def test_change_listeners_fire(tmp_path):
    config = Config(tmp_path / "config.json")
    seen = []
    config.on_change(lambda key, value: seen.append((key, value)))
    config.set("app.user_name", "Test")
    assert ("app.user_name", "Test") in seen


def test_database_schema_and_crud(tmp_path):
    db = Database(tmp_path / "test.db")
    db.migrate()
    task_id = db.insert("tasks", {
        "title": "Write tests",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    })
    assert task_id > 0
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    assert row["title"] == "Write tests"
    db.update("tasks", task_id, {"status": "done"})
    assert db.query_one("SELECT status FROM tasks WHERE id = ?", (task_id,))["status"] == "done"
    assert db.delete("tasks", task_id) == 1
    assert db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,)) is None
    db.close()


def test_database_migration_is_idempotent(tmp_path):
    db = Database(tmp_path / "test.db")
    first = db.migrate()
    second = db.migrate()
    assert first == second
    db.close()


def test_database_fts_and_backup(tmp_path):
    db = Database(tmp_path / "test.db")
    db.migrate()
    file_id = db.insert("files", {
        "path": "/tmp/a.pdf", "name": "a.pdf", "ext": ".pdf", "size": 10,
        "mtime": 1.0, "kind": "document", "indexed_at": "2026-01-01",
    })
    db.execute(
        "INSERT INTO files_fts (rowid, name, path, content) VALUES (?,?,?,?)",
        (file_id, "a.pdf", "/tmp/a.pdf", "the companies act borrowing powers"),
    )
    rows = db.query("SELECT rowid FROM files_fts WHERE files_fts MATCH ?", ('"borrowing"*',))
    assert rows and rows[0]["rowid"] == file_id

    target = tmp_path / "backup.db"
    db.backup(target)
    assert target.exists()
    assert db.integrity_check()[0] is True
    db.close()

    restored = Database(target)
    assert restored.scalar("SELECT COUNT(*) FROM files") == 1
    restored.close()


def test_table_counts_and_integrity(app):
    counts = app.db.table_counts()
    assert counts["tasks"] == 0
    assert app.db.integrity_check()[0] is True
    assert app.db.size_bytes() > 0


def test_config_migrates_the_retired_engines(tmp_path, monkeypatch):
    monkeypatch.setenv("RAY_OPENROUTER_API_KEY", "sk-or-test")
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "ai": {
            "routing_version": 2,
            "default_provider": "cursor",
            "daily_budget": {"cursor": 300, "grok": 300, "gemini": 400},
            "providers": {
                "cursor": {"binary": "cursor-agent", "workspace": ""},
                "grok": {"model": "grok-4"},
                "gemini": {"model": "gemini-2.5-flash"},
            },
        },
    }), encoding="utf-8")
    config = Config(path)
    assert config.get("ai.routing_version") == 3
    assert config.get("ai.engine_order") == ["openrouter", "gemini"]
    assert config.get("ai.default_provider") is None
    assert set(config.section("ai.providers")) == {"openrouter", "gemini", "fish"}
    assert "cursor" not in config.section("ai.daily_budget")
    assert config.validate() == []
    saved = json.loads(path.read_text(encoding="utf-8"))["ai"]
    assert "cursor" not in saved["providers"] and "grok" not in saved["providers"]


def test_config_migration_prefers_gemini_when_that_is_all_there_is(tmp_path, monkeypatch):
    monkeypatch.delenv("RAY_OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("RAY_GEMINI_API_KEY", "AIza-test")
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ai": {"routing_version": 2, "default_provider": "grok"}}), encoding="utf-8")
    config = Config(path)
    assert config.get("ai.engine_order") == ["gemini", "openrouter"]
    assert config.get("ai.routing_version") == 3


def test_config_keeps_an_explicit_engine_order(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "ai": {"routing_version": 2, "default_provider": "grok", "engine_order": ["gemini", "openrouter"]},
    }), encoding="utf-8")
    config = Config(path)
    assert config.get("ai.engine_order") == ["gemini", "openrouter"]
    assert config.get("ai.pinned_engine") == ""
    assert config.get("ai.cap_behaviour") == "stop"


def test_database_is_at_schema_four_with_usage_cost(tmp_path):
    from ray.core.database import SCHEMA_VERSION

    db = Database(tmp_path / "legacy.db")
    db.migrate()
    assert SCHEMA_VERSION == 4
    assert db.scalar("PRAGMA user_version") == 4
    columns = {row["name"] for row in db.query("PRAGMA table_info(usage)")}
    assert "cost_usd" in columns
