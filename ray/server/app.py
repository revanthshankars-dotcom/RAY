"""Local UI server.

A small threaded HTTP server (stdlib only) that serves the RAY interface and a
JSON API over the real engine. The desktop shell embeds the same UI in a native
window; a browser works too.

No framework, no build step, no CDN - it must run on a Celeron with 8 GB RAM and
still be usable offline.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__
from ..core.logging_setup import get_logger
from ..security.permissions import LEVEL_AUTO

log = get_logger("server")

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
MAX_BODY = 2 * 1024 * 1024


class RayServer:
    def __init__(self, app, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.app = app
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- lifecycle
    def start(self, background: bool = True) -> "RayServer":
        handler = _make_handler(self.app)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        self._httpd.allow_reuse_address = True
        actual_port = self._httpd.server_address[1]
        self.port = actual_port
        if background:
            self._thread = threading.Thread(target=self._httpd.serve_forever, name="ray-http", daemon=True)
            self._thread.start()
        log.info("UI server listening on http://%s:%s", self.host, actual_port)
        return self

    def serve_forever(self) -> None:
        if self._httpd is None:
            self.start(background=False)
        assert self._httpd is not None
        self._httpd.serve_forever()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=3)
        log.info("UI server stopped")

    @property
    def url(self) -> str:
        host = "localhost" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}/"

    @property
    def exposed(self) -> bool:
        return self.host not in ("127.0.0.1", "localhost", "::1")


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, default=str))


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"RAY/{__version__}"
        protocol_version = "HTTP/1.1"

        # ------------------------------------------------------------- plumbing
        def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; media-src 'self' blob: data:; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; form-action 'none'",
            )
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(_json_safe(payload), default=str).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _error(self, message: str, status: int = 400) -> None:
            self._json({"ok": False, "error": message}, status)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                # A cross-site form post cannot set this header, so this simple
                # check blocks the classic localhost CSRF attack.
                raise ApiError("requests must use Content-Type: application/json", 415)
            if length > MAX_BODY:
                raise ApiError("request body too large", 413)
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ApiError(f"invalid JSON body: {exc}") from exc
            return payload if isinstance(payload, dict) else {"value": payload}

        @staticmethod
        def _query(parsed) -> dict[str, str]:
            return {key: values[0] for key, values in parse_qs(parsed.query).items()}

        # --------------------------------------------------------------- routes
        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(204, b"", "text/plain")

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PATCH(self) -> None:  # noqa: N802
            self._dispatch("PATCH")

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch("DELETE")

        def _origin_ok(self) -> bool:
            """Refuse browser requests from other sites.

            When RAY listens on loopback only, a page on the internet must not be
            able to drive the local engine. Native tools (curl, the tests) send no
            Origin header and are unaffected; when the user deliberately binds to
            the network, they have accepted that exposure and the check is skipped.
            """
            origin = (self.headers.get("Origin") or "").strip()
            if not origin:
                return True
            target = urlparse(origin)
            if target.scheme not in ("http", "https"):
                return False
            # Same origin as the page that is being served: always fine (this is
            # also how a reverse proxy / preview host looks).
            host_header = (self.headers.get("Host") or "").strip().lower()
            if host_header and target.netloc.lower() == host_header:
                return True
            if _CURRENT.get("exposed", False):
                # Deliberately network-bound: a reverse proxy may rewrite hosts,
                # so fall back to allowing the local addresses only.
                return target.hostname in ("127.0.0.1", "localhost", "::1")
            port = self.server.server_port
            return target.hostname in ("127.0.0.1", "localhost", "::1") and (
                target.port in (None, port)
            )

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            try:
                if method in ("POST", "PATCH", "DELETE") and not self._origin_ok():
                    self._error("cross-origin requests are not allowed", 403)
                    return
                if not path.startswith("/api/"):
                    self._serve_static(path)
                    return
                route = ROUTES.get((method, path)) or ROUTES.get(("*", path))
                if route is None:
                    dynamic = _match_dynamic(method, path)
                    if dynamic is None:
                        self._error(f"no route for {method} {path}", 404)
                        return
                    handler, params = dynamic
                else:
                    handler, params = route, {}
                try:
                    body = self._body() if method in ("POST", "PATCH", "DELETE") else {}
                except ApiError as exc:
                    self._error(exc.message, exc.status)
                    return
                result = handler(
                    app,
                    body=body,
                    query=self._query(parsed),
                    params=params,
                    method=method,
                    handler=self,
                )
                if isinstance(result, bytes):
                    return  # handler already wrote the response
                self._json({"ok": True, "data": result})
            except ApiError as exc:
                self._error(exc.message, exc.status)
            except FileNotFoundError as exc:
                self._error(str(exc), 404)
            except PermissionError as exc:
                self._error(str(exc), 403)
            except (ValueError, KeyError) as exc:
                self._error(str(exc).strip("'\""), 400)
            except Exception as exc:
                log.error("API error on %s %s: %s", method, path, exc)
                log.debug("%s", traceback.format_exc())
                self._error(f"{exc.__class__.__name__}: {exc}", 500)

        # --------------------------------------------------------------- static
        def _serve_static(self, path: str) -> None:
            relative = path.lstrip("/") or "index.html"
            if relative.endswith("/"):
                relative += "index.html"
            candidate = (UI_DIR / relative).resolve()
            if not str(candidate).startswith(str(UI_DIR.resolve())) or not candidate.exists() or candidate.is_dir():
                candidate = UI_DIR / "index.html"
            if not candidate.exists():
                self._send(404, b"RAY UI files are missing", "text/plain; charset=utf-8")
                return
            content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
            if candidate.suffix == ".js":
                content_type = "application/javascript; charset=utf-8"
            if candidate.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            body = candidate.read_bytes()
            self._send(200, body, content_type, {"Cache-Control": "no-cache"})

    return Handler


# ===================================================================== routes
ROUTES: dict[tuple[str, str], Callable[..., Any]] = {}


def route(method: str, path: str):
    def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
        ROUTES[(method, path)] = function
        return function

    return decorator


DYNAMIC: list[tuple[str, re.Pattern, Callable[..., Any]]] = []


def dynamic_route(method: str, pattern: str):
    compiled = re.compile(f"^{pattern}$")

    def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
        DYNAMIC.append((method, compiled, function))
        return function

    return decorator


def _match_dynamic(method: str, path: str):
    for route_method, pattern, function in DYNAMIC:
        if route_method not in (method, "*"):
            continue
        match = pattern.match(path)
        if match:
            return function, match.groupdict()
    return None


# ------------------------------------------------------------------- helpers
def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------- system
@route("GET", "/api/health")
def health(app, **_: Any) -> Any:
    return app.health()


@route("GET", "/api/status")
def status(app, **_: Any) -> Any:
    payload = app.status()
    payload["server"] = {"host": _CURRENT.get("host"), "port": _CURRENT.get("port"), "exposed": _CURRENT.get("exposed")}
    return payload


@route("GET", "/api/dashboard")
def dashboard(app, **_: Any) -> Any:
    from datetime import datetime

    now = datetime.now()
    hour = now.hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    name = str(app.config.get("app.user_name", "") or "").strip()
    try:
        snapshot = app.sysmon.snapshot()
    except Exception as exc:
        snapshot = {"error": str(exc)}
    return {
        "greeting": greeting,
        "user_name": name,
        "now": now.isoformat(timespec="seconds"),
        "date_label": now.strftime("%A, %d %B %Y"),
        "system": snapshot,
        "tasks": app.tasks.goals_for_today()[:6],
        "tasks_stats": app.tasks.stats(),
        "schedule": app.calendar.today()[:6],
        "reminders": app.reminders.upcoming(hours=24)[:5],
        "recent_files": app.files.recent_files(limit=6),
        "recent_apps": app.apps.recent(limit=6),
        "notifications": app.db.query("SELECT * FROM notifications ORDER BY id DESC LIMIT 6"),
        "unread_notifications": app.db.scalar("SELECT COUNT(*) FROM notifications WHERE read = 0", default=0),
        "automation": app.automation.describe(),
        "providers": app.router.health(),
        "offline": app.router.is_offline(),
        "widgets": app.config.get("ui.dashboard_widgets", []),
        "voice_enabled": bool(app.config.get("voice.enabled", False)),
        "security": app.permissions.describe(),
    }


@route("GET", "/api/events")
def events(app, query=None, **_: Any) -> Any:
    after = _int((query or {}).get("after"), 0)
    return {"events": app.bus.since(after), "latest": app.bus.latest_id()}


@route("GET", "/api/logs")
def logs(app, query=None, **_: Any) -> Any:
    lines = max(10, min(_int((query or {}).get("lines"), 200), 1000))
    path = app.paths.logs / "ray.log"
    if not path.exists():
        return {"lines": [], "path": str(path)}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        tail = handle.readlines()[-lines:]
    return {"lines": [line.rstrip("\n") for line in tail], "path": str(path)}


# ------------------------------------------------------------------ command
@route("POST", "/api/command")
def command(app, body=None, **_: Any) -> Any:
    body = body or {}
    text = str(body.get("text", "")).strip()
    if not text:
        raise ApiError("nothing to do - the request was empty")
    resume = body.get("resume_task_id")
    approval = body.get("approval_id")
    result = app.orchestrator.handle(
        text,
        agent=body.get("agent"),
        history=body.get("history") if isinstance(body.get("history"), list) else None,
        approval_id=_int(approval, 0) or None,
        resume_task_id=str(resume) if resume else None,
    )
    payload = result.as_dict()
    if body.get("speak") and result.speak:
        app.bus.publish("speak_request", text=result.speak, origin="command")
    return payload


@route("POST", "/api/command/cancel")
def command_cancel(app, body=None, **_: Any) -> Any:
    task_id = str((body or {}).get("task_id", ""))
    return {"cancelled": app.orchestrator.cancel(task_id), "task_id": task_id}


@route("GET", "/api/agents")
def agents(app, **_: Any) -> Any:
    runs = app.db.query(
        "SELECT * FROM agent_runs ORDER BY id DESC LIMIT 25"
    )
    return {"agents": app.agents.list(), "recent_runs": runs}


# -------------------------------------------------------------------- tasks
@route("GET", "/api/tasks")
def tasks_list(app, query=None, **_: Any) -> Any:
    query = query or {}
    rows = app.tasks.list(
        status=query.get("status", "open"),
        category=query.get("category"),
        limit=_int(query.get("limit"), 200),
        include_done=query.get("include_done") == "1",
    )
    return {"tasks": rows, "stats": app.tasks.stats(), "categories": app.tasks.categories()}


@route("POST", "/api/tasks")
def tasks_create(app, body=None, **_: Any) -> Any:
    body = body or {}
    return app.tasks.create(
        title=str(body.get("title", "")),
        description=str(body.get("description", "")),
        priority=str(body.get("priority", "normal")),
        due_at=body.get("due_at") or None,
        category=str(body.get("category", "general")),
        recurrence=body.get("recurrence") or None,
    )


@dynamic_route("PATCH", r"/api/tasks/(?P<task_id>\d+)")
def tasks_update(app, body=None, params=None, **_: Any) -> Any:
    return app.tasks.update(int(params["task_id"]), **(body or {}))


@dynamic_route("DELETE", r"/api/tasks/(?P<task_id>\d+)")
def tasks_delete(app, params=None, **_: Any) -> Any:
    return {"deleted": app.tasks.delete(int(params["task_id"]))}


@dynamic_route("POST", r"/api/tasks/(?P<task_id>\d+)/complete")
def tasks_complete(app, params=None, **_: Any) -> Any:
    return app.tasks.complete(int(params["task_id"]))


# -------------------------------------------------------------------- notes
@route("GET", "/api/notes")
def notes_list(app, query=None, **_: Any) -> Any:
    query = query or {}
    return {
        "notes": app.notes.list(
            category=query.get("category"),
            query=query.get("q"),
            limit=_int(query.get("limit"), 200),
        ),
        "categories": app.notes.categories(),
        "stats": app.notes.stats(),
    }


@route("POST", "/api/notes")
def notes_create(app, body=None, **_: Any) -> Any:
    body = body or {}
    return app.notes.create(
        title=str(body.get("title", "")),
        body=str(body.get("body", "")),
        category=str(body.get("category", "general")),
    )


@dynamic_route("PATCH", r"/api/notes/(?P<note_id>\d+)")
def notes_update(app, body=None, params=None, **_: Any) -> Any:
    return app.notes.update(int(params["note_id"]), **(body or {}))


@dynamic_route("DELETE", r"/api/notes/(?P<note_id>\d+)")
def notes_delete(app, params=None, **_: Any) -> Any:
    return {"deleted": app.notes.delete(int(params["note_id"]))}


# ----------------------------------------------------------------- reminders
@route("GET", "/api/reminders")
def reminders_list(app, query=None, **_: Any) -> Any:
    include_disabled = (query or {}).get("all") == "1"
    return {"reminders": app.reminders.list(include_disabled=include_disabled), "stats": app.reminders.stats()}


@route("POST", "/api/reminders")
def reminders_create(app, body=None, **_: Any) -> Any:
    body = body or {}
    return app.reminders.create(
        title=str(body.get("title", "")),
        due_at=body.get("due_at") or "",
        recurrence=body.get("recurrence") or None,
    )


@dynamic_route("DELETE", r"/api/reminders/(?P<reminder_id>\d+)")
def reminders_delete(app, params=None, **_: Any) -> Any:
    return {"deleted": app.reminders.delete(int(params["reminder_id"]))}


@dynamic_route("POST", r"/api/reminders/(?P<reminder_id>\d+)/snooze")
def reminders_snooze(app, body=None, params=None, **_: Any) -> Any:
    return app.reminders.snooze(int(params["reminder_id"]), _int((body or {}).get("minutes"), 10))


# ------------------------------------------------------------------ calendar
@route("GET", "/api/calendar")
def calendar_view(app, query=None, **_: Any) -> Any:
    query = query or {}
    view = query.get("view", "month")
    if view == "week":
        return app.calendar.week_view()
    if view == "day":
        return app.calendar.day_view()
    year = _int(query.get("year"), 0) or None
    month = _int(query.get("month"), 0) or None
    return app.calendar.month_view(year, month)


@route("POST", "/api/calendar")
def calendar_create(app, body=None, **_: Any) -> Any:
    body = body or {}
    return app.calendar.create(
        title=str(body.get("title", "")),
        start_at=body.get("start_at") or "",
        end_at=body.get("end_at") or None,
        description=str(body.get("description", "")),
        location=str(body.get("location", "")),
        all_day=bool(body.get("all_day", False)),
    )


@dynamic_route("DELETE", r"/api/calendar/(?P<event_id>\d+)")
def calendar_delete(app, params=None, **_: Any) -> Any:
    return {"deleted": app.calendar.delete(int(params["event_id"]))}


# --------------------------------------------------------------------- files
@route("GET", "/api/files")
def files_list(app, query=None, **_: Any) -> Any:
    query = query or {}
    path = query.get("path") or str(Path.home())
    return app.files.list_dir(path, limit=_int(query.get("limit"), 400))


@route("GET", "/api/files/search")
def files_search(app, query=None, **_: Any) -> Any:
    query = query or {}
    return app.files.search(
        query=query.get("q", ""),
        root=query.get("root") or None,
        extension=query.get("ext") or None,
        kind=query.get("kind") or None,
        limit=_int(query.get("limit"), 40),
        excluded_names=app.config.get("indexing.excluded_names", []),
    )


@route("GET", "/api/files/recent")
def files_recent(app, query=None, **_: Any) -> Any:
    return {"files": app.files.recent_files(limit=_int((query or {}).get("limit"), 20))}


@route("GET", "/api/files/insights")
def files_insights(app, query=None, **_: Any) -> Any:
    root = (query or {}).get("root") or None
    return {
        "large": app.files.large_files(root, min_mb=50, limit=15),
        "duplicates": app.files.duplicates(root, min_size_kb=64, limit=10),
        "organisation": app.files.plan_organisation(root),
    }


@route("POST", "/api/files/open")
def files_open(app, body=None, **_: Any) -> Any:
    path = str((body or {}).get("path", "")).strip()
    if not path:
        raise ApiError("no path given")
    return app.apps.open_path(path)


@route("POST", "/api/files/reveal")
def files_reveal(app, body=None, **_: Any) -> Any:
    path = Path(str((body or {}).get("path", "")))
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    target = path if path.is_dir() else path.parent
    return app.apps.open_path(str(target))


@route("POST", "/api/files/organise")
def files_organise(app, body=None, **_: Any) -> Any:
    plan = (body or {}).get("plan")
    if not isinstance(plan, list) or not plan:
        raise ApiError("no organisation plan supplied")
    return app.files.apply_organisation(plan)


@route("POST", "/api/files/backup")
def files_backup(app, body=None, **_: Any) -> Any:
    target = str((body or {}).get("path", "")).strip()
    if not target:
        raise ApiError("no path given")
    result = app.tools.call("backup_path", {"target": target})
    if not result.ok:
        raise ApiError(result.error)
    return result.data


# ---------------------------------------------------------------------- apps
@route("GET", "/api/apps")
def apps_list(app, query=None, **_: Any) -> Any:
    query = query or {}
    if query.get("running") == "1":
        return {"apps": app.apps.running()[:60], "mode": "running"}
    return {"apps": app.apps.list_apps(), "mode": "installed", "recent": app.apps.recent(limit=10)}


@route("POST", "/api/apps/launch")
def apps_launch(app, body=None, **_: Any) -> Any:
    target = str((body or {}).get("target", "")).strip()
    if not target:
        raise ApiError("no application given")
    result = app.tools.call("open_application", {"target": target})
    if not result.ok:
        raise ApiError(result.error, 403 if result.requires_approval else 400)
    return result.data


@route("POST", "/api/apps/close")
def apps_close(app, body=None, **_: Any) -> Any:
    name = str((body or {}).get("name", "")).strip()
    result = app.tools.call("close_application", {"name": name})
    if not result.ok:
        raise ApiError(result.error, 403 if result.requires_approval else 400)
    return result.data


# -------------------------------------------------------------------- system
@route("GET", "/api/system")
def system(app, query=None, **_: Any) -> Any:
    return {
        "snapshot": app.sysmon.snapshot(),
        "history": app.sysmon.history(limit=_int((query or {}).get("history"), 60)),
        "processes": app.sysmon.processes(limit=18),
        "report": app.sysmon.report(),
    }


@route("POST", "/api/system/screenshot")
def system_screenshot(app, body=None, **_: Any) -> Any:
    result = app.tools.call("take_screenshot", {})
    if not result.ok:
        raise ApiError(result.error, 500)
    return result.data


@route("POST", "/api/system/volume")
def system_volume(app, body=None, **_: Any) -> Any:
    body = body or {}
    if "percent" in body:
        result = app.tools.call("set_volume", {"percent": _int(body.get("percent"), 50)})
    else:
        result = app.tools.call("change_volume", {
            "direction": str(body.get("direction", "up")),
            "steps": _int(body.get("steps"), 2),
        })
    if not result.ok:
        raise ApiError(result.error)
    return result.data


@route("POST", "/api/system/brightness")
def system_brightness(app, body=None, **_: Any) -> Any:
    result = app.tools.call("set_brightness", {"percent": _int((body or {}).get("percent"), 50)})
    if not result.ok:
        raise ApiError(result.error)
    return result.data


@route("POST", "/api/system/power")
def system_power(app, body=None, **_: Any) -> Any:
    action = str((body or {}).get("action", "")).strip()
    tool = "power_off" if action in ("restart", "shutdown") else "power_action"
    result = app.tools.call(tool, {"action": action})
    if not result.ok:
        raise ApiError(result.error, 403 if result.requires_approval else 400)
    return result.data


@route("POST", "/api/system/panel")
def system_panel(app, body=None, **_: Any) -> Any:
    result = app.tools.call("open_system_panel", {"panel": str((body or {}).get("panel", "settings"))})
    if not result.ok:
        raise ApiError(result.error, 403 if result.requires_approval else 400)
    return result.data


# --------------------------------------------------------------------- tools
@route("GET", "/api/tools")
def tools_list(app, query=None, **_: Any) -> Any:
    query = query or {}
    return {
        "tools": app.tools.list_tools(category=query.get("category") or None),
        "summary": app.tools.describe(),
        "catalog": app.shell.catalog(),
    }


@route("POST", "/api/tools/call")
def tools_call(app, body=None, **_: Any) -> Any:
    body = body or {}
    name = str(body.get("tool", "")).strip()
    args = body.get("args") if isinstance(body.get("args"), dict) else {}
    approval_id = _int(body.get("approval_id"), 0) or None
    result = app.tools.call(name, args, approval_id=approval_id)
    payload = result.as_dict()
    if not result.ok and result.requires_approval:
        return payload
    if not result.ok:
        raise ApiError(result.error)
    return payload


# ----------------------------------------------------------------- approvals
@route("GET", "/api/approvals")
def approvals_list(app, **_: Any) -> Any:
    return {"pending": app.permissions.pending(), "policy": app.permissions.describe()}


@route("POST", "/api/approvals")
def approvals_decide(app, body=None, **_: Any) -> Any:
    body = body or {}
    approval_id = _int(body.get("approval_id"), 0)
    if not approval_id:
        raise ApiError("approval_id is required")
    approved = bool(body.get("approved"))
    changed = app.permissions.decide(approval_id, approved)
    resume = None
    if approved and body.get("task_id"):
        resume = app.orchestrator.resume(str(body["task_id"]), approval_id).as_dict()
    return {"decided": changed, "approved": approved, "resumed": resume}


@route("POST", "/api/security/emergency")
def security_emergency(app, body=None, **_: Any) -> Any:
    active = bool((body or {}).get("active", True))
    app.permissions.emergency_stop(active)
    app.audit.record("emergency_stop", detail="engaged" if active else "released", result="ok")
    return {"emergency_stop": active}


@route("POST", "/api/security/lock")
def security_lock(app, body=None, **_: Any) -> Any:
    locked = bool((body or {}).get("locked", True))
    app.permissions.lock() if locked else app.permissions.unlock()
    return {"locked": locked}


@route("GET", "/api/security")
def security(app, query=None, **_: Any) -> Any:
    query = query or {}
    limit = _int(query.get("limit"), 80)
    return {
        "policy": app.permissions.describe(),
        "audit": app.audit.recent(limit=limit),
        "stats": app.audit.stats(),
        "pending": app.permissions.pending(),
        "sensitive_paths": [str(path) for path in app.files.protected_roots()],
        "server_exposed": _CURRENT.get("exposed", False),
        "config_problems": app.config.validate(),
    }


@route("POST", "/api/security/policy")
def security_policy(app, body=None, **_: Any) -> Any:
    body = body or {}
    allowed = {"mode", "level2_confirmation", "level3_confirmation", "session_lock_minutes", "allow_shell"}
    for key, value in body.items():
        if key in allowed:
            app.config.set(f"permissions.{key}", value)
    return app.permissions.describe()


# ----------------------------------------------------------------- providers
@route("GET", "/api/providers")
def providers(app, **_: Any) -> Any:
    return {
        "providers": app.router.describe(),
        "credentials": app.credentials.status(),
        "health": app.router.health(),
        "models": {
            name: app.config.get(f"ai.providers.{name}.model", "")
            for name in ("grok", "gemini", "fish")
        },
        "usage": app.router.usage.today(),
    }


@route("POST", "/api/providers/test")
def providers_test(app, body=None, **_: Any) -> Any:
    name = str((body or {}).get("name", "")).strip()
    if not name:
        raise ApiError("provider name is required")
    return app.router.test_provider(name)


@route("POST", "/api/credentials")
def credentials_set(app, body=None, **_: Any) -> Any:
    body = body or {}
    name = str(body.get("name", "")).strip().lower()
    value = str(body.get("value", ""))
    if name not in ("grok", "gemini", "fish"):
        raise ApiError("unknown provider")
    app.credentials.set(name, value)
    app.router.rebuild()
    app.audit.record("credential_saved", target=name, result="ok")
    status = app.router.test_provider(name)
    return {"saved": True, "test": status, "credentials": app.credentials.status()}


@route("DELETE", "/api/credentials")
def credentials_delete(app, body=None, **_: Any) -> Any:
    name = str((body or {}).get("name", "")).strip().lower()
    removed = app.credentials.delete(name)
    app.router.rebuild()
    app.audit.record("credential_removed", target=name, result="ok" if removed else "missing")
    return {"removed": removed, "credentials": app.credentials.status()}


@route("POST", "/api/providers/model")
def providers_model(app, body=None, **_: Any) -> Any:
    body = body or {}
    name = str(body.get("name", "")).strip().lower()
    if name not in ("grok", "gemini", "fish"):
        raise ApiError("unknown provider")
    if body.get("model"):
        app.config.set(f"ai.providers.{name}.model", str(body["model"]).strip())
    if body.get("reference_id") is not None:
        app.config.set("ai.providers.fish.reference_id", str(body["reference_id"]).strip())
        app.config.set("voice.voice_id", str(body["reference_id"]).strip())
    if body.get("enabled") is not None:
        app.config.set(f"ai.providers.{name}.enabled", bool(body["enabled"]))
    app.router.rebuild()
    return {"model": app.config.get(f"ai.providers.{name}.model"), "health": app.router.health()}


@route("GET", "/api/providers/voices")
def providers_voices(app, **_: Any) -> Any:
    try:
        return {"voices": app.router.voices()}
    except Exception as exc:
        return {"voices": [], "error": str(exc)}


# ------------------------------------------------------------------- voice
@route("POST", "/api/voice/speak")
def voice_speak(app, body=None, handler=None, **_: Any) -> Any:
    body = body or {}
    text = str(body.get("text", "")).strip()
    if not text:
        raise ApiError("no text to speak")
    if not app.config.get("voice.enabled", False) and not body.get("force"):
        raise ApiError("voice output is disabled. Turn it on in Settings > Voice.", 409)
    try:
        audio, fmt = app.router.speak(text[:1200], voice_id=body.get("voice_id") or None)
    except Exception as exc:
        return {"spoken": False, "error": str(exc)}
    mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg", "pcm": "audio/L16"}.get(fmt, "audio/mpeg")
    if handler is not None:
        handler._send(200, audio, mime, {"X-Audio-Format": fmt})
        return b""
    return {"spoken": True, "bytes": len(audio), "format": fmt}


@route("GET", "/api/voice/status")
def voice_status(app, **_: Any) -> Any:
    fish = app.router.provider("fish")
    return {
        "enabled": bool(app.config.get("voice.enabled", False)),
        "fish_configured": bool(fish and fish.availability()[0]),
        "voice_id": app.config.get("voice.voice_id", ""),
        "speed": app.config.get("voice.speed", 1.0),
        "stt": {
            "provider": app.config.get("voice.stt_provider", "auto"),
            "note": (
                "Speech recognition uses the browser/Edge Web Speech API (Chrome or Edge only) or "
                "your own local Whisper install. Audio is only captured while you hold the mic button."
            ),
            "browser_supported": True,
            "local_whisper": _has_module("faster_whisper") or _has_module("whisper"),
        },
        "wake_word": {
            "enabled": bool(app.config.get("voice.wake_word_enabled", False)),
            "word": app.config.get("voice.wake_word", "ray"),
            "note": "Wake word listens only while the Voice section is open and the toggle is on.",
        },
    }


@route("POST", "/api/voice/transcribe")
def voice_transcribe(app, body=None, **_: Any) -> Any:
    """Transcribe audio with a local Whisper install when one exists.

    RAY does not pretend to do speech-to-text without a provider. The browser
    handles the default path; this endpoint is for people who install
    faster-whisper or openai-whisper locally.
    """
    body = body or {}
    path = str(body.get("path", "")).strip()
    if not _has_module("faster_whisper") and not _has_module("whisper"):
        raise ApiError(
            "No local speech-recognition engine is installed. RAY uses your browser's speech "
            "recognition by default (Chrome/Edge). To transcribe audio files offline, install "
            "'faster-whisper' (pip install faster-whisper).",
            501,
        )
    if not path or not Path(path).exists():
        raise ApiError("audio file not found")
    try:
        if _has_module("faster_whisper"):
            from faster_whisper import WhisperModel  # type: ignore

            model = WhisperModel("base", device="cpu", compute_type="int8")
            segments, info = model.transcribe(path, beam_size=1)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return {"text": text, "language": getattr(info, "language", ""), "engine": "faster-whisper"}
        import whisper  # type: ignore

        model = whisper.load_model("base")
        result = model.transcribe(path)
        return {"text": result.get("text", "").strip(), "language": result.get("language", ""), "engine": "whisper"}
    except Exception as exc:
        raise ApiError(f"transcription failed: {exc}", 500)


def _has_module(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


# --------------------------------------------------------------- knowledge
@route("GET", "/api/knowledge")
def knowledge(app, query=None, **_: Any) -> Any:
    return {"status": app.indexer.stats(), **_index_status(app)}


def _index_status(app) -> dict[str, Any]:
    return {"progress": app.indexer.progress, "running": app.indexer.running}


@route("POST", "/api/knowledge/index")
def knowledge_index(app, body=None, **_: Any) -> Any:
    body = body or {}
    roots = body.get("roots")
    if isinstance(roots, list) and roots:
        for root in roots:
            app.indexer.add_root(str(root))
    result = app.indexer.index()
    return result


@route("POST", "/api/knowledge/roots")
def knowledge_roots(app, body=None, **_: Any) -> Any:
    body = body or {}
    action = str(body.get("action", "add"))
    path = str(body.get("path", "")).strip()
    if action == "add":
        return app.indexer.add_root(path)
    if action == "remove":
        return {"removed": app.indexer.remove_root(path)}
    if action == "clear":
        return app.indexer.clear()
    raise ApiError("unknown action")


@route("GET", "/api/knowledge/search")
def knowledge_search(app, query=None, **_: Any) -> Any:
    query = query or {}
    return app.search.search(
        query=query.get("q", ""),
        extension=query.get("ext") or None,
        kind=query.get("kind") or None,
        limit=_int(query.get("limit"), 40),
    )


@route("POST", "/api/knowledge/document")
def knowledge_document(app, body=None, **_: Any) -> Any:
    body = body or {}
    path = str(body.get("path", "")).strip()
    if not path:
        raise ApiError("no document path given")
    from ..knowledge.extract import extract

    document = extract(path, max_chars=_int(body.get("max_chars"), 20000))
    return document


# ------------------------------------------------------------------ browser
@route("GET", "/api/browser/search")
def browser_search(app, query=None, **_: Any) -> Any:
    q = (query or {}).get("q", "").strip()
    if not q:
        raise ApiError("nothing to search for")
    return app.browser.search(q, limit=_int((query or {}).get("limit"), 8))


@route("POST", "/api/browser/fetch")
def browser_fetch(app, body=None, **_: Any) -> Any:
    url = str((body or {}).get("url", "")).strip()
    if not url:
        raise ApiError("no URL given")
    return app.browser.fetch(url).as_dict()


@route("POST", "/api/browser/research")
def browser_research(app, body=None, **_: Any) -> Any:
    body = body or {}
    question = str(body.get("question", "")).strip()
    if not question:
        raise ApiError("no research question given")
    return app.browser.research(question, router=app.router, pages=_int(body.get("pages"), 4))


@route("POST", "/api/browser/open")
def browser_open(app, body=None, **_: Any) -> Any:
    return app.browser.open_in_browser(str((body or {}).get("url", "")))


@route("GET", "/api/browser/history")
def browser_history(app, query=None, **_: Any) -> Any:
    return {
        "history": app.browser.history(limit=_int((query or {}).get("limit"), 60)),
        "bookmarks": app.browser.bookmarks(),
        "reports": app.db.query("SELECT id, question, created_at FROM research_reports ORDER BY id DESC LIMIT 20"),
    }


@route("POST", "/api/browser/bookmark")
def browser_bookmark(app, body=None, **_: Any) -> Any:
    body = body or {}
    return app.browser.add_bookmark(str(body.get("title", "")), str(body.get("url", "")), str(body.get("tags", "")))


@route("DELETE", "/api/browser/bookmark")
def browser_bookmark_delete(app, body=None, **_: Any) -> Any:
    return {"deleted": app.browser.delete_bookmark(_int((body or {}).get("id"), 0))}


@dynamic_route("GET", r"/api/browser/report/(?P<report_id>\d+)")
def browser_report(app, params=None, **_: Any) -> Any:
    row = app.db.query_one("SELECT * FROM research_reports WHERE id = ?", (int(params["report_id"]),))
    if not row:
        raise ApiError("report not found", 404)
    try:
        row["sources"] = json.loads(row.get("sources") or "[]")
    except json.JSONDecodeError:
        row["sources"] = []
    return row


# ------------------------------------------------------------------- memory
@route("GET", "/api/memory")
def memory(app, query=None, **_: Any) -> Any:
    return {
        "memories": app.memory.recall((query or {}).get("q", ""), limit=_int((query or {}).get("limit"), 100)),
        "stats": app.memory.stats(),
    }


@route("POST", "/api/memory")
def memory_add(app, body=None, **_: Any) -> Any:
    body = body or {}
    return app.memory.remember(
        title=str(body.get("title", "")),
        content=str(body.get("content", "")),
        category=str(body.get("category", "general")),
    )


@dynamic_route("DELETE", r"/api/memory/(?P<memory_id>\d+)")
def memory_delete(app, params=None, **_: Any) -> Any:
    return {"deleted": app.memory.forget(int(params["memory_id"]))}


@route("POST", "/api/memory/export")
def memory_export(app, **_: Any) -> Any:
    target = app.paths.data / "memory-export.json"
    app.memory.export_json(target)
    return {"path": str(target), "exported": app.memory.stats()["total"]}


# --------------------------------------------------------------- automation
@route("GET", "/api/automation")
def automation(app, **_: Any) -> Any:
    return {
        "workflows": app.automation.workflows.list(),
        "runs": app.automation.workflows.runs(limit=30),
        "engine": app.automation.describe(),
        "scheduler": app.scheduler.status(),
    }


@route("POST", "/api/automation")
def automation_create(app, body=None, **_: Any) -> Any:
    body = body or {}
    return app.automation.workflows.create(
        name=str(body.get("name", "")),
        trigger=body.get("trigger") or {},
        actions=body.get("actions") or [],
        conditions=body.get("conditions") or [],
        description=str(body.get("description", "")),
        enabled=bool(body.get("enabled", True)),
    )


@dynamic_route("POST", r"/api/automation/(?P<workflow_id>\d+)/run")
def automation_run(app, params=None, **_: Any) -> Any:
    return app.automation.run(int(params["workflow_id"]), trigger="manual")


@dynamic_route("POST", r"/api/automation/(?P<workflow_id>\d+)/toggle")
def automation_toggle(app, body=None, params=None, **_: Any) -> Any:
    enabled = bool((body or {}).get("enabled", True))
    return app.automation.workflows.set_enabled(int(params["workflow_id"]), enabled)


@dynamic_route("DELETE", r"/api/automation/(?P<workflow_id>\d+)")
def automation_delete(app, params=None, **_: Any) -> Any:
    return {"deleted": app.automation.workflows.delete(int(params["workflow_id"]))}


# ------------------------------------------------------------------ media
@route("GET", "/api/media")
def media(app, query=None, **_: Any) -> Any:
    query = query or {}
    return {
        "library": app.media.search(query.get("q", ""), limit=_int(query.get("limit"), 60)),
        "playlists": app.media.playlists(),
        "roots": [str(path) for path in app.media.roots()],
    }


@route("POST", "/api/media/play")
def media_play(app, body=None, **_: Any) -> Any:
    query = str((body or {}).get("query", "")).strip()
    result = app.tools.call("play_media", {"query": query})
    if not result.ok:
        raise ApiError(result.error, 403 if result.requires_approval else 404)
    return result.data


# ---------------------------------------------------------------- analytics
@route("GET", "/api/analytics")
def analytics(app, **_: Any) -> Any:
    return {
        "usage": app.router.usage.today(),
        "usage_history": app.router.usage.history(days=14),
        "errors": app.router.usage.recent_errors(limit=8),
        "tasks": app.tasks.stats(),
        "focus": app.focus.focus_stats(days=7),
        "notes": app.notes.stats(),
        "reminders": app.reminders.stats(),
        "memories": app.memory.stats(),
        "audit": app.audit.stats(),
        "index": app.indexer.stats(),
        "cache": app.router.cache.stats(),
        "agents": app.db.query(
            "SELECT agent, COUNT(*) AS runs, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed "
            "FROM agent_runs GROUP BY agent ORDER BY runs DESC"
        ),
        "database": {"size_bytes": app.db.size_bytes(), "counts": app.db.table_counts()},
    }


# ----------------------------------------------------------------- settings
@route("GET", "/api/settings")
def settings_get(app, **_: Any) -> Any:
    config = app.config.all()
    for name in ("grok", "gemini", "fish"):
        provider = config.get("ai", {}).get("providers", {}).get(name, {})
        provider.pop("api_key", None)
    return {
        "config": config,
        "problems": app.config.validate(),
        "paths": app.paths.describe(),
        "version": __version__,
        "capabilities": {
            "screenshot": True,
            "input_automation": app.input.available(),
            "shell": app.shell.enabled,
            "clipboard": True,
        },
    }


@route("POST", "/api/settings")
def settings_set(app, body=None, **_: Any) -> Any:
    body = body or {}
    values = body.get("values") if isinstance(body.get("values"), dict) else body
    from ..security.secrets import looks_secret

    if looks_secret(" ".join(str(value) for value in values.values() if isinstance(value, str))):
        raise ApiError(
            "that looks like a credential, so it was not saved to the settings file. "
            "Use Settings > AI Providers (the value is encrypted) instead."
        )
    changed = 0
    for key, value in values.items():
        if key in ("action", "values"):
            continue
        if not isinstance(key, str) or "." not in key:
            continue
        app.config.set(key, value)
        changed += 1
    if changed:
        app.audit.record("settings_changed", detail=", ".join(list(values)[:10]))
    return {"changed": changed, "problems": app.config.validate(), "config": app.config.all()}


@route("POST", "/api/backup")
def backup(app, body=None, **_: Any) -> Any:
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = app.paths.backups / f"ray-{stamp}.db"
    path = app.db.backup(destination)
    return {"path": str(path), "size_bytes": path.stat().st_size, "integrity": app.db.integrity_check()}


@route("GET", "/api/backups")
def backups(app, **_: Any) -> Any:
    files = []
    for path in sorted(app.paths.backups.glob("*.db"))[-20:]:
        stat = path.stat()
        files.append({"name": path.name, "path": str(path), "size_bytes": stat.st_size, "modified": stat.st_mtime})
    return {"backups": files, "directory": str(app.paths.backups)}


@route("POST", "/api/shutdown")
def shutdown(app, **_: Any) -> Any:
    threading.Thread(target=app.stop, name="ray-shutdown", daemon=True).start()
    return {"stopping": True}


# --------------------------------------------------------------- downloads
@route("GET", "/api/downloads")
def downloads(app, **_: Any) -> Any:
    folder = app.files.downloads_dir
    if not folder.exists():
        return {"path": str(folder), "entries": [], "organisation": {"plan": [], "unmatched": []}}
    listing = app.files.list_dir(folder, limit=400)
    listing["organisation"] = app.files.plan_organisation(folder)
    return listing


# ------------------------------------------------------------------ current
_CURRENT: dict[str, Any] = {"host": "127.0.0.1", "port": 8765, "exposed": False}


def create_server(app, host: str | None = None, port: int | None = None) -> RayServer:
    host = host or str(app.config.get("ui.host", "127.0.0.1"))
    port = int(port or app.config.get("ui.port", 8765))
    server = RayServer(app, host, port)
    _CURRENT.update({"host": host, "port": port, "exposed": server.exposed})
    return server


def find_free_port(host: str = "127.0.0.1", start: int = 8765) -> int:
    for candidate in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, candidate))
                return candidate
            except OSError:
                continue
    return 0
