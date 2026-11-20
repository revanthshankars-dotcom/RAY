"""Encrypted local credential store.

Keys are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using a 32-byte key
that is generated on first use and stored with ``0600`` permissions next to the
database.  Ciphertext lives in SQLite; plaintext never touches disk, logs or the
HTTP API.

Environment variables are honoured as a fallback (useful for CI and for people
who prefer to keep secrets out of the database entirely):

    RAY_GROK_API_KEY / XAI_API_KEY       grok
    RAY_GEMINI_API_KEY / GEMINI_API_KEY  gemini
    RAY_FISH_API_KEY / FISH_AUDIO_API_KEY fish

A value set through the UI always wins over the environment.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from ..core.database import Database, utcnow
from ..core.logging_setup import get_logger

log = get_logger("security.credentials")

PROVIDERS = ("grok", "gemini", "fish")

ENV_NAMES: dict[str, tuple[str, ...]] = {
    "grok": ("RAY_GROK_API_KEY", "XAI_API_KEY", "GROK_API_KEY"),
    "gemini": ("RAY_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "fish": ("RAY_FISH_API_KEY", "FISH_AUDIO_API_KEY", "FISH_API_KEY"),
}


class CredentialError(RuntimeError):
    pass


class CredentialStore:
    def __init__(self, db: Database, key_path: Path | str) -> None:
        self.db = db
        self.key_path = Path(key_path)
        self._lock = threading.RLock()
        self._fernet = None

    # ------------------------------------------------------------------ crypto
    def _load_key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:  # pragma: no cover - Windows may ignore
            pass
        log.info("generated new local encryption key at %s", self.key_path)
        return key

    @property
    def fernet(self):
        if self._fernet is None:
            try:
                from cryptography.fernet import Fernet
            except ImportError as exc:  # pragma: no cover
                raise CredentialError(
                    "The 'cryptography' package is required for the credential store. "
                    "Install it with: pip install cryptography"
                ) from exc
            with self._lock:
                if self._fernet is None:
                    self._fernet = Fernet(self._load_key())
        return self._fernet

    # ------------------------------------------------------------------- CRUD
    def set(self, name: str, value: str) -> None:
        name = name.strip().lower()
        value = (value or "").strip()
        if not name:
            raise CredentialError("credential name is required")
        if not value:
            raise CredentialError("credential value is empty")
        if any(ch.isspace() for ch in value):
            raise CredentialError("credential must not contain whitespace")
        token = self.fernet.encrypt(value.encode("utf-8"))
        now = utcnow()
        existing = self.db.query_one("SELECT name FROM credentials WHERE name = ?", (name,))
        if existing:
            self.db.execute(
                "UPDATE credentials SET ciphertext = ?, updated_at = ? WHERE name = ?",
                (token, now, name),
            )
        else:
            self.db.execute(
                "INSERT INTO credentials (name, ciphertext, created_at, updated_at) VALUES (?,?,?,?)",
                (name, token, now, now),
            )
        log.info("stored credential '%s'", name)

    def get(self, name: str) -> str | None:
        name = name.strip().lower()
        row = self.db.query_one("SELECT ciphertext FROM credentials WHERE name = ?", (name,))
        if row:
            try:
                return self.fernet.decrypt(row["ciphertext"]).decode("utf-8")
            except Exception as exc:
                log.error("could not decrypt credential '%s': %s", name, exc)
                raise CredentialError(
                    f"Stored credential '{name}' cannot be decrypted. Re-enter it in Settings."
                ) from exc
        for env_name in ENV_NAMES.get(name, ()):
            value = os.environ.get(env_name)
            if value:
                return value.strip()
        return None

    def source(self, name: str) -> str:
        if self.db.query_one("SELECT 1 FROM credentials WHERE name = ?", (name,)):
            return "vault"
        for env_name in ENV_NAMES.get(name, ()):
            if os.environ.get(env_name):
                return f"env:{env_name}"
        return "unset"

    def delete(self, name: str) -> bool:
        name = name.strip().lower()
        row = self.db.query_one("SELECT 1 FROM credentials WHERE name = ?", (name,))
        if not row:
            return False
        self.db.execute("DELETE FROM credentials WHERE name = ?", (name,))
        log.info("removed credential '%s'", name)
        return True

    def names(self) -> list[str]:
        rows = self.db.query("SELECT name FROM credentials ORDER BY name")
        return [row["name"] for row in rows]

    def has(self, name: str) -> bool:
        return bool(self.get(name))

    def status(self) -> list[dict[str, Any]]:
        """Metadata only - never the secret itself."""
        stored = {row["name"]: row for row in self.db.query("SELECT * FROM credentials")}
        out: list[dict[str, Any]] = []
        for name in PROVIDERS:
            row = stored.get(name)
            src = self.source(name)
            out.append(
                {
                    "name": name,
                    "configured": src != "unset",
                    "source": src,
                    "updated_at": row["updated_at"] if row else None,
                    "last_tested": row["last_tested"] if row else None,
                    "last_test_ok": bool(row["last_test_ok"]) if row and row["last_test_ok"] is not None else None,
                    "last_test_detail": (row["last_test_detail"] if row else None),
                    "masked": "••••••••" if src != "unset" else "",
                }
            )
        return out

    def record_test(self, name: str, ok: bool, detail: str) -> None:
        self.db.execute(
            "UPDATE credentials SET last_tested = ?, last_test_ok = ?, last_test_detail = ? WHERE name = ?",
            (utcnow(), 1 if ok else 0, detail[:500], name.strip().lower()),
        )

    # ------------------------------------------------------------------ misc
    def export_encrypted(self, destination: Path) -> Path:
        """Copy the vault + key so credentials can be restored on another PC."""
        import shutil

        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        self.db.backup(destination / "vault.db")
        shutil.copy2(self.key_path, destination / "secret.key")
        try:
            os.chmod(destination / "secret.key", 0o600)
        except OSError:
            pass
        return destination

    def rotate_key(self) -> None:
        """Re-encrypt every stored credential under a fresh key."""
        from cryptography.fernet import Fernet

        rows = self.db.query("SELECT name, ciphertext FROM credentials")
        plain = [(row["name"], self.fernet.decrypt(row["ciphertext"])) for row in rows]
        new_key = Fernet.generate_key()
        new_fernet = Fernet(new_key)
        with self._lock:
            for name, value in plain:
                token = new_fernet.encrypt(value)
                self.db.execute("UPDATE credentials SET ciphertext = ?, updated_at = ? WHERE name = ?",
                                (token, utcnow(), name))
            self.key_path.write_bytes(new_key)
            try:
                os.chmod(self.key_path, 0o600)
            except OSError:
                pass
            self._fernet = new_fernet
        log.info("credential vault key rotated (%d entries)", len(plain))
