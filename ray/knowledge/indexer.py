"""Local file index with SQLite FTS5.

Incremental (mtime-based), prunes excluded directories, and never uploads
anything. Content extraction is capped by size and by the supported-format list,
so indexing a normal Documents folder stays fast on a Celeron.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..core.database import Database, utcnow
from ..core.logging_setup import get_logger
from ..computer.files import classify, human_size
from .extract import SUPPORTED, extract

log = get_logger("knowledge.indexer")

#: Partial downloads and lock files are never worth indexing.
EXCLUDED_SUFFIXES = {".tmp", ".temp", ".part", ".partial", ".crdownload", ".download", ".lock"}

DEFAULT_EXCLUDES = {
    "$RECYCLE.BIN", "System Volume Information", "node_modules", ".git", "__pycache__",
    ".venv", "venv", ".cache", ".npm", ".gradle", "AppData", "Windows", "Program Files",
    "Program Files (x86)", ".mozilla", ".config", "site-packages",
}


class FileIndexer:
    def __init__(self, db: Database, config) -> None:
        self.db = db
        self.config = config
        self._cancel = threading.Event()
        self._running = False
        self._lock = threading.Lock()
        self.progress: dict[str, Any] = {"running": False, "seen": 0, "indexed": 0, "skipped": 0, "current": ""}

    # ------------------------------------------------------------------ roots
    def roots(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM index_roots ORDER BY path")
        if rows:
            return rows
        # sensible defaults, only if they exist on this machine
        defaults = [
            Path.home() / "Documents",
            Path.home() / "Downloads",
            Path.home() / "Desktop",
        ]
        existing = [{"path": str(path), "enabled": 1, "file_count": 0, "last_indexed": None} for path in defaults if path.exists()]
        for item in existing:
            self.add_root(item["path"])
        return self.db.query("SELECT * FROM index_roots ORDER BY path")

    def add_root(self, path: str | Path) -> dict[str, Any]:
        target = Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
        if not target.is_dir():
            raise NotADirectoryError(f"{target} is not a folder")
        existing = self.db.query_one("SELECT * FROM index_roots WHERE path = ?", (str(target),))
        if existing:
            self.db.execute("UPDATE index_roots SET enabled = 1 WHERE id = ?", (existing["id"],))
        else:
            self.db.insert("index_roots", {"path": str(target), "enabled": 1})
        return self.db.query_one("SELECT * FROM index_roots WHERE path = ?", (str(target),)) or {}

    def remove_root(self, path: str) -> bool:
        target = str(Path(os.path.expanduser(path)).resolve())
        row = self.db.query_one("SELECT id FROM index_roots WHERE path = ?", (target,))
        if not row:
            return False
        self.db.execute("DELETE FROM index_roots WHERE id = ?", (row["id"],))
        ids = [item["id"] for item in self.db.query("SELECT id FROM files WHERE path LIKE ?", (target + os.sep + "%",))]
        for file_id in ids:
            self._delete_file(file_id)
        return True

    # ---------------------------------------------------------------- indexing
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def running(self) -> bool:
        return self._running

    def index(
        self,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
        max_files: int | None = None,
    ) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "detail": "an index run is already in progress", **self.progress}
        self._cancel.clear()
        self._running = True
        started = time.time()
        seen = indexed = skipped = 0
        errors = 0
        exhaustive = self.config.get("indexing.index_content", True)
        max_bytes = int(self.config.get("indexing.max_file_mb", 25) or 25) * 1024 * 1024
        excludes = set(self.config.get("indexing.excluded_names", []) or []) | DEFAULT_EXCLUDES
        try:
            roots = [root for root in self.roots() if root.get("enabled", 1)]
            for root in roots:
                base = Path(root["path"])
                if not base.is_dir():
                    continue
                for dirpath, dirnames, filenames in os.walk(base):
                    if self._cancel.is_set():
                        break
                    dirnames[:] = [
                        name for name in dirnames
                        if name.lower() not in {item.lower() for item in excludes} and not name.startswith("$")
                    ]
                    for filename in filenames:
                        if self._cancel.is_set() or (max_files and seen >= max_files):
                            break
                        seen += 1
                        path = Path(dirpath) / filename
                        if filename.startswith("~$") or path.suffix.lower() in EXCLUDED_SUFFIXES:
                            skipped += 1
                            continue
                        try:
                            stat = path.stat()
                        except OSError:
                            skipped += 1
                            continue
                        if stat.st_size > max_bytes:
                            skipped += 1
                            continue
                        try:
                            changed = self._index_file(path, stat, exhaustive)
                            indexed += 1 if changed else 0
                        except Exception as exc:
                            errors += 1
                            log.debug("indexing failed for %s: %s", path, exc)
                        if progress_cb and seen % 25 == 0:
                            progress_cb({"seen": seen, "indexed": indexed, "current": str(path)})
                        self.progress = {
                            "running": True, "seen": seen, "indexed": indexed,
                            "skipped": skipped, "current": str(path),
                        }
                self.db.execute(
                    "UPDATE index_roots SET last_indexed = ?, file_count = "
                    "(SELECT COUNT(*) FROM files WHERE path LIKE ?) WHERE id = ?",
                    (utcnow(), root["path"].rstrip(os.sep) + os.sep + "%", root["id"]),
                )
                if self._cancel.is_set() or (max_files and seen >= max_files):
                    break
            duration = time.time() - started
            result = {
                "ok": True,
                "seen": seen,
                "indexed": indexed,
                "skipped": skipped,
                "errors": errors,
                "cancelled": self._cancel.is_set(),
                "seconds": round(duration, 1),
            }
            log.info(
                "index run complete: %s scanned, %s stored in %.1fs",
                seen, indexed, duration,
            )
            return result
        finally:
            self._running = False
            self.progress = {"running": False, "seen": seen, "indexed": indexed, "skipped": skipped, "current": ""}
            self._lock.release()

    def _index_file(self, path: Path, stat: os.stat_result, read_content: bool) -> bool:
        existing = self.db.query_one("SELECT id, mtime, size FROM files WHERE path = ?", (str(path),))
        if existing and abs(float(existing["mtime"]) - stat.st_mtime) < 1 and existing["size"] == stat.st_size:
            return False  # unchanged

        text = ""
        preview = ""
        if read_content and path.suffix.lower() in SUPPORTED:
            try:
                document = extract(path, max_chars=12000)
                text = document.get("text", "")
                preview = re.sub(r"\s+", " ", text[:280]).strip()
            except Exception as exc:
                log.debug("extract failed for %s: %s", path, exc)
        row = {
            "path": str(path),
            "name": path.name,
            "ext": path.suffix.lower(),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "kind": classify(path),
            "content_chars": len(text),
            "preview": preview,
            "indexed_at": utcnow(),
        }
        if existing:
            file_id = int(existing["id"])
            self.db.update("files", file_id, row)
            self.db.execute("DELETE FROM files_fts WHERE rowid = ?", (file_id,))
        else:
            file_id = self.db.insert("files", row)
        if text:
            self.db.execute(
                "INSERT INTO files_fts (rowid, name, path, content) VALUES (?,?,?,?)",
                (file_id, path.name, str(path), text[:40000]),
            )
        return True

    def _delete_file(self, file_id: int) -> None:
        self.db.execute("DELETE FROM files_fts WHERE rowid = ?", (file_id,))
        self.db.delete("files", file_id)

    def refresh_path(self, path: str | Path) -> dict[str, Any]:
        target = Path(path)
        if not target.exists():
            row = self.db.query_one("SELECT id FROM files WHERE path = ?", (str(target),))
            if row:
                self._delete_file(int(row["id"]))
            return {"removed": str(target)}
        stat = target.stat()
        if target.is_dir():
            return {"skipped": "directory"}
        changed = self._index_file(target, stat, self.config.get("indexing.index_content", True))
        return {"indexed": str(target), "changed": changed}

    def clear(self) -> dict[str, int]:
        files = int(self.db.scalar("SELECT COUNT(*) FROM files", default=0) or 0)
        self.db.execute("DELETE FROM files_fts")
        self.db.execute("DELETE FROM files")
        return {"removed": files}

    def stats(self) -> dict[str, Any]:
        total = int(self.db.scalar("SELECT COUNT(*) FROM files", default=0) or 0)
        size = int(self.db.scalar("SELECT COALESCE(SUM(size),0) FROM files", default=0) or 0)
        by_kind = self.db.query("SELECT kind, COUNT(*) AS count FROM files GROUP BY kind ORDER BY count DESC")
        last = self.db.scalar("SELECT MAX(indexed_at) FROM files")
        return {
            "files": total,
            "size_bytes": size,
            "size_human": human_size(size),
            "by_kind": by_kind,
            "last_indexed": last,
            "roots": self.roots(),
            "running": self._running,
            "content_indexing": bool(self.config.get("indexing.index_content", True)),
        }


def safe_fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression."""
    tokens = re.findall(r"[A-Za-z0-9_.-]{2,}", query or "")
    if not tokens:
        return ""
    return " ".join(f'"{token}"*' for token in tokens[:8])


