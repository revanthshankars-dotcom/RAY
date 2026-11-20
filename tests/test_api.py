"""The local HTTP API that the interface talks to."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest


def test_health_and_status(client):
    health = client.get("/api/health")
    assert health["ok"] is True
    assert health["data"]["tools"] >= 70

    status = client.get("/api/status")
    data = status["data"]
    assert data["version"]
    assert data["tools"]["count"] >= 70
    assert "providers" in data
    assert data["home"]


def test_dashboard_shape(client):
    data = client.get("/api/dashboard")["data"]
    for key in ("greeting", "date_label", "tasks_stats", "schedule", "reminders",
                "recent_files", "notifications", "automation", "security", "offline"):
        assert key in data
    assert isinstance(data["schedule"], list)
    assert "total" in data["tasks_stats"]


def test_task_crud_over_http(client):
    created = client.post("/api/tasks", {"title": "API task", "priority": "high"})
    assert created["ok"] is True
    task_id = created["data"]["id"]

    listed = client.get("/api/tasks")["data"]
    assert any(task["id"] == task_id for task in listed["tasks"])

    patched = client.patch(f"/api/tasks/{task_id}", {"status": "done"})
    assert patched["data"]["status"] == "done"

    completed = client.post(f"/api/tasks/{task_id}/complete", {})
    assert completed["ok"] is True

    deleted = client.delete(f"/api/tasks/{task_id}")
    assert deleted["data"]["deleted"] is True


def test_task_validation_error(client):
    response = client.post("/api/tasks", {"title": ""})
    assert response["ok"] is False
    assert "title" in response["error"].lower() or "empty" in response["error"].lower()


def test_notes_endpoint(client):
    note = client.post("/api/notes", {"title": "API note", "body": "hello"})["data"]
    assert note["id"]
    assert any(item["id"] == note["id"] for item in client.get("/api/notes")["data"]["notes"])


def test_command_executes_and_logs(client):
    result = client.post("/api/command", {"text": "check my laptop performance"})["data"]
    assert result["status"] == "completed"
    assert "system report" in result["answer"].lower()
    assert result["outcomes"][0]["ok"] is True


def test_command_requires_text(client):
    response = client.post("/api/command", {"text": "  "})
    assert response["ok"] is False


def test_command_approval_round_trip(client, tmp_path):
    victim = tmp_path / "api-delete.txt"
    victim.write_text("bye", encoding="utf-8")
    result = client.post("/api/command", {"text": f"delete the file {victim}"})["data"]
    assert result["status"] == "awaiting_approval"
    approval_id = next(item["requires_approval"] for item in result["outcomes"] if item.get("requires_approval"))

    approvals = client.get("/api/approvals")["data"]["pending"]
    assert any(item["id"] == approval_id for item in approvals)

    decided = client.post("/api/approvals", {
        "approval_id": approval_id, "approved": True, "task_id": result["task_id"],
    })["data"]
    assert decided["decided"] is True
    assert decided["resumed"]["status"] == "completed"
    assert not victim.exists()


def test_tools_listing_and_call(client):
    tools = client.get("/api/tools")["data"]
    assert tools["summary"]["count"] >= 70
    assert len(tools["tools"]) >= 70
    assert tools["catalog"]

    called = client.post("/api/tools/call", {"tool": "list_tasks", "args": {}})
    assert called["ok"] is True

    unknown = client.post("/api/tools/call", {"tool": "not_a_tool", "args": {}})
    assert unknown["ok"] is False


def test_unknown_route_returns_json_404(client):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"{client.base}/api/does-not-exist", timeout=10)
    assert excinfo.value.code == 404
    payload = json.loads(excinfo.value.read())
    assert payload["ok"] is False


def test_bad_json_body_is_reported(client):
    request = urllib.request.Request(
        client.base + "/api/tasks", data=b"{not json",
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 400


def test_credentials_never_echoed(client, app):
    saved = client.post("/api/credentials", {"name": "openrouter", "value": "sk-or-very-secret-value"})
    assert saved["ok"] is True
    assert "sk-or-very-secret-value" not in json.dumps(saved)
    assert saved["data"]["saved"] is True

    listed = client.get("/api/providers")
    assert "sk-or-very-secret-value" not in json.dumps(listed)
    openrouter = next(item for item in listed["data"]["providers"]["providers"] if item["name"] == "openrouter")
    assert openrouter["configured"] is True

    removed = client.delete("/api/credentials", {"name": "openrouter"})
    assert removed["ok"] is True
    assert app.credentials.get("openrouter") is None


def test_provider_test_without_key_explains(client):
    result = client.post("/api/providers/test", {"name": "gemini"})
    assert result["ok"] is True
    assert result["data"]["available"] is False
    assert "key" in result["data"]["detail"].lower()


def test_settings_round_trip(client):
    settings = client.get("/api/settings")["data"]
    assert "config" in settings
    assert "capabilities" in settings

    updated = client.post("/api/settings", {"values": {"app.user_name": "Revanth"}})
    assert updated["ok"] is True
    assert client.get("/api/settings")["data"]["config"]["app"]["user_name"] == "Revanth"


def test_settings_rejects_secret_looking_values(client):
    response = client.post("/api/settings", {"values": {"app.user_name": "sk-abcdefghijklmnopqrstuvwxyz"}})
    assert response["ok"] is False
    assert "credential" in response["error"].lower()


def test_security_center_payload(client):
    data = client.get("/api/security")["data"]
    for key in ("policy", "pending", "audit", "sensitive_paths", "server_exposed"):
        assert key in data
    assert isinstance(data["policy"]["levels"], dict)
    assert "mode" in data["policy"]
    assert len(data["policy"]["levels"]) == 3


def test_emergency_stop_blocks_tools(client):
    client.post("/api/security/emergency", {"active": True})
    try:
        result = client.post("/api/tools/call", {"tool": "create_task", "args": {"title": "blocked"}})
        assert result["ok"] is False
        assert "emergency" in result["error"].lower()
    finally:
        client.post("/api/security/emergency", {"active": False})
    assert client.post("/api/tools/call", {"tool": "create_task", "args": {"title": "allowed"}})["ok"] is True


def test_events_cursor(client):
    first = client.get("/api/events?after=0")["data"]
    assert "events" in first and "latest" in first
    client.post("/api/tasks", {"title": "event trigger"})
    later = client.get(f"/api/events?after={first['latest']}")["data"]
    assert any(event["kind"].startswith("task") for event in later["events"])


def test_knowledge_endpoints(client, tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    (root / "act.txt").write_text("Borrowing powers and board approval.", encoding="utf-8")

    added = client.post("/api/knowledge/roots", {"path": str(root)})
    assert added["ok"] is True

    indexed = client.post("/api/knowledge/index", {})
    assert indexed["ok"] is True

    search = client.get("/api/knowledge/search?query=borrowing")["data"]
    assert search["count"] >= 1
    assert search["results"][0]["name"] == "act.txt"


def test_memory_endpoints(client):
    created = client.post("/api/memory", {"title": "API memory", "content": "remember this"})
    assert created["ok"] is True
    assert created["data"]["id"]
    assert any(item["title"] == "API memory" for item in client.get("/api/memory")["data"]["memories"])

    refusal = client.post("/api/memory", {"title": "key", "content": "xai-abcdefghijklmnopqrstuvwxyz123456"})
    assert refusal["ok"] is False


def test_automation_endpoints(client):
    created = client.post("/api/automation", body={
        "name": "API workflow",
        "trigger": {"type": "manual"},
        "actions": [{"type": "notify", "title": "api notify"}],
    })
    assert created["ok"] is True
    workflow_id = created["data"]["id"]

    run = client.post(f"/api/automation/{workflow_id}/run", {})
    assert run["data"]["status"] == "ok"

    toggled = client.post(f"/api/automation/{workflow_id}/toggle", {"enabled": False})
    assert toggled["data"]["enabled"] is False

    assert client.delete(f"/api/automation/{workflow_id}")["ok"] is True


def test_static_files_are_served(server):
    for path, marker in (("/", b"RAY"), ("/app.js", b"function"), ("/styles.css", b"--")):
        with urllib.request.urlopen(f"{server.url}{path}", timeout=10) as response:
            assert response.status == 200
            body = response.read()
            assert marker in body
            assert len(body) > 500


def test_path_traversal_is_refused(server):
    with urllib.request.urlopen(f"{server.url}/../../etc/passwd", timeout=10) as response:
        body = response.read()
    assert b"root:" not in body


def test_system_endpoint_is_real(client):
    payload = client.get("/api/system")["data"]
    data = payload["snapshot"]
    assert data["memory"]["percent"] >= 0
    assert data["cpu"]["cores_logical"] >= 1
    assert data["identity"]["hostname"]
    assert payload["processes"] is not None
    assert payload["report"].startswith("RAY system report")


def test_analytics_shape(client):
    data = client.get("/api/analytics")["data"]
    assert "usage" in data
    assert "focus" in data
    assert "database" in data
    assert data["database"]["size_bytes"] > 0


def test_logs_endpoint_redacts_secrets(client, app):
    app.audit.record("probe", target="/tmp/x")
    payload = json.dumps(client.get("/api/logs?lines=20"))
    assert "xai-" not in payload

def test_cross_origin_writes_are_refused(server):
    request = urllib.request.Request(
        server.url + "/api/tasks",
        data=json.dumps({"title": "csrf"}).encode(),
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 403


def test_loopback_origin_is_accepted(server):
    request = urllib.request.Request(
        server.url + "/api/tasks",
        data=json.dumps({"title": "same origin"}).encode(),
        headers={"Content-Type": "application/json", "Origin": server.url},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert json.loads(response.read())["ok"] is True


def test_non_json_posts_are_refused(server):
    request = urllib.request.Request(
        server.url + "/api/tasks",
        data=b'{"title": "form post"}',
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 415


def test_exposed_server_still_blocks_foreign_origins(server):
    # simulate a network-bound server: same-origin proxy traffic is fine,
    # a page from another site is not
    from ray.server import app as server_module

    previous = dict(server_module._CURRENT)
    server_module._CURRENT["exposed"] = True
    try:
        foreign = urllib.request.Request(
            server.url + "/api/tasks",
            data=json.dumps({"title": "nope"}).encode(),
            headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(foreign, timeout=10)
        assert excinfo.value.code == 403

        same = urllib.request.Request(
            server.url + "/api/tasks",
            data=json.dumps({"title": "proxy traffic"}).encode(),
            headers={"Content-Type": "application/json",
                     "Origin": server.url.rstrip("/"),
                     "Host": server.url.rstrip("/").split("//")[1]},
            method="POST",
        )
        with urllib.request.urlopen(same, timeout=10) as response:
            assert json.loads(response.read())["ok"] is True
    finally:
        server_module._CURRENT.clear()
        server_module._CURRENT.update(previous)


def test_response_headers_are_locked_down(server):
    with urllib.request.urlopen(server.url + "/api/health", timeout=10) as response:
        headers = response.headers
        assert headers["Access-Control-Allow-Origin" if "Access-Control-Allow-Origin" in headers else "X-Content-Type-Options"] == "nosniff"
        assert "default-src 'self'" in headers["Content-Security-Policy"]
        assert headers["Referrer-Policy"] == "no-referrer"


def test_engines_endpoint_describes_the_policy(client):
    data = client.get("/api/ai/engines")["data"]
    assert data["policy"] == "preference"
    assert data["cap_behaviour"] == "stop"
    assert data["preference"] == ["openrouter", "gemini"]
    engines = {item["name"]: item for item in data["engines"]}
    assert set(engines) == {"openrouter", "gemini", "fish"}
    assert engines["openrouter"]["credit"]["monthly_usd"] == 0
    assert engines["openrouter"]["configured"] is False
    # the payload explains itself, without ever carrying a key
    assert "sk-or" not in json.dumps(data)
    assert data["active"]["usable"] == []


def test_engines_patch_saves_order_caps_and_behaviour(client, app):
    result = client.patch("/api/ai/engines", {
        "order": ["gemini", "openrouter"],
        "policy": "credits",
        "cap_behaviour": "switch",
        "pinned": "gemini",
        "credits": {"openrouter": {"monthly_usd": 4.5, "monthly_calls": 200}},
    })
    assert result["ok"] is True, result
    data = result["data"]
    assert data["preference"] == ["gemini", "openrouter"]
    assert data["policy"] == "credits"
    assert data["cap_behaviour"] == "switch"
    assert data["pinned"] == "gemini"
    assert data["credits"]["openrouter"]["monthly_usd"] == 4.5
    assert app.config.get("ai.credits.openrouter.monthly_calls") == 200
    assert app.audit.search("ai_engine_policy_updated") or app.db.query(
        "SELECT * FROM audit_log WHERE action = 'ai_engine_policy_updated'"
    )


def test_engines_patch_rejects_nonsense(client, app):
    for body in (
        {"order": []},
        {"order": ["grok"]},
        {"policy": "vibes"},
        {"cap_behaviour": "spend-anyway"},
        {"pinned": "fish"},
        {"credits": {"openrouter": {"monthly_usd": -3}}},
        {"credits": {"openrouter": {"monthly_calls": "lots"}}},
        {"credits": {"cursor": {"monthly_usd": 1}}},
    ):
        result = client.patch("/api/ai/engines", body)
        assert result["ok"] is False, body
        assert result["error"], body
    assert app.config.get("ai.engine_policy") == "preference"


def test_command_accepts_a_per_request_engine(client):
    rejected = client.post("/api/command", {"text": "hello", "engine": "grok"})
    assert rejected["ok"] is False
    assert "engine" in rejected["error"].lower()
    offline = client.post("/api/command", {"text": "open my documents folder", "engine": "gemini"})
    assert offline["ok"] is True, offline
    assert offline["data"]["status"] in ("completed", "failed", "needs_approval")


def test_security_scan_reports_real_checks(client, app):
    before = app.audit.stats()["total"]
    result = client.post("/api/security/scan", {})
    assert result["ok"] is True
    data = result["data"]
    assert data["total"] >= 7
    assert data["passed"] == data["total"]
    assert data["ok"] is True
    names = {check["name"] for check in data["checks"]}
    assert {"Database integrity", "Configuration", "Credential vault", "Network exposure"} <= names
    assert app.audit.stats()["total"] > before


def test_preference_policy_keeps_the_saved_order(client, app, monkeypatch):
    from ray.ai.cache import UsageTracker

    monkeypatch.setenv("RAY_OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setenv("RAY_GEMINI_API_KEY", "AIza-test-key")
    app.router.rebuild()
    UsageTracker(app.db).record("openrouter", "m", "chat", cost_usd=9.0)
    client.patch("/api/ai/engines", {
        "policy": "preference",
        "order": ["openrouter", "gemini"],
        "credits": {"openrouter": {"monthly_usd": 10}, "gemini": {"monthly_usd": 10}},
    })
    active = client.get("/api/ai/engines")["data"]["active"]
    assert active["order"] == ["openrouter", "gemini"]
    assert active["primary"] == "openrouter"


def test_credits_policy_puts_the_engine_with_most_headroom_first(client, app, monkeypatch):
    from ray.ai.cache import UsageTracker

    monkeypatch.setenv("RAY_OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setenv("RAY_GEMINI_API_KEY", "AIza-test-key")
    app.router.rebuild()
    # OpenRouter is 90% through its cap, Gemini has not spent anything.
    UsageTracker(app.db).record("openrouter", "m", "chat", cost_usd=9.0)
    client.patch("/api/ai/engines", {
        "policy": "credits",
        "order": ["openrouter", "gemini"],
        "credits": {"openrouter": {"monthly_usd": 10}, "gemini": {"monthly_usd": 10}},
    })
    data = client.get("/api/ai/engines")["data"]
    assert data["policy"] == "credits"
    assert data["active"]["order"] == ["gemini", "openrouter"]
    assert data["active"]["primary"] == "gemini"
    assert data["credits"]["openrouter"]["percent"] == 90.0


def test_manual_policy_serves_only_the_pinned_engine(client, app, monkeypatch):
    monkeypatch.setenv("RAY_OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setenv("RAY_GEMINI_API_KEY", "AIza-test-key")
    app.router.rebuild()
    client.patch("/api/ai/engines", {"policy": "manual", "pinned": "gemini"})
    active = client.get("/api/ai/engines")["data"]["active"]
    assert active["usable"] == ["gemini"]
    assert active["order"][0] == "gemini"


def test_status_reports_the_user_name_for_the_monogram(client, app):
    client.post("/api/settings", {"values": {"app.user_name": "Revanth Shankar"}})
    status = client.get("/api/status")["data"]
    assert status["user_name"] == "Revanth Shankar"
    dashboard = client.get("/api/dashboard")["data"]
    assert dashboard["user_name"] == "Revanth Shankar"
