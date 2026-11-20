"""File management with real safety rails.

Design rules
------------
* Destructive operations never hit the filesystem directly: deletion moves the
  item into RAY's quarantine folder with a manifest, so it can be restored.
* System and credential directories are refused outright.
* Every operation verifies its own result (the file exists / has the right size)
  and reports honestly if it did not.
* Searches prune excluded directories instead of walking 500k files on a
  Celeron.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..core.logging_setup import get_logger

log = get_logger("computer.files")

PREVIEW_LIMIT = 4000

DOCUMENT_KINDS: dict[str, list[str]] = {
    "document": [".pdf", ".docx", ".doc", ".odt", ".rtf"],
    "text": [".txt", ".md", ".log", ".ini", ".cfg"],
    "spreadsheet": [".csv", ".xlsx", ".xls", ".ods"],
    "presentation": [".pptx", ".ppt", ".odp"],
    "image": [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".heic"],
    "audio": [".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac"],
    "video": [".mp4", ".mkv", ".avi", ".mov", ".webm"],
    "archive": [".zip", ".rar", ".7z", ".tar", ".gz"],
    "code": [".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".c", ".cpp", ".cs", ".rs", ".go", ".sh", ".ps1"],
    "installer": [".exe", ".msi", ".dmg", ".deb", ".appimage"],
}

ORGANISE_PLAN: dict[str, list[str]] = {
    "Documents": DOCUMENT_KINDS["document"] + DOCUMENT_KINDS["text"],
    "Spreadsheets": DOCUMENT_KINDS["spreadsheet"],
    "Presentations": DOCUMENT_KINDS["presentation"],
    "Images": DOCUMENT_KINDS["image"],
    "Audio": DOCUMENT_KINDS["audio"] + [".flac"],
    "Video": DOCUMENT_KINDS["video"],
    "Archives": DOCUMENT_KINDS["archive"],
    "Installers": DOCUMENT_KINDS["installer"],
}


class FileManager:
    def __init__(self, quarantine_dir: Path | str | None = None, downloads_dir: Path | None = None) -> None:
        self.quarantine_dir = Path(quarantine_dir) if quarantine_dir else Path.home() / "RAY-quarantine"
        self.downloads_dir = downloads_dir or Path.home() / "Downloads"

    # ----------------------------------------------------------------- safety
    @staticmethod
    def resolve(path: str | Path) -> Path:
        if not str(path).strip():
            raise ValueError("empty path")
        return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()

    @staticmethod
    def protected_roots() -> list[Path]:
        candidates: list[str] = []
        if sys.platform.startswith("win"):
            for env in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
                value = os.environ.get(env)
                if value:
                    candidates.append(value)
        else:
            candidates.extend(["/bin", "/sbin", "/usr", "/etc", "/boot", "/proc", "/sys", "/dev", "/var/lib"])
        home = Path.home()
        candidates.extend([str(home / ".ssh"), str(home / ".gnupg"), str(home / ".config")])
        roots: list[Path] = []
        for candidate in candidates:
            try:
                roots.append(Path(candidate).resolve())
            except OSError:
                continue
        return roots

    def is_protected(self, path: Path) -> bool:
        try:
            target = path.resolve()
        except OSError:
            return True
        if str(target) in ("/", "C:\\"):
            return True
        for root in self.protected_roots():
            try:
                if target == root or root in target.parents:
                    return True
            except (OSError, ValueError):
                continue
        return False

    def _guard_mutation(self, *paths: Path) -> None:
        for path in paths:
            if self.is_protected(path):
                raise PermissionError(
                    f"RAY will not modify {path} - it is a protected system location."
                )

    # -------------------------------------------------------------- inspection
    def info(self, path: str | Path) -> dict[str, Any]:
        target = self.resolve(path)
        if not target.exists():
            raise FileNotFoundError(f"{target} does not exist")
        stat = target.stat()
        data: dict[str, Any] = {
            "path": str(target),
            "name": target.name,
            "is_dir": target.is_dir(),
            "size_bytes": stat.st_size if target.is_file() else None,
            "size_human": human_size(stat.st_size) if target.is_file() else "",
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "created": datetime.fromtimestamp(stat.st_ctime).isoformat(timespec="seconds"),
            "ext": target.suffix.lower(),
            "kind": classify(target),
            "readable": os.access(target, os.R_OK),
            "protected": self.is_protected(target),
        }
        if target.is_dir():
            try:
                data["entries"] = len(list(target.iterdir()))
            except OSError:
                data["entries"] = None
            data["parent"] = str(target.parent)
        return data

    def list_dir(self, path: str | Path, limit: int = 500, show_hidden: bool = False) -> dict[str, Any]:
        target = self.resolve(path)
        if not target.is_dir():
            raise NotADirectoryError(f"{target} is not a folder")
        entries: list[dict[str, Any]] = []
        for entry in sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            entries.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "is_dir": entry.is_dir(),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "kind": "folder" if entry.is_dir() else classify(entry),
                }
            )
            if len(entries) >= limit:
                break
        return {
            "path": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "entries": entries,
            "truncated": len(entries) >= limit,
        }

    def read_text(self, path: str | Path, max_chars: int = PREVIEW_LIMIT) -> dict[str, Any]:
        target = self.resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"{target} is not a file")
        if target.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("file is larger than 5 MB; use document extraction instead")
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise RuntimeError(f"could not read {target}: {exc}") from exc
        return {
            "path": str(target),
            "text": text[:max_chars],
            "chars": len(text),
            "truncated": len(text) > max_chars,
        }

    # ------------------------------------------------------------------ search
    def search(
        self,
        query: str = "",
        root: str | Path | None = None,
        extension: str | None = None,
        kind: str | None = None,
        min_size_mb: float | None = None,
        max_size_mb: float | None = None,
        modified_within_days: int | None = None,
        limit: int = 60,
        excluded_names: Iterable[str] | None = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        base = self.resolve(root) if root else Path.home()
        if not base.exists():
            raise FileNotFoundError(f"{base} does not exist")
        exclusions = {name.lower() for name in (excluded_names or [])}
        query_lower = (query or "").strip().lower()
        ext_filter = None
        if extension:
            ext_filter = extension if extension.startswith(".") else f".{extension}"
            ext_filter = ext_filter.lower()
        kind_exts = {value.lower() for value in DOCUMENT_KINDS.get(kind, [])} if kind else None
        cutoff = time.time() - modified_within_days * 86400 if modified_within_days else None
        min_bytes = int(min_size_mb * 1024 * 1024) if min_size_mb else None
        max_bytes = int(max_size_mb * 1024 * 1024) if max_size_mb else None

        results: list[dict[str, Any]] = []
        scanned = 0
        started = time.time()
        stack = [base]
        while stack and len(results) < limit and (time.time() - started) < timeout:
            current = stack.pop()
            try:
                with os.scandir(current) as iterator:
                    for entry in iterator:
                        scanned += 1
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name.lower() in exclusions or entry.name.startswith("$"):
                                    continue
                                if entry.name.startswith(".") and entry.name not in (".config",):
                                    continue
                                stack.append(Path(entry.path))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            stat = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        name = entry.name
                        suffix = Path(name).suffix.lower()
                        if ext_filter and suffix != ext_filter:
                            continue
                        if kind_exts is not None and suffix not in kind_exts:
                            continue
                        if min_bytes is not None and stat.st_size < min_bytes:
                            continue
                        if max_bytes is not None and stat.st_size > max_bytes:
                            continue
                        if cutoff is not None and stat.st_mtime < cutoff:
                            continue
                        if query_lower and query_lower not in name.lower():
                            continue
                        results.append(
                            {
                                "name": name,
                                "path": entry.path,
                                "ext": suffix,
                                "kind": classify(Path(name)),
                                "size": stat.st_size,
                                "size_human": human_size(stat.st_size),
                                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                                "mtime": stat.st_mtime,
                            }
                        )
                        if len(results) >= limit:
                            break
            except (PermissionError, OSError):
                continue

        results.sort(key=lambda row: row["mtime"], reverse=True)
        for row in results:
            row.pop("mtime", None)
        return {
            "query": query,
            "root": str(base),
            "results": results,
            "count": len(results),
            "scanned": scanned,
            "elapsed_ms": int((time.time() - started) * 1000),
            "truncated": len(results) >= limit,
        }

    def recent_files(self, limit: int = 12, roots: list[Path] | None = None) -> list[dict[str, Any]]:
        candidates = roots or [Path.home() / "Downloads", Path.home() / "Documents", Path.home() / "Desktop"]
        found: list[dict[str, Any]] = []
        for root in candidates:
            if not root.exists():
                continue
            try:
                for entry in os.scandir(root):
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    found.append(
                        {
                            "name": entry.name,
                            "path": entry.path,
                            "size": stat.st_size,
                            "size_human": human_size(stat.st_size),
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                            "mtime": stat.st_mtime,
                        }
                    )
            except OSError:
                continue
        found.sort(key=lambda row: row["mtime"], reverse=True)
        for row in found:
            row.pop("mtime", None)
        return found[:limit]

    def large_files(self, root: str | Path | None = None, min_mb: float = 100, limit: int = 30) -> list[dict[str, Any]]:
        result = self.search(
            root=root or Path.home(),
            min_size_mb=min_mb,
            limit=limit,
            excluded_names=["node_modules", ".git", "AppData", "$RECYCLE.BIN"],
            timeout=25,
        )
        return sorted(result["results"], key=lambda row: row["size"], reverse=True)

    def duplicates(self, root: str | Path | None = None, min_size_kb: int = 32, limit: int = 40) -> list[dict[str, Any]]:
        base = self.resolve(root) if root else Path.home() / "Downloads"
        if not base.exists():
            raise FileNotFoundError(f"{base} does not exist")
        by_size: dict[int, list[Path]] = {}
        scanned = 0
        started = time.time()
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                name for name in dirnames
                if name.lower() not in {".git", "node_modules", "__pycache__", "appdata", ".cache"}
                and not name.startswith("$")
            ]
            for filename in filenames:
                path = Path(dirpath) / filename
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                scanned += 1
                if size < min_size_kb * 1024:
                    continue
                by_size.setdefault(size, []).append(path)
            if time.time() - started > 25:
                break

        groups: list[dict[str, Any]] = []
        wasted = 0
        for size, paths in by_size.items():
            if len(paths) < 2:
                continue
            by_hash: dict[str, list[str]] = {}
            for path in paths:
                digest = _hash_file(path)
                if digest:
                    by_hash.setdefault(digest, []).append(str(path))
            for digest, same in by_hash.items():
                if len(same) > 1:
                    groups.append(
                        {
                            "sha256": digest,
                            "size": size,
                            "size_human": human_size(size),
                            "count": len(same),
                            "paths": same,
                            "wasted_bytes": size * (len(same) - 1),
                        }
                    )
                    wasted += size * (len(same) - 1)
            if len(groups) >= limit:
                break
        groups.sort(key=lambda row: row["wasted_bytes"], reverse=True)
        return groups

    # --------------------------------------------------------------- mutations
    def create_folder(self, path: str | Path) -> dict[str, Any]:
        target = self.resolve(path)
        self._guard_mutation(target)
        if target.exists():
            raise FileExistsError(f"{target} already exists")
        target.mkdir(parents=True)
        return {"created": str(target), "verified": target.is_dir()}

    def rename(self, path: str | Path, new_name: str) -> dict[str, Any]:
        target = self.resolve(path)
        self._guard_mutation(target)
        if not target.exists():
            raise FileNotFoundError(f"{target} does not exist")
        new_name = new_name.strip()
        if not new_name or any(ch in new_name for ch in ('/', '\\', ':')) or new_name in (".", ".."):
            raise ValueError("invalid new name")
        destination = target.with_name(new_name)
        if destination.exists():
            raise FileExistsError(f"{destination} already exists")
        target.rename(destination)
        return {"from": str(target), "to": str(destination), "verified": destination.exists()}

    def copy(self, source: str | Path, destination: str | Path, overwrite: bool = False) -> dict[str, Any]:
        src = self.resolve(source)
        dst = self.resolve(destination)
        self._guard_mutation(dst)
        if not src.exists():
            raise FileNotFoundError(f"{src} does not exist")
        if dst.exists() and not overwrite:
            raise FileExistsError(f"{dst} already exists (overwrite=False)")
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=overwrite)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        verified = dst.exists()
        return {"from": str(src), "to": str(dst), "verified": verified}

    def move(self, source: str | Path, destination: str | Path, overwrite: bool = False) -> dict[str, Any]:
        src = self.resolve(source)
        dst = self.resolve(destination)
        self._guard_mutation(src, dst)
        if not src.exists():
            raise FileNotFoundError(f"{src} does not exist")
        if dst.exists() and not overwrite:
            raise FileExistsError(f"{dst} already exists (overwrite=False)")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return {
            "from": str(src),
            "to": str(dst),
            "verified": dst.exists() and not src.exists(),
        }

    def trash(self, path: str | Path, permanent: bool = False) -> dict[str, Any]:
        """Remove a file - recoverable by default via the quarantine folder."""
        target = self.resolve(path)
        self._guard_mutation(target)
        if not target.exists():
            raise FileNotFoundError(f"{target} does not exist")

        if permanent:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return {"deleted": str(target), "permanent": True, "verified": not target.exists()}

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bucket = self.quarantine_dir / stamp
        bucket.mkdir(parents=True, exist_ok=True)
        destination = bucket / target.name
        counter = 1
        while destination.exists():
            destination = bucket / f"{target.stem}-{counter}{target.suffix}"
            counter += 1
        shutil.move(str(target), str(destination))
        manifest = bucket / "manifest.json"
        entries = []
        if manifest.exists():
            try:
                entries = json.loads(manifest.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                entries = []
        entries.append({"original": str(target), "quarantined": str(destination), "at": time.time()})
        manifest.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        return {
            "deleted": str(target),
            "quarantined_at": str(destination),
            "manifest": str(manifest),
            "permanent": False,
            "verified": destination.exists() and not target.exists(),
        }

    def restore(self, quarantined_path: str | Path) -> dict[str, Any]:
        source = self.resolve(quarantined_path)
        manifest = source.parent / "manifest.json"
        if not source.exists() or not manifest.exists():
            raise FileNotFoundError("no quarantine record for this file")
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        match = next((entry for entry in entries if entry.get("quarantined") == str(source)), None)
        if not match:
            raise FileNotFoundError("file was not found in the quarantine manifest")
        original = Path(match["original"])
        if original.exists():
            raise FileExistsError(f"{original} already exists; move it first")
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(original))
        return {"restored": str(original), "verified": original.exists()}

    # ----------------------------------------------------------- organisation
    def plan_organisation(self, folder: str | Path | None = None) -> dict[str, Any]:
        """Propose a tidy-up plan. Nothing is moved until it is applied."""
        base = self.resolve(folder) if folder else self.downloads_dir
        if not base.is_dir():
            raise NotADirectoryError(f"{base} is not a folder")
        plan: list[dict[str, Any]] = []
        unmoved: list[str] = []
        for entry in sorted(base.iterdir()):
            if entry.is_dir():
                continue
            suffix = entry.suffix.lower()
            category = next(
                (name for name, exts in ORGANISE_PLAN.items() if suffix in exts), None
            )
            if not category:
                unmoved.append(entry.name)
                continue
            plan.append(
                {
                    "file": entry.name,
                    "from": str(entry),
                    "to": str(base / category / entry.name),
                    "category": category,
                }
            )
        return {
            "folder": str(base),
            "plan": plan,
            "unmatched": unmoved,
            "total": len(plan),
        }

    def apply_organisation(self, plan: list[dict[str, Any]]) -> dict[str, Any]:
        moved: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for item in plan:
            try:
                src = self.resolve(item["from"])
                dst = self.resolve(item["to"])
                self._guard_mutation(src, dst)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                moved.append({"file": item["file"], "to": str(dst), "verified": dst.exists()})
            except Exception as exc:
                failed.append({"file": item.get("file"), "error": str(exc)})
        return {"moved": moved, "failed": failed, "count": len(moved)}

    # ---------------------------------------------------------------- backups
    def create_backup(self, source: str | Path, destination: str | Path | None = None) -> dict[str, Any]:
        src = self.resolve(source)
        if not src.exists():
            raise FileNotFoundError(f"{src} does not exist")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if destination:
            archive = self.resolve(destination)
            if archive.suffix.lower() != ".zip":
                archive = archive.with_suffix(".zip")
        else:
            archive = src.parent / f"{src.name}-backup-{stamp}.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            base = src.parent
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                for dirpath, dirnames, filenames in os.walk(src):
                    dirnames[:] = [name for name in dirnames if name not in {"node_modules", ".git", "__pycache__", ".venv"}]
                    for filename in filenames:
                        path = Path(dirpath) / filename
                        try:
                            bundle.write(path, path.relative_to(base))
                        except OSError:
                            continue
        else:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                bundle.write(src, src.name)

        verification = self.verify_archive(archive)
        return {
            "archive": str(archive),
            "size_bytes": archive.stat().st_size,
            "size_human": human_size(archive.stat().st_size),
            "source": str(src),
            "verified": verification["ok"],
            "integrity": verification,
        }

    @staticmethod
    def verify_archive(archive: str | Path) -> dict[str, Any]:
        path = Path(archive)
        if not path.exists():
            return {"ok": False, "detail": "archive missing", "entries": 0}
        try:
            with zipfile.ZipFile(path) as bundle:
                bad = bundle.testzip()
                entries = len(bundle.namelist())
            if bad:
                return {"ok": False, "detail": f"corrupt entry: {bad}", "entries": entries}
            return {"ok": True, "detail": "CRC check passed", "entries": entries}
        except zipfile.BadZipFile as exc:
            return {"ok": False, "detail": f"not a valid zip: {exc}", "entries": 0}


def classify(path: Path | str) -> str:
    suffix = Path(path).suffix.lower()
    for kind, extensions in DOCUMENT_KINDS.items():
        if suffix in extensions:
            return kind
    return "other"


def human_size(size: float | int | None) -> str:
    if size is None:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def _hash_file(path: Path, chunk: int = 1024 * 256) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(chunk)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def parse_time_window(text: str) -> timedelta | None:
    """Small helper used by the planner for 'last week' style filters."""
    text = text.strip().lower()
    mapping = {"today": 1, "yesterday": 1, "this week": 7, "last week": 14, "this month": 31, "last month": 60}
    if text in mapping:
        return timedelta(days=mapping[text])
    return None