class IndexSearch:
    def __init__(self, db: Database) -> None:
        self.db = db

    def search(
        self,
        query: str = "",
        extension: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        started = time.time()
        clauses: list[str] = []
        params: list[Any] = []
        if extension:
            ext = extension if extension.startswith(".") else f".{extension}"
            clauses.append("f.ext = ?")
            params.append(ext.lower())
        if kind:
            clauses.append("f.kind = ?")
            params.append(kind)
        where = f"AND {' AND '.join(clauses)}" if clauses else ""

        rows: list[dict[str, Any]] = []
        mode = "filename"
        if query.strip():
            match = safe_fts_query(query)
            if match:
                try:
                    rows = self.db.query(
                        "SELECT f.*, snippet(files_fts, 3, '[', ']', '…', 12) AS snippet, "
                        "bm25(files_fts) AS rank "
                        "FROM files_fts JOIN files f ON f.id = files_fts.rowid "
                        f"WHERE files_fts MATCH ? {where} ORDER BY rank LIMIT ?",
                        (match, *params, limit),
                    )
                    mode = "full-text"
                except Exception as exc:
                    log.warning("FTS query failed (%s); falling back to LIKE", exc)
                    rows = []
            if not rows:
                like = f"%{query.strip()}%"
                rows = self.db.query(
                    "SELECT f.*, f.preview AS snippet FROM files f "
                    f"WHERE (f.name LIKE ? OR f.path LIKE ? OR f.preview LIKE ?) {where} "
                    "ORDER BY f.mtime DESC LIMIT ?",
                    (like, like, like, *params, limit),
                )
                mode = "name/preview"
        else:
            rows = self.db.query(
                "SELECT f.*, f.preview AS snippet FROM files f "
                f"WHERE 1=1 {where} ORDER BY f.mtime DESC LIMIT ?",
                (*params, limit),
            )
        for row in rows:
            row["size_human"] = human_size(row.get("size", 0))
            row["modified"] = (
                datetime.fromtimestamp(row["mtime"]).isoformat(timespec="seconds")
                if row.get("mtime") else None
            )
        return {
            "query": query,
            "mode": mode,
            "results": rows,
            "count": len(rows),
            "elapsed_ms": int((time.time() - started) * 1000),
        }
