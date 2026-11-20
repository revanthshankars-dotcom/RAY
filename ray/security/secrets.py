"""Detect credentials accidentally pasted where they do not belong.

Used by long-term memory, settings and notes so an API key never ends up in a
plaintext JSON file, a note body or a memory row.  Detection is deliberately
broad: a false positive only means RAY asks you to use the encrypted credential
store, while a false negative would leak a secret to disk.
"""

from __future__ import annotations

import re

SECRET_PATTERNS = [
    # Provider key shapes (OpenAI/xAI/Google/GitHub/GitLab/Hugging Face).
    re.compile(r"\b(sk|xai|AIza|ghp|gho|ghs|glpat|hf|fsh)[-_A-Za-z0-9]{12,}\b"),
    re.compile(r"(?i)\b(api[_\s-]?key|password|passwd|secret|token)\b\s*(is|:|=)\s*\S+"),
    # Long opaque blobs (base64 keys, session tokens, JWT fragments).
    re.compile(r"\b[A-Za-z0-9_\-+/=]{32,}\b"),
]
# Checksums and git object ids look like secrets to the blob rule but are not:
# skipping them keeps ordinary notes and commit hashes writable.
_HARMLESS_DIGEST = re.compile(r"^[0-9a-fA-F]{32,128}$")
_WORD_PART = re.compile(r"^[A-Za-z]{0,12}[0-9]{0,4}$")
_NAMED = tuple(SECRET_PATTERNS[:2])


def looks_secret(text: str) -> bool:
    candidate = text or ""
    if any(pattern.search(candidate) for pattern in _NAMED):
        return True
    for match in SECRET_PATTERNS[2].finditer(candidate):
        blob = match.group(0).strip("=+/")
        if _HARMLESS_DIGEST.match(blob):
            continue
        if blob.count("_") >= 2 and all(
            _WORD_PART.match(part) for part in blob.split("_")
        ):
            continue  # snake_case file name, not a key
        letters = any(ch.isalpha() for ch in blob)
        digits = any(ch.isdigit() for ch in blob)
        if letters and (digits or len(blob) >= 40):
            return True
    return False
