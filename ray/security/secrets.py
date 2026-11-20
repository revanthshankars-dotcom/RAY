"""Detect credentials accidentally pasted where they do not belong.

Used by long-term memory, settings and notes so an API key never ends up in a
plaintext JSON file, a note body or a memory row.  Detection is deliberately
broad: a false positive only means RAY asks you to use the encrypted credential
store, while a false negative would leak a secret to disk.
"""

from __future__ import annotations

import re

SECRET_PATTERNS = [
    re.compile(r"\b(sk|xai|AIza|ghp|gho|glpat|hf)[-_A-Za-z0-9]{12,}\b"),
    re.compile(r"(?i)\b(api[_\s-]?key|password|passwd|secret|token)\b\s*(is|:|=)\s*\S+"),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
]


def looks_secret(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in SECRET_PATTERNS)
