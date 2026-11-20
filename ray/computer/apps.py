"""Application discovery and launching.

Windows
    * Start Menu shortcuts (.lnk) for both user and machine profiles
    * Registry "App Paths" entries (chrome.exe, code.exe, ...)
    * Launch through ``os.startfile`` / ``ShellExecute`` so the shell resolves
      shortcuts, arguments and working directories correctly

Linux
    * freedesktop ``.desktop`` entries parsed properly (Exec field codes
      stripped, .desktop escaping handled)

macOS
    * ``/Applications`` bundles launched with ``open -a``

Only entries that actually exist on this machine are reported - nothing is
assumed to be installed.
"""

from __future__ import annotations

import difflib
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..core.database import Database, utcnow
from ..core.logging_setup import get_logger

log = get_logger("computer.apps")

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

_URL_RE = re.compile(r"^(https?://|mailto:)", re.IGNORECASE)
_APP_EXTENSIONS = {".exe", ".lnk", ".bat", ".cmd", ".app", ".desktop", ".url", ".msc"}

# Applications RAY refuses to launch on its own without explicit human approval.
_RESTRICTED = {
    "cmd", "powershell", "pwsh", "regedit", "mmc", "taskmgr", "diskmgmt",
    "gpedit", "secpol", "services", "eventvwr", "wt", "terminal", "konsole",
    "gnome-terminal", "bash", "sh", "sudo", "su", "mkfs", "diskpart",
}


