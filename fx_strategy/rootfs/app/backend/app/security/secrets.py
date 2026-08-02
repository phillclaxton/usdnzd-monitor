"""Credential storage.

Secrets live in ``/data/secrets.json`` with mode ``0600``, encrypted with a key
held in a separate file (``/data/secret.key``, also ``0600``).  They are never
written to the database, never returned by the API in full, never logged and
never included in a normal export.

Splitting the key from the ciphertext does not defend against an attacker who
already has root on the host — nothing at this layer could.  What it does buy is
that a leaked backup archive, a copied database, or a diagnostics bundle does
not on its own hand over a working Wise token.
"""

from __future__ import annotations

import base64
import json
import os
import secrets as pysecrets
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import AppConfig, get_config
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Known credential slots. Anything else is rejected so a typo cannot silently
#: create a second, never-read secret.
SECRET_KEYS = frozenset(
    {
        "wise_api_token",
        "generic_api_key",
        "mqtt_password",
    }
)

FILE_MODE = 0o600
DIR_MODE = 0o700


class SecretError(RuntimeError):
    """Raised when the secret store cannot be read or written."""


def mask(value: str | None) -> str:
    """Render a credential for display: last four characters only."""
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return f"{'•' * 8}{value[-4:]}"


class SecretStore:
    """Encrypted key/value store for API credentials."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config or get_config()

    @property
    def path(self) -> Path:
        return self._config.secrets_path

    @property
    def key_path(self) -> Path:
        return self._config.secret_key_path

    # -- key management ---------------------------------------------------

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            raw = self.key_path.read_bytes().strip()
            if raw:
                self._harden(self.key_path)
                return raw
        key = Fernet.generate_key()
        self.key_path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        self.key_path.write_bytes(key)
        self._harden(self.key_path)
        log.info("secret_key_created", path=str(self.key_path))
        return key

    @staticmethod
    def _harden(path: Path) -> None:
        try:
            os.chmod(path, FILE_MODE)
        except OSError as exc:  # pragma: no cover - depends on the filesystem
            log.warning("secret_permissions_not_applied", path=str(path), error=str(exc))

    def _cipher(self) -> Fernet:
        return Fernet(self._load_or_create_key())

    # -- storage ----------------------------------------------------------

    def _read_raw(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise SecretError("The secrets file is not valid JSON.") from exc
        if not isinstance(document, dict):
            raise SecretError("The secrets file has an unexpected structure.")
        return {str(k): str(v) for k, v in document.items()}

    def _write_raw(self, document: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        # Write via a temporary file so an interrupted write cannot truncate an
        # existing set of working credentials.
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        self._harden(temporary)
        temporary.replace(self.path)
        self._harden(self.path)

    # -- public API -------------------------------------------------------

    def get(self, key: str) -> str | None:
        """Return the decrypted secret, or ``None`` when it is not set."""
        if key not in SECRET_KEYS:
            raise SecretError(f"unknown secret {key!r}")
        stored = self._read_raw().get(key)
        if not stored:
            return None
        try:
            return self._cipher().decrypt(stored.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            # A mismatched key means the value is unrecoverable. Say so rather
            # than pretending the credential is simply absent.
            log.error("secret_undecryptable", key=key)
            raise SecretError(
                f"{key} cannot be decrypted; the key file may have been replaced. "
                "Re-enter the credential to fix this."
            ) from None

    def set(self, key: str, value: str) -> None:
        if key not in SECRET_KEYS:
            raise SecretError(f"unknown secret {key!r}")
        if not value:
            self.delete(key)
            return
        document = self._read_raw()
        document[key] = self._cipher().encrypt(value.encode("utf-8")).decode("ascii")
        self._write_raw(document)
        log.info("secret_stored", key=key)

    def delete(self, key: str) -> None:
        if key not in SECRET_KEYS:
            raise SecretError(f"unknown secret {key!r}")
        document = self._read_raw()
        if document.pop(key, None) is not None:
            self._write_raw(document)
            log.info("secret_deleted", key=key)

    def has(self, key: str) -> bool:
        return bool(self._read_raw().get(key))

    def status(self) -> dict[str, dict[str, Any]]:
        """Describe which credentials are set, without revealing them."""
        result: dict[str, dict[str, Any]] = {}
        for key in sorted(SECRET_KEYS):
            configured = self.has(key)
            hint = ""
            if configured:
                try:
                    hint = mask(self.get(key))
                except SecretError:
                    hint = "unreadable"
            result[key] = {"configured": configured, "hint": hint}
        return result

    def file_permissions(self) -> str | None:
        """The secrets file mode, for the diagnostics page."""
        if not self.path.exists():
            return None
        return oct(self.path.stat().st_mode & 0o777)


def generate_token(length: int = 32) -> str:
    """Random token used for confirmation flows."""
    return base64.urlsafe_b64encode(pysecrets.token_bytes(length)).decode("ascii").rstrip("=")


_store: SecretStore | None = None


def get_secret_store() -> SecretStore:
    global _store
    if _store is None:
        _store = SecretStore()
    return _store


def reset_secret_store() -> None:
    """Drop the cached store. Used by tests."""
    global _store
    _store = None
