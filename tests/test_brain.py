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


# ------------------------------------------------------- plain English commands
PLAIN_ENGLISH = [
    ("open chrome", "open_app", {"app": "chrome"}),
    ("open youtube", "open_url", {"url": "https://www.youtube.com"}),
    ("go to gmail", "open_url", {"url": "https://mail.google.com"}),
    ("open github.com", "open_url", {"url": "https://github.com"}),
    ("what's my cpu usage", "system_report", {}),
    ("how much ram am i using", "system_report", {}),
    ("how much disk space is left", "system_report", {}),
    ("what is using my cpu", "list_processes", {"sort_by": "cpu"}),
    ("show processes", "list_processes", {}),
    ("capture my screen", "screenshot", {}),
    ("set volume to 40", "set_volume", {"percent": 40}),
    ("make it louder", "volume", {"direction": "up"}),
    ("lock my pc", "power_lock", {"action": "lock"}),
    ("close chrome", "close_app", {"name": "chrome"}),
    ("open bluetooth settings", "open_panel", {"panel": "bluetooth"}),
    ("read my clipboard", "clipboard_get", {}),
    ("copy hello world to my clipboard", "clipboard_set", {"text": "hello world"}),
    ("type hello world", "type_text", {"text": "hello world"}),
    ("press ctrl+s", "press_hotkey", {"keys": ["ctrl", "s"]}),
    ("index status", "index_status", {}),
    ("start a focus session for 25 minutes", "start_focus", {"minutes": 25}),
    ("what do you remember about me", "recall", {}),
    ("research cheap SSDs", "research", {}),
    ("show my bookmarks", "list_bookmarks", {}),
    ("search my files for invoice", "search_files", {"query": "invoice"}),
    ("how many tasks do I have", "list_tasks", {}),
    ("help", "capabilities", {}),
    ("what can you do", "capabilities", {}),
    ("hello", "smalltalk", {}),
    ("who are you", "smalltalk", {}),
    ("turn off wifi", "explain", {}),
    ("empty the trash", "explain", {}),
]


@pytest.mark.parametrize("text,expected_kind,expected_args", PLAIN_ENGLISH)
def test_plain_english_maps_to_the_right_intent(parser, text, expected_kind, expected_args):
    kind, args = kinds(text, parser)
    assert kind == expected_kind, f"{text!r} -> {kind}"
    for key, value in expected_args.items():
        assert args.get(key) == value, f"{text!r}: {key}={args.get(key)!r}"


def test_reminder_title_drops_the_time_phrase(parser):
    kind, args = kinds("remind me in 20 minutes to stretch", parser)
    assert kind == "create_reminder"
    assert args["title"].lower() == "stretch"
    assert args["due_at"]

    kind, args = kinds("remind me at 7pm to call mom", parser)
    assert args["title"].lower() == "call mom"


def test_task_title_drops_the_leading_to(parser):
    assert kinds("add a task to buy milk", parser)[1]["title"] == "buy milk"


def test_event_title_reads_naturally(parser):
    kind, args = kinds("add a meeting with team on friday at 10", parser)
    assert kind == "create_event"
    assert args["title"].lower() == "meeting with team"


def test_every_local_intent_points_at_a_real_tool_and_agent(app):
    """The parser and the tool schema must not drift apart."""
    from ray.brain.planner import INTENT_PLANS

    for kind, (tool, agent, _why) in INTENT_PLANS.items():
        assert agent in app.orchestrator.agents.agents, f"{kind}: unknown agent {agent}"
        assert tool in app.tools, f"{kind}: unknown tool {tool}"


def test_local_plans_satisfy_the_tool_schema(app):
    """A plan that misses a required argument fails at run time; catch it here."""
    from ray.brain.planner import ANSWER_INTENTS, ARG_ALIASES, INTENT_PLANS

    checked = 0
    for text, _kind, _args in PLAIN_ENGLISH:
        plan = app.planner.plan_local(text)
        if plan is None or not plan.steps:
            continue
        for step in plan.steps:
            tool = app.tools.get(step.tool)
            assert tool is not None, step.tool
            required = (tool.parameters or {}).get("required", [])
            missing = [name for name in required if name not in step.args]
            assert not missing, f"{text!r} -> {step.tool} missing {missing}"
            checked += 1
    assert checked >= 15  # the sample really exercised the tool plans


def test_offline_english_commands_need_no_provider(app):
    """No keys: a greeting and a system question still get a real answer."""
    greeting = app.orchestrator.handle("hello")
    assert greeting.status == "answered"
    assert "ray" in greeting.answer.lower()
    assert "provider" not in greeting.answer.lower()

    report = app.orchestrator.handle("how much ram am i using")
    assert report.status == "completed"
    assert report.plan["steps"][0]["tool"] == "system_report"
    assert "RAM" in report.answer or "memory" in report.answer.lower()


def test_unknown_request_still_explains_what_is_possible(app):
    result = app.orchestrator.handle("compose a sonnet about the monsoon")
    assert "local things" in result.answer or "provider" in result.answer.lower()


def test_agent_tab_is_a_hint_not_a_cage(app):
    """A plain command typed in the wrong tab must still run."""
    result = app.orchestrator.handle("system report", agent="productivity")
    assert result.agent == "system"
    assert result.status == "completed"

    honored = app.orchestrator.handle("how much ram am i using", agent="system")
    assert honored.agent == "system"
