"""Intent parsing, planning and orchestration behind the command center."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ray.brain.intent import IntentParser, parse_when


@pytest.fixture()
def parser():
    return IntentParser()


def kinds(text, parser):
    intent = parser.parse(text)
    return (intent.kind, intent.args) if intent else (None, {})


def test_reminder_intent(parser):
    kind, args = kinds("remind me to call the registrar at 6pm", parser)
    assert kind == "create_reminder"
    assert "registrar" in args["title"].lower()
    assert args["due_at"]


def test_task_intent_keeps_capitalisation(parser):
    kind, args = kinds("add task Finish the Companies Act notes", parser)
    assert kind == "create_task"
    assert args["title"].startswith("Finish")


def test_note_intent(parser):
    kind, args = kinds("note: section 180 borrowing limits", parser)
    assert kind == "create_note"
    assert "borrowing" in (args.get("body") or "").lower()


def test_memory_intent(parser):
    kind, args = kinds("remember that my exam is on 12 March", parser)
    assert kind == "remember"
    assert "exam" in str(args).lower()


def test_system_check_intent(parser):
    kind, _ = kinds("check my laptop performance", parser)
    assert kind == "system_report"


def test_screenshot_intent(parser):
    kind, _ = kinds("take a screenshot", parser)
    assert kind == "screenshot"


def test_file_search_intent_with_root(parser):
    kind, args = kinds("find pdfs in downloads", parser)
    assert kind == "search_files"
    assert args["extension"] == "pdf"
    assert args["query"] == ""
    assert args["root"].lower() in ("downloads", "download")


def test_folder_open_intent(parser):
    kind, args = kinds("open downloads", parser)
    assert kind == "open_folder"
    assert args["folder"].lower() in ("downloads", "download")


def test_web_search_intent(parser):
    kind, args = kinds("search for retrieval augmented generation tutorials", parser)
    assert kind == "web_search"
    assert "retrieval" in args["query"].lower()


def test_open_and_search_compound_intent(parser):
    kind, args = kinds("open chrome and search for python decorators", parser)
    assert kind == "web_search"
    assert args["open_browser"] is True
    assert "decorators" in args["query"].lower()


def test_volume_and_brightness(parser):
    assert kinds("volume up", parser)[0] == "volume"
    assert kinds("set brightness to 40 percent", parser)[0] == "set_brightness"


def test_power_intents(parser):
    assert kinds("lock the computer", parser)[0] == "power_lock"
    assert kinds("shut down the laptop", parser)[0] == "power_shutdown"


def test_backup_intent(parser):
    kind, args = kinds("back up my projects folder", parser)
    assert kind == "backup"
    assert "projects" in str(args).lower()


def test_note_verb_required(parser):
    intent = parser.parse("find my meeting notes")
    assert intent is not None and intent.kind == "search_files"


def test_unknown_text_returns_none(parser):
    assert parser.parse("what is the meaning of life") is None
    assert parser.parse("") is None


def test_parse_when_relative():
    now = datetime(2026, 11, 20, 9, 0)
    assert parse_when("in 30 minutes", now) == datetime(2026, 11, 20, 9, 30)
    assert parse_when("in 2 hours", now) == datetime(2026, 11, 20, 11, 0)
    assert parse_when("tomorrow at 9:30", now) == datetime(2026, 11, 21, 9, 30)
    assert parse_when("at 18:15", now) == datetime(2026, 11, 20, 18, 15)
    assert parse_when("tonight", now) is not None
    assert parse_when("someday", now) is None


def test_plan_local_creates_tool_step(app):
    plan = app.planner.plan_local("check my laptop performance")
    assert plan is not None
    assert plan.steps[0].tool == "system_report"
    assert plan.source == "local"


def test_orchestrator_executes_task(app):
    result = app.orchestrator.handle("add task Buy printer paper")
    assert result.status == "completed"
    assert app.db.scalar("SELECT COUNT(*) FROM tasks WHERE title LIKE '%printer paper%'") == 1
    assert result.outcomes and result.outcomes[0]["ok"] is True


def test_orchestrator_reports_step_details(app):
    result = app.orchestrator.handle("how is my system doing")
    payload = result.as_dict()
    assert payload["status"] == "completed"
    assert payload["outcomes"][0]["tool"] == "system_report"
    assert payload["answer"]
    assert payload["task_id"]


def test_orchestrator_security_log(app):
    result = app.orchestrator.handle("check my laptop performance")
    rows = app.db.query("SELECT * FROM agent_runs WHERE goal = ?", (result.text,))
    assert rows and rows[0]["status"] in ("completed", "ok", "done")


def test_state_changing_tools_are_audited(app):
    app.orchestrator.handle("add task Audit me")
    assert any(entry["tool"] == "create_task" for entry in app.audit.recent(limit=20))


def test_orchestrator_pauses_for_approval(app, tmp_path):
    victim = tmp_path / "approval.txt"
    victim.write_text("x", encoding="utf-8")
    result = app.orchestrator.handle(f"delete the file {victim}")
    assert result.status == "awaiting_approval"
    approval_id = next(o["requires_approval"] for o in result.outcomes if o.get("requires_approval"))
    assert approval_id
    assert victim.exists()

    app.permissions.decide(approval_id, True)
    resumed = app.orchestrator.resume(result.task_id, approval_id)
    assert resumed.status == "completed"
    assert not victim.exists()


def test_orchestrator_cancel_unknown_task(app):
    assert app.orchestrator.cancel("no-such-task") is False


def test_orchestrator_cancel_signals_running_task(app):
    import threading
    import time

    state = {"cancel": threading.Event()}
    app.orchestrator._tasks["manual-task"] = state
    try:
        assert app.orchestrator.cancel("manual-task") is True
        assert state["cancel"].is_set() is True
    finally:
        app.orchestrator._tasks.pop("manual-task", None)
    time.sleep(0)


def test_orchestrator_resume_unknown_task(app):
    result = app.orchestrator.resume("missing-task", 1)
    assert result.status == "error"
    assert "approval" in result.error.lower()


def test_arg_interpolation_between_steps(app):
    outcomes = [{"ok": True, "tool": "create_note", "data": {"note": {"id": 7}}}]
    resolved = app.orchestrator._resolve_args({"note_id": "$1.note.id"}, outcomes)
    assert resolved["note_id"] == 7


def test_offline_chat_explains_itself(app):
    answer = app.orchestrator.chat("Tell me a joke")
    assert "provider" in answer.lower()
    assert "settings" in answer.lower() or "local" in answer.lower()


def test_agent_selection(app):
    agent = app.agents.select("write a python function to sort files")
    assert agent.name
    assert app.agents.get(agent.name) is not None
    assert len(app.agents.list()) >= 12


def test_plan_parsing_from_ai_json(app):
    raw = '{"steps": [{"tool": "list_tasks", "args": {}}], "answer": "Checking"}'
    plan = app.planner._parse_plan(raw, "show tasks", None)
    assert plan.steps and plan.steps[0].tool == "list_tasks"
    assert plan.answer == "Checking"


def test_plan_rejects_unknown_tools(app):
    raw = '{"steps": [{"tool": "rm_rf_everything", "args": {}}]}'
    plan = app.planner._parse_plan(raw, "do bad things", None)
    assert plan.steps == []


def test_plan_limits_steps(app):
    steps = ", ".join('{"tool": "list_tasks", "args": {}}' for _ in range(50))
    plan = app.planner._parse_plan(f'{{"steps": [{steps}]}}', "loop", None)
    limit = int(app.config.get("brain.max_steps", 8))
    assert len(plan.steps) <= limit
    assert "step limit" in plan.notes


def test_conversational_fallback_uses_chat_provider_when_planning_fails(app, monkeypatch):
    from ray.brain.planner import Plan

    monkeypatch.setattr(
        app.planner,
        "plan",
        lambda *args, **kwargs: Plan(goal="hi", agent="assistant", source="offline", answer="offline notice"),
    )
    monkeypatch.setattr(app.orchestrator, "_chat_result", lambda *args, **kwargs: "Cursor says hello.")
    result = app.orchestrator.handle("tell me a joke")
    assert result.status == "answered"
    assert result.answer.startswith("Cursor says hello.")
    assert "without tools" in result.answer


def test_conversational_fallback_keeps_offline_notice_when_chat_fails(app, monkeypatch):
    from ray.ai.base import ProviderUnavailable
    from ray.brain.planner import Plan

    monkeypatch.setattr(
        app.planner,
        "plan",
        lambda *args, **kwargs: Plan(goal="hi", agent="assistant", source="offline", answer="offline notice"),
    )

    def explode(*args, **kwargs):
        raise ProviderUnavailable("no providers")

    monkeypatch.setattr(app.orchestrator, "_chat_result", explode)
    assert app.orchestrator.handle("tell me a joke").answer == "offline notice"
