"""Local media library.

Reads the audio and video files that are actually on this machine, reads their
tags when ``mutagen`` is available, and plays them through the OS default player.
RAY does not stream, rip or bypass anything: if a file is not on disk, it is not
in the library.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core.logging_setup import get_logger
from ..computer.files import human_size

log = get_logger("media.service")

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".opus", ".aac", ".wma"}
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}


class MediaService:
    def __init__(self, app_manager, config=None, extra_roots: list[Path] | None = None) -> None:
        self.app_manager = app_manager
        self.config = config if config is not None else self._load_config()
        self.extra_roots = extra_roots or []

    @staticmethod
    def _load_config():
        class _Cfg:
            def get(self, key, default=None):
                return default

        return _Cfg()

    def roots(self) -> list[Path]:
        candidates: list[Path] = []
        configured = ""
        try:
            configured = str(self.config.get("paths.music", "") or "")
        except Exception:
            configured = ""
        if configured:
            candidates.append(Path(os.path.expanduser(configured)))
        candidates.extend(
            [
                Path.home() / "Music",
                Path.home() / "Videos",
                Path.home() / "Downloads",
            ]
        )
        candidates.extend(self.extra_roots)
        return [path for path in candidates if path.exists() and path.is_dir()]

    # ------------------------------------------------------------------ library
    def library(self, limit: int = 500, include_video: bool = True, refresh: bool = False) -> list[dict[str, Any]]:
        extensions = AUDIO_EXT | (VIDEO_EXT if include_video else set())
        tracks: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root in self.roots():
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [name for name in dirnames if not name.startswith(".")]
                for filename in filenames:
                    path = Path(dirpath) / filename
                    suffix = path.suffix.lower()
                    if suffix not in extensions or str(path) in seen:
                        continue
                    seen.add(str(path))
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    tracks.append(
                        {
                            "path": str(path),
                            "name": filename,
                            "title": path.stem,
                            "kind": "video" if suffix in VIDEO_EXT else "audio",
                            "ext": suffix,
                            "size": stat.st_size,
                            "size_human": human_size(stat.st_size),
                            "modified": stat.st_mtime,
                            "folder": dirpath,
                        }
                    )
                    if len(tracks) >= limit:
                        return tracks
        return tracks

    def search(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        query = (query or "").strip().lower()
        tracks = self.library(limit=4000)
        if not query:
            return tracks[:limit]
        terms = [term for term in query.replace("-", " ").split() if term]
        scored: list[tuple[int, dict[str, Any]]] = []
        for track in tracks:
            haystack = f"{track['name']} {track['folder']}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, track))
        scored.sort(key=lambda item: (-item[0], item[1]["name"].lower()))
        return [track for _, track in scored[:limit]]

    def play(self, query: str) -> dict[str, Any]:
        if not (query or "").strip():
            raise ValueError("tell me what to play")
        playlist = self.find_playlist(query)
        if playlist:
            return self.play_playlist(playlist)
        matches = self.search(query, limit=5)
        if not matches:
            raise FileNotFoundError(
                f"I could not find any local audio matching '{query}'. RAY only plays files that "
                "are already on this computer - add them to your Music folder first."
            )
        track = matches[0]
        self.app_manager.open_path(track["path"])
        return {
            "played": True,
            "title": track["title"],
            "path": track["path"],
            "alternatives": [item["title"] for item in matches[1:4]],
        }

    # ---------------------------------------------------------------- playlists
    def find_playlist(self, query: str) -> Path | None:
        query = (query or "").lower().strip()
        for root in self.roots():
            for extension in (".m3u", ".m3u8", ".pls"):
                for playlist in root.rglob(f"*{extension}"):
                    if query in playlist.stem.lower():
                        return playlist
        return None

    def playlists(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for root in self.roots():
            for extension in (".m3u", ".m3u8", ".pls"):
                for playlist in root.rglob(f"*{extension}"):
                    try:
                        entries = self.read_playlist(playlist)
                    except OSError:
                        continue
                    found.append(
                        {
                            "name": playlist.stem,
                            "path": str(playlist),
                            "tracks": len(entries),
                        }
                    )
        return found

    @staticmethod
    def read_playlist(path: Path) -> list[str]:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        entries: list[str] = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if path.suffix.lower() == ".pls":
                if line.lower().startswith("file") and "=" in line:
                    entries.append(line.split("=", 1)[1].strip())
            else:
                entries.append(line)
        return entries

    def play_playlist(self, playlist: Path) -> dict[str, Any]:
        entries = self.read_playlist(playlist)
        if not entries:
            raise ValueError(f"the playlist {playlist.name} is empty")
        base = playlist.parent
        resolved: list[str] = []
        for entry in entries:
            candidate = Path(entry)
            if not candidate.is_absolute():
                candidate = base / candidate
            if candidate.exists():
                resolved.append(str(candidate))
        if not resolved:
            raise FileNotFoundError(
                f"none of the {len(entries)} tracks in '{playlist.name}' exist on disk any more"
            )
        self.app_manager.open_path(resolved[0])
        return {
            "played": True,
            "playlist": playlist.stem,
            "first_track": Path(resolved[0]).name,
            "tracks_available": len(resolved),
            "tracks_total": len(entries),
            "note": (
                "RAY hands the first track to your default player; queueing the rest depends on "
                "that player. Use its own playlist support for full sequential playback."
            ),
        }
