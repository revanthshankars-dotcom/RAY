"""Filesystem locations used by RAY.

Everything RAY writes lives under a single application home directory so the
user can inspect, back up, or delete it in one place:

    Windows : %LOCALAPPDATA%\\RAY
    Linux   : ~/.local/share/ray
    macOS   : ~/Library/Application Support/RAY

Override with the RAY_HOME environment variable (used by the test suite).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _default_home() -> Path:
    override = os.environ.get("RAY_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "RAY"
        return Path.home() / "AppData" / "Local" / "RAY"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "RAY"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ray"


class Paths:
    """Resolved application directories, created on first access."""

    def __init__(self, home: Path | str | None = None) -> None:
        self.home = Path(home).expanduser().resolve() if home else _default_home()

    def _ensure(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def data(self) -> Path:
        return self._ensure(self.home / "data")

    @property
    def logs(self) -> Path:
        return self._ensure(self.home / "logs")

    @property
    def cache(self) -> Path:
        return self._ensure(self.home / "cache")

    @property
    def backups(self) -> Path:
        return self._ensure(self.home / "backups")

    @property
    def quarantine(self) -> Path:
        """Where RAY moves files instead of hard-deleting them."""
        return self._ensure(self.home / "quarantine")

    @property
    def screenshots(self) -> Path:
        return self._ensure(self.home / "screenshots")

    @property
    def db_path(self) -> Path:
        self._ensure(self.home)
        return self.home / "ray.db"

    @property
    def config_path(self) -> Path:
        self._ensure(self.home)
        return self.home / "config.json"

    @property
    def secret_key_path(self) -> Path:
        self._ensure(self.home)
        return self.home / "secret.key"

    @property
    def packages_dir(self) -> Path:
        """Repository-relative assets (UI files ship with the code)."""
        return Path(__file__).resolve().parent.parent

    def describe(self) -> dict:
        return {
            "home": str(self.home),
            "data": str(self.data),
            "logs": str(self.logs),
            "cache": str(self.cache),
            "database": str(self.db_path),
            "platform": sys.platform,
            "python": sys.version.split()[0],
        }


_default_paths: Paths | None = None


def paths() -> Paths:
    global _default_paths
    if _default_paths is None:
        _default_paths = Paths()
    return _default_paths


def reset_paths(home: Path | str | None = None) -> Paths:
    """Point RAY at a different home (tests / --home flag)."""
    global _default_paths
    _default_paths = Paths(home)
    return _default_paths
