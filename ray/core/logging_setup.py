"""Structured logging.

One rotating file handler (10 MB x 5) plus a console handler.  Every record
carries the operation context when available.  Secrets are scrubbed by a
filter before anything leaves the process.
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{6,})"),
    re.compile(r"(xai-[A-Za-z0-9_\-]{6,})"),
    re.compile(r"(AIza[0-9A-Za-z_\-]{10,})"),
    re.compile(r"(?i)(api[_-]?key[\"'\s:=]+)([^\s\"',]{6,})"),
    re.compile(r"(?i)(authorization[\"'\s:=]+bearer\s+)([^\s\"',]{6,})"),
]

_configured = False


def redact(text: str) -> str:
    """Remove anything that looks like a credential."""
    if not text:
        return text
    out = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 1:
            out = pattern.sub(lambda m: m.group(1)[:4] + "***REDACTED***", out)
        else:
            out = pattern.sub(lambda m: m.group(1) + "***REDACTED***", out)
    return out


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def setup_logging(log_dir: Path | str | None = None, level: str = "INFO", console: bool = True) -> logging.Logger:
    global _configured
    root = logging.getLogger("ray")
    if _configured:
        return root

    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    redactor = _RedactingFilter()

    if log_dir is not None:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            Path(log_dir) / "ray.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        stream.addFilter(redactor)
        root.addHandler(stream)

    root.propagate = False
    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ray.{name}" if not name.startswith("ray") else name)
