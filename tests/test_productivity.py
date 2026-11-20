"""Tasks, notes, reminders and calendar behaviour."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ray.productivity.common import next_occurrence, parse_dt


def test_task_crud(app):
    task = app.tasks.create("Finish notes", priority="high", category="law")
    assert task["status"] == "open"
    assert task["priority"] == "high"
    updated = app.tasks.update(task["id"], status="in_progress", priority="urgent")
    assert updated["status"] == "in_progress"
    assert updated["priority"] == "urgent"
    assert app.tasks.delete(task["id"]) is True
    with pytest.raises(KeyError):
        app.tasks.get(task["id"])


def test_task_validation(app):
    with pytest.raises(ValueError):
        app.tasks.create("   ")
    task = app.tasks.create("Check validation")
    with pytest.raises(ValueError):
        app.tasks.update(task["id"], priority="impossible")


def test_task_completion_and_recurrence(app):
    due = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    task = app.tasks.create("Daily revision", due_at=due, recurrence="daily")
    result = app.tasks.complete(task["id"])
    assert result["task"]["status"] == "done"
    assert result["task"]["completed_at"]
    assert result["next"] is not None
    assert result["next"]["status"] == "open"
    assert parse_dt(result["next"]["due_at"]) > parse_dt(due)


def test_task_sorting_and_stats(app):
    app.tasks.create("Normal task", priority="normal")
    urgent = app.tasks.create("Urgent task", priority="urgent")
    tasks = app.tasks.list()
    assert tasks[0]["id"] == urgent["id"]
    stats = app.tasks.stats()
    assert stats["open"] == 2
    assert stats["completion_rate"] == 0.0


def test_overdue_detection(app):
    past = (datetime.now() - timedelta(days=2)).replace(microsecond=0).isoformat()
    app.tasks.create("Late thing", due_at=past)
    overdue = [task for task in app.tasks.list() if task["overdue"]]
    assert len(overdue) == 1


def test_notes_crud_and_search(app):
    note = app.notes.create(title="Companies Act", body="Section 180 covers borrowing powers.", category="law")
    assert app.notes.get(note["id"])["title"] == "Companies Act"
    assert app.notes.search("borrowing")[0]["id"] == note["id"]
    pinned = app.notes.toggle_pin(note["id"])
    assert pinned["pinned"] == 1
    assert app.notes.list()[0]["id"] == note["id"]
    export = app.notes.export_markdown(app.paths.data / "notes.md")
    assert "borrowing powers" in export.read_text(encoding="utf-8")
    assert app.notes.delete(note["id"]) is True


def test_notes_auto_title(app):
    note = app.notes.create(body="Remember to call the registrar tomorrow morning")
    assert note["title"].startswith("Remember to call")


def test_reminder_fires_and_reschedules(app):
    due = (datetime.now() - timedelta(seconds=5)).replace(microsecond=0).isoformat()
    reminder = app.reminders.create("Drink water", due_at=due, recurrence="every:1h")
    fired = app.reminders.due()
    assert any(item["id"] == reminder["id"] for item in fired)
    refreshed = app.reminders.get(reminder["id"])
    assert parse_dt(refreshed["due_at"]) > datetime.now()


def test_one_shot_reminder_disables_after_firing(app):
    due = (datetime.now() - timedelta(minutes=1)).replace(microsecond=0).isoformat()
    reminder = app.reminders.create("One off", due_at=due)
    app.reminders.due()
    assert app.reminders.get(reminder["id"])["enabled"] == 0


def test_reminder_validation_and_snooze(app):
    with pytest.raises(ValueError):
        app.reminders.create("Bad", due_at="not a date")
    reminder = app.reminders.create("Snooze me", due_at=datetime.now().isoformat(timespec="seconds"))
    snoozed = app.reminders.snooze(reminder["id"], 30)
    assert parse_dt(snoozed["due_at"]) > datetime.now() + timedelta(minutes=25)


def test_calendar_views_and_events(app):
    start = datetime.now().replace(microsecond=0)
    event = app.calendar.create("Study block", start_at=start, end_at=start + timedelta(hours=2), location="Desk")
    assert app.calendar.today()
    month = app.calendar.month_view()
    assert len(month["weeks"]) >= 4
    week = app.calendar.week_view()
    assert len(week["days"]) == 7
    day = app.calendar.day_view()
    assert any(item["id"] == event["id"] for item in day["events"])
    assert app.calendar.delete(event["id"]) is True


def test_calendar_rejects_reversed_times(app):
    start = datetime.now()
    with pytest.raises(ValueError):
        app.calendar.create("Bad", start_at=start, end_at=start - timedelta(hours=1))


def test_recurrence_helpers():
    base = datetime(2026, 11, 20, 9, 0)
    assert next_occurrence(base, "daily") == datetime(2026, 11, 21, 9, 0)
    assert next_occurrence(base, "weekly") == datetime(2026, 11, 27, 9, 0)
    assert next_occurrence(base, "every:2d") == datetime(2026, 11, 22, 9, 0)
    assert next_occurrence(base, "every:30m") == datetime(2026, 11, 20, 9, 30)
    assert next_occurrence(base, "monthly") == datetime(2026, 12, 20, 9, 0)
    assert next_occurrence(base, None) is None
    # weekly on Fridays only
    weekday_next = next_occurrence(datetime(2026, 11, 20, 9, 0), "weekdays")
    assert weekday_next.weekday() < 5


def test_focus_and_habits(app):
    session = app.focus.start_focus(25, "Writing")
    app.focus.finish_focus(session["id"], completed=True)
    stats = app.focus.focus_stats()
    assert stats["total_minutes"] == 25

    habit = app.focus.add_habit("Read")
    app.focus.log_habit(habit["id"])
    habits = app.focus.habits()
    assert habits[0]["today"] == 1
    assert habits[0]["streak"] == 1
