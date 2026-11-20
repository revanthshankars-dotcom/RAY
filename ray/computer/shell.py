"""Validated command execution.

RAY never hands AI-generated text to a shell.  Commands must:

1. name an executable from a fixed allow-list,
2. be passed as an argument list (never ``shell=True``),
3. contain no shell metacharacters,
4. run inside an existing working directory, with a hard timeout,
5. be explicitly enabled by the user (Settings > Permissions > allow shell) and
   approved as a Level 3 action.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..core.logging_setup import get_logger

log = get_logger("computer.shell")

MAX_OUTPUT_CHARS = 20000
MAX_TIMEOUT = 180

ALLOWED_COMMANDS: dict[str, str] = {
    # cross platform
    "git": "version control", "python": "python interpreter", "python3": "python interpreter",
    "pip": "python packages", "node": "node runtime", "npm": "node packages",
    "ping": "network reachability",
    # windows
    "ipconfig": "network configuration", "whoami": "current user", "systeminfo": "system summary",
    "tasklist": "process list", "dir": "directory listing", "where": "locate executable",
    "getmac": "mac address", "ver": "windows version",
    # posix
    "ls": "directory listing", "pwd": "working directory", "df": "disk usage",
    "du": "directory size", "uname": "system information", "hostname": "host name",
    "free": "memory usage", "uptime": "uptime", "nproc": "cpu count", "echo": "print text",
    "which": "locate executable", "ps": "process list",
}

FORBIDDEN_PATTERNS = [
    re.compile(r"[;&|><`]"),
    re.compile(r"\$\("),
    re.compile(r"\brm\b\s+-rf\b"),
    re.compile(r"\bformat\b", re.IGNORECASE),
    re.compile(r"\bdel\b\s+/[sfq]", re.IGNORECASE),
    re.compile(r"\b(net\s+user|reg\s+add|sc\s+create)\b", re.IGNORECASE),
]


class ShellRunner:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def validate(self, command: list[str] | str, cwd: str | Path | None = None) -> list[str]:
        if not self.enabled:
            raise PermissionError(
                "Command execution is disabled. Enable it in Settings > Permissions if you "
                "want RAY to run allow-listed commands."
            )
        if isinstance(command, str):
            raise ValueError("commands must be passed as an argument list, not a string")
        if not command:
            raise ValueError("empty command")
        executable = str(command[0]).strip()
        if any(pattern.search(" ".join(str(part) for part in command)) for pattern in FORBIDDEN_PATTERNS):
            raise ValueError("command contains shell metacharacters and was rejected")
        base = Path(executable).name.lower()
        if base.endswith(".exe"):
            base = base[:-4]
        if base not in ALLOWED_COMMANDS:
            raise PermissionError(
                f"'{base}' is not on RAY's allow-list of safe commands. "
                f"Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
            )
        if shutil.which(executable) is None and not Path(executable).exists():
            raise FileNotFoundError(f"'{executable}' is not installed")
        if cwd is not None:
            working = Path(cwd).expanduser()
            if not working.is_dir():
                raise NotADirectoryError(f"{working} is not a directory")
        return [str(part) for part in command]

    def run(
        self,
        command: list[str],
        cwd: str | Path | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        argv = self.validate(command, cwd)
        timeout = max(1, min(int(timeout), MAX_TIMEOUT))
        try:
            completed = subprocess.run(
                argv,
                cwd=str(Path(cwd).expanduser()) if cwd else None,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                env=self._safe_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"command timed out after {timeout}s: {' '.join(argv)}") from exc
        except OSError as exc:
            raise RuntimeError(f"could not run {' '.join(argv)}: {exc}") from exc

        stdout = (completed.stdout or "")[:MAX_OUTPUT_CHARS]
        stderr = (completed.stderr or "")[:MAX_OUTPUT_CHARS]
        return {
            "command": argv,
            "cwd": str(cwd) if cwd else os.getcwd(),
            "returncode": completed.returncode,
            "ok": completed.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": len(completed.stdout or "") > MAX_OUTPUT_CHARS,
        }

    @staticmethod
    def _safe_env() -> dict[str, str]:
        """Strip anything that smells like a credential before spawning."""
        env = dict(os.environ)
        for key in list(env):
            upper = key.upper()
            if any(marker in upper for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")):
                env.pop(key, None)
        return env

    @staticmethod
    def catalog() -> list[dict[str, str]]:
        return [
            {"command": name, "description": description}
            for name, description in sorted(ALLOWED_COMMANDS.items())
        ]