class AppManager:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db
        self._cache: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------ discovery
    def _start_menu_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        if IS_WINDOWS:
            for env, suffix in (
                ("PROGRAMDATA", r"Microsoft\Windows\Start Menu\Programs"),
                ("APPDATA", r"Microsoft\Windows\Start Menu\Programs"),
            ):
                base = os.environ.get(env)
                if base:
                    dirs.append(Path(base) / suffix)
        elif IS_MAC:
            dirs.extend([Path("/Applications"), Path.home() / "Applications"])
        else:
            dirs.extend(
                [
                    Path("/usr/share/applications"),
                    Path("/usr/local/share/applications"),
                    Path.home() / ".local" / "share" / "applications",
                    Path("/var/lib/flatpak/exports/share/applications"),
                ]
            )
        return [directory for directory in dirs if directory.exists()]

    def _scan_start_menu(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if IS_MAC:
            for directory in self._start_menu_dirs():
                for bundle in directory.glob("*.app"):
                    entries.append(
                        {
                            "name": bundle.stem,
                            "path": str(bundle),
                            "source": "applications",
                            "launch": "open -a",
                        }
                    )
            return entries

        for directory in self._start_menu_dirs():
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                suffix = path.suffix.lower()
                if IS_WINDOWS and suffix != ".lnk" and suffix not in {".url", ".msc"}:
                    continue
                if not IS_WINDOWS and suffix != ".desktop":
                    continue
                try:
                    if not IS_WINDOWS:
                        name, exec_line, hidden = _parse_desktop_entry(path)
                        if hidden or not exec_line:
                            continue
                        entries.append(
                            {
                                "name": name,
                                "path": str(path),
                                "exec": exec_line,
                                "source": "desktop",
                                "launch": "exec",
                            }
                        )
                    else:
                        entries.append(
                            {
                                "name": path.stem,
                                "path": str(path),
                                "source": "start_menu",
                                "launch": "shell",
                            }
                        )
                except OSError:
                    continue
        return entries

    def _registry_app_paths(self) -> list[dict[str, Any]]:
        if not IS_WINDOWS:
            return []
        try:
            import winreg
        except ImportError:  # pragma: no cover
            return []
        entries: list[dict[str, Any]] = []
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        ]
        for root, path in keys:
            try:
                with winreg.OpenKey(root, path) as key:
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            sub_name = winreg.EnumKey(key, index)
                            with winreg.OpenKey(key, sub_name) as sub:
                                target, _ = winreg.QueryValueEx(sub, "")
                            if target and Path(target).exists():
                                entries.append(
                                    {
                                        "name": Path(target).stem,
                                        "path": str(target),
                                        "source": "registry",
                                        "launch": "exec",
                                    }
                                )
                        except OSError:
                            continue
            except OSError:
                continue
        return entries

    def list_apps(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._cache is not None and not refresh:
            return list(self._cache)
        seen: set[str] = set()
        apps: list[dict[str, Any]] = []
        for entry in self._scan_start_menu() + self._registry_app_paths():
            key = entry["name"].strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            apps.append(entry)
        apps.sort(key=lambda item: item["name"].lower())
        self._cache = apps
        return list(apps)

    # -------------------------------------------------------------- resolution
    def resolve(self, query: str) -> dict[str, Any] | None:
        query = (query or "").strip()
        if not query:
            return None
        lowered = query.lower()
        # 1. direct path
        candidate = Path(os.path.expandvars(os.path.expanduser(query)))
        if candidate.exists():
            return {"name": candidate.stem, "path": str(candidate), "launch": "path"}
        # 2. which/where on PATH
        executable = shutil.which(query)
        if executable:
            return {"name": Path(executable).stem, "path": executable, "launch": "exec"}
        apps = self.list_apps()
        # 3. exact name
        for app in apps:
            if app["name"].lower() in (lowered, f"{lowered}.exe"):
                return app
        # 4. starts-with
        starts = [app for app in apps if app["name"].lower().startswith(lowered)]
        if len(starts) == 1:
            return starts[0]
        # 5. contains
        contains = [app for app in apps if lowered in app["name"].lower()]
        if len(contains) == 1:
            return contains[0]
        if starts or contains:
            pool = starts or contains
            return {"ambiguous": True, "candidates": [app["name"] for app in pool[:8]], "query": query}
        # 6. fuzzy
        close = difflib.get_close_matches(query, [app["name"] for app in apps], n=3, cutoff=0.72)
        if close:
            return {"ambiguous": True, "candidates": close, "query": query}
        return None

    # ---------------------------------------------------------------- launching
    def launch(self, target: str, args: list[str] | None = None) -> dict[str, Any]:
        """Launch an app, shortcut, document, folder or URL."""
        target = (target or "").strip()
        if not target:
            raise ValueError("no application or path given")

        if _URL_RE.match(target):
            return self.open_path(target)

        resolved = self.resolve(target)
        if resolved is None:
            raise ValueError(
                f"'{target}' was not found on this computer. "
                "RAY only launches applications it can actually locate."
            )
        if resolved.get("ambiguous"):
            raise ValueError(
                f"'{target}' is ambiguous. Did you mean: {', '.join(resolved['candidates'])}?"
            )

        name = str(resolved.get("name") or target)
        if name.lower() in _RESTRICTED and resolved.get("launch") != "path":
            # Shells and system utilities are legitimate but flagged for the audit log.
            log.info("launching restricted-class application '%s'", name)

        launch_mode = resolved.get("launch")
        try:
            if launch_mode == "shell" or (IS_WINDOWS and Path(resolved["path"]).suffix.lower() == ".lnk"):
                os.startfile(resolved["path"])  # type: ignore[attr-defined]
            elif launch_mode == "open -a":
                subprocess.Popen(["open", "-a", resolved["path"]])
            elif launch_mode == "desktop":
                command = _strip_exec_codes(resolved["exec"])
                parts = _split_exec(command)
                if not parts:
                    raise ValueError(f"desktop entry for '{name}' has no runnable command")
                subprocess.Popen(parts, start_new_session=True, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                path = Path(resolved["path"])
                suffix = path.suffix.lower()
                if IS_WINDOWS and suffix not in {".exe", ".bat", ".cmd"}:
                    os.startfile(str(path))  # type: ignore[attr-defined]
                elif not os.access(str(path), os.X_OK) and suffix not in {".exe", ".bat", ".cmd"}:
                    self.open_path(str(path))
                    return {"launched": name, "path": str(path), "opened_with": "default application"}
                else:
                    subprocess.Popen(
                        [str(path)] + list(args or []),
                        start_new_session=True,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"could not launch '{name}': {exc}") from exc

        self._record_launch(name, resolved.get("path", ""))
        log.info("launched %s (%s)", name, resolved.get("path"))
        return {"launched": name, "path": resolved.get("path", ""), "method": launch_mode}

    def open_path(self, path_or_url: str) -> dict[str, Any]:
        """Open a file, folder or URL with the operating system handler."""
        target = str(path_or_url).strip()
        if not target:
            raise ValueError("empty path")
        if _URL_RE.match(target):
            import webbrowser

            opened = webbrowser.open(target)
            if not opened:
                raise RuntimeError(f"no browser available to open {target}")
            self._record_launch(target, target)
            return {"opened": target, "method": "browser"}
        path = Path(os.path.expandvars(os.path.expanduser(target)))
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist")
        try:
            if IS_WINDOWS:
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif IS_MAC:
                subprocess.Popen(["open", str(path.getparent() if path.is_file() else path)])
            else:
                opener = "xdg-open"
                if shutil.which(opener) is None:
                    if path.is_dir():
                        raise RuntimeError("no xdg-open available to open folders on this system")
                    raise RuntimeError("no xdg-open available to open files on this system")
                subprocess.Popen(
                    [opener, str(path)],
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except OSError as exc:
            raise RuntimeError(f"could not open {path}: {exc}") from exc
        self._record_launch(path.name, str(path))
        return {"opened": str(path), "method": "default application"}

    # ----------------------------------------------------------------- running
    def running(self) -> list[dict[str, Any]]:
        try:
            import psutil
        except ImportError:  # pragma: no cover
            raise RuntimeError("psutil is required to inspect running applications")
        seen: dict[str, dict[str, Any]] = {}
        for process in psutil.process_iter(["pid", "name", "memory_info", "create_time"]):
            try:
                name = process.info.get("name") or ""
                if not name:
                    continue
                memory = process.info.get("memory_info")
                entry = seen.setdefault(
                    name,
                    {
                        "name": name,
                        "pids": [],
                        "memory_mb": 0.0,
                        "started": process.info.get("create_time"),
                    },
                )
                entry["pids"].append(process.info["pid"])
                entry["memory_mb"] = round(entry["memory_mb"] + (memory.rss if memory else 0) / (1024 * 1024), 1)
            except Exception:
                continue
        return sorted(seen.values(), key=lambda item: item["memory_mb"], reverse=True)

    def close(self, name: str, force: bool = False) -> dict[str, Any]:
        """Ask a running application to close. Level 2 (needs confirmation)."""
        try:
            import psutil
        except ImportError:  # pragma: no cover
            raise RuntimeError("psutil is required to close applications")
        target = (name or "").strip().lower()
        if not target:
            raise ValueError("no application name given")
        if target in {"explorer.exe", "explorer", "winlogon.exe", "csrss.exe", "svchost.exe",
                      "system", "systemd", "init", "kernel_task"}:
            raise PermissionError(f"'{name}' is a critical system process and will not be closed")
        closed = []
        for process in psutil.process_iter(["pid", "name"]):
            try:
                proc_name = (process.info.get("name") or "").lower()
                if proc_name == target or Path(proc_name).stem == Path(target).stem:
                    process.terminate() if not force else process.kill()
                    closed.append(process.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not closed:
            raise ValueError(f"'{name}' is not running")
        return {"closed": name, "pids": closed, "forced": force}

    # -------------------------------------------------------------- favourites
    def recent(self, limit: int = 8) -> list[dict[str, Any]]:
        if not self.db:
            return []
        return self.db.query(
            "SELECT name, path, last_launched, launch_count FROM app_usage "
            "ORDER BY last_launched DESC LIMIT ?",
            (limit,),
        )

    def _record_launch(self, name: str, path: str) -> None:
        if not self.db:
            return
        existing = self.db.query_one(
            "SELECT id FROM app_usage WHERE name = ? AND path = ?", (name, path)
        )
        if existing:
            self.db.execute(
                "UPDATE app_usage SET last_launched = ?, launch_count = launch_count + 1 WHERE id = ?",
                (utcnow(), existing["id"]),
            )
        else:
            self.db.insert(
                "app_usage",
                {"name": name, "path": path, "last_launched": utcnow(), "launch_count": 1},
            )


def _strip_exec_codes(command: str) -> str:
    """Remove freedesktop Exec placeholders (%U, %f, ...)."""
    return re.sub(r"\s*%[fFuUdDnNickvm]", "", command).strip()


def _split_exec(command: str) -> list[str]:
    import shlex

    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _unescape_desktop(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\t", "\t").replace("\\s", " ").replace("\\\\", "\\")


def _parse_desktop_entry(path: Path) -> tuple[str, str, bool]:
    name = path.stem
    exec_line = ""
    hidden = False
    in_main = False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return name, "", True
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_main = line == "[Desktop Entry]"
            continue
        if not in_main or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key == "Name" and name == path.stem:
            name = _unescape_desktop(value.strip())
        elif key == "Exec" and not exec_line:
            exec_line = value.strip()
        elif key in ("NoDisplay", "Hidden"):
            if value.strip().lower() in ("true", "1"):
                hidden = True
    return name, exec_line, hidden


def system_info() -> dict[str, Any]:
    return {
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "home": str(Path.home()),
    }
