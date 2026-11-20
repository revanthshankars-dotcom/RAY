"""No-code automation: triggers, conditions, safety and history."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pytest

from ray.automation.engine import WorkflowService


def test_workflow_validation(app):
    service = app.automation.workflows
    with pytest.raises(ValueError):
        service.create("", {"type": "manual"}, [{"type": "notify", "title": "x"}])
    with pytest.raises(ValueError):
        service.create("Bad", {"type": "whenever"}, [{"type": "notify", "title": "x"}])
    with pytest.raises(ValueError):
        service.create("No actions", {"type": "manual"}, [])
    with pytest.raises(ValueError):
        service.create("Every zero", {"type": "every", "minutes": 0}, [{"type": "notify", "title": "x"}])
    with pytest.raises(ValueError):
        service.create("Daily no time", {"type": "daily"}, [{"type": "notify", "title": "x"}])
    with pytest.raises(ValueError):
        service.create("File no folder", {"type": "file_added"}, [{"type": "notify", "title": "x"}])


def test_manual_workflow_runs_and_history(app):
    workflow = app.automation.workflows.create(
        "Say hello",
        {"type": "manual"},
        [{"type": "notify", "title": "Hello", "body": "from a workflow"}],
    )
    result = app.automation.run(workflow["id"], trigger="manual")
    assert result["status"] == "ok", result
    assert result["run_id"] > 0

    runs = app.automation.workflows.runs(workflow_id=workflow["id"])
    assert runs and runs[0]["status"] == "ok"

    notes = app.db.query("SELECT * FROM notifications WHERE title = 'Hello'")
    assert len(notes) == 1


def test_disabled_workflow_does_not_tick(app):
    workflow = app.automation.workflows.create(
        "Disabled",
        {"type": "every", "minutes": 1},
        [{"type": "notify", "title": "should not fire"}],
    )
    app.automation.workflows.set_enabled(workflow["id"], False)
    fired = app.automation.tick()
    assert all(item["workflow_id"] != workflow["id"] for item in fired)
    assert app.db.scalar("SELECT COUNT(*) FROM notifications WHERE title = 'should not fire'") == 0


def test_every_trigger_fires_once_per_interval(app):
    workflow = app.automation.workflows.create(
        "Timed",
        {"type": "every", "minutes": 5},
        [{"type": "notify", "title": "tick"}],
    )
    first = app.automation.tick()
    assert any(item["workflow_id"] == workflow["id"] for item in first)
    second = app.automation.tick()
    assert not any(item["workflow_id"] == workflow["id"] for item in second)


def test_daily_trigger_respects_time(app):
    now = datetime.now()
    future = (now + timedelta(hours=3)).strftime("%H:%M")
    workflow = app.automation.workflows.create(
        "Later",
        {"type": "daily", "at": future},
        [{"type": "notify", "title": "later"}],
    )
    fired = app.automation.tick()
    assert not any(item["workflow_id"] == workflow["id"] for item in fired)


def test_file_added_trigger(app, tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "baseline.txt").write_text("already here", encoding="utf-8")
    workflow = app.automation.workflows.create(
        "New file",
        {"type": "file_added", "folder": str(folder)},
        [{"type": "notify", "title": "file arrived"}],
    )
    app.automation.tick()                       # records the folder's current state
    arrival = folder / "arrival.txt"
    arrival.write_text("hello", encoding="utf-8")
    os.utime(arrival, (time.time() + 5, time.time() + 5))
    fired = app.automation.tick()
    assert any(item["workflow_id"] == workflow["id"] for item in fired)


def test_conditions_block_scheduled_execution(app):
    later = datetime.now() + timedelta(hours=2)
    window = {"start": later.strftime("%H:%M"), "end": (later + timedelta(minutes=1)).strftime("%H:%M")}
    workflow = app.automation.workflows.create(
        "Conditional",
        {"type": "every", "minutes": 1},
        [{"type": "notify", "title": "never"}],
        conditions=[{"type": "time_window", **window}],
    )
    fired = app.automation.tick()
    assert not any(item["workflow_id"] == workflow["id"] for item in fired)
    assert app.db.scalar("SELECT COUNT(*) FROM notifications WHERE title = 'never'") == 0


def test_destructive_workflow_is_blocked(app):
    workflow = app.automation.workflows.create(
        "Danger",
        {"type": "manual"},
        [{"type": "tool", "tool": "delete_path", "args": {"path": "/tmp/whatever"}}],
    )
    result = app.automation.run(workflow["id"])
    assert result["status"] == "failed"
    assert "destructive" in str(result["steps"][0]["error"]).lower()
    assert app.db.scalar("SELECT COUNT(*) FROM workflow_runs") >= 1


def test_tool_action_runs_real_tool(app):
    workflow = app.automation.workflows.create(
        "Count tasks",
        {"type": "manual"},
        [{"type": "tool", "tool": "list_tasks", "args": {}}],
    )
    result = app.automation.run(workflow["id"])
    assert result["status"] == "ok"
    assert any(step.get("ok") for step in result.get("steps", []))


def test_create_task_action(app):
    workflow = app.automation.workflows.create(
        "Make a task",
        {"type": "manual"},
        [{"type": "tool", "tool": "create_task", "args": {"title": "From automation"}}],
    )
    result = app.automation.run(workflow["id"])
    assert result["status"] == "ok"
    assert app.db.scalar("SELECT COUNT(*) FROM tasks WHERE title = 'From automation'") == 1


def test_workflow_crud(app):
    workflow = app.automation.workflows.create("Temp", {"type": "manual"}, [{"type": "notify", "title": "x"}])
    updated = app.automation.workflows.update(workflow["id"], name="Renamed", description="changed")
    assert updated["name"] == "Renamed"
    assert app.automation.workflows.get(workflow["id"])["description"] == "changed"
    assert app.automation.workflows.delete(workflow["id"]) is True
    with pytest.raises(KeyError):
        app.automation.workflows.get(workflow["id"])


def test_describe_lists_default_templates(app):
    described = app.automation.describe()
    assert described["workflows"] >= 4      # the shipped templates
    assert described["active"] == 0         # templates start disabled
    assert described["recent_runs"] == []


def test_notify_writes_notification(app):
    notification_id = app.automation.notify("Unit", "body", kind="info")
    row = app.db.query_one("SELECT * FROM notifications WHERE id = ?", (notification_id,))
    assert row["title"] == "Unit"


def test_destructive_tools_are_flagged(app):
    assert app.tools.get("delete_path").destructive is True
    assert app.tools.get("power_off").destructive is True
    assert app.tools.get("list_tasks").destructive is False


def test_scheduler_status_and_tick(app):
    app.scheduler.start()
    try:
        status = app.scheduler.status()
        assert status["running"] is True
        summary = app.scheduler.tick()
        assert "reminders" in summary
    finally:
        app.scheduler.stop()
