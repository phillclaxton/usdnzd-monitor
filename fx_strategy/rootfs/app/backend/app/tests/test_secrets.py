"""Secret store tests.

The point of these is narrow and important: a credential must not be readable
from the file on disk, must not appear in a status payload, and must not survive
in the store once deleted.
"""

from __future__ import annotations

import json
import stat

import pytest

from app.security.secrets import SecretError, SecretStore, mask

TOKEN = "wise-live-token-2f8a91c4"


@pytest.fixture
def store(app_config: object) -> SecretStore:
    from app.config import get_config

    return SecretStore(get_config())


def test_round_trip(store: SecretStore) -> None:
    store.set("wise_api_token", TOKEN)
    assert store.get("wise_api_token") == TOKEN


def test_the_token_is_not_readable_from_the_file(store: SecretStore) -> None:
    store.set("wise_api_token", TOKEN)
    raw = store.path.read_text(encoding="utf-8")
    assert TOKEN not in raw
    # The stored value is a Fernet token, not the plaintext.
    assert json.loads(raw)["wise_api_token"].startswith("gAAAAA")


def test_file_permissions_are_owner_only(store: SecretStore) -> None:
    store.set("wise_api_token", TOKEN)
    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600


def test_status_never_reveals_the_value(store: SecretStore) -> None:
    store.set("wise_api_token", TOKEN)
    status = store.status()
    assert status["wise_api_token"]["configured"] is True
    assert status["wise_api_token"]["hint"] == "••••••••91c4"
    assert TOKEN not in json.dumps(status)


def test_deleting_removes_the_value(store: SecretStore) -> None:
    store.set("wise_api_token", TOKEN)
    store.delete("wise_api_token")
    assert store.get("wise_api_token") is None
    assert store.has("wise_api_token") is False
    assert TOKEN not in store.path.read_text(encoding="utf-8")


def test_setting_an_empty_value_deletes(store: SecretStore) -> None:
    store.set("wise_api_token", TOKEN)
    store.set("wise_api_token", "")
    assert store.has("wise_api_token") is False


def test_unknown_keys_are_refused(store: SecretStore) -> None:
    for action in (
        lambda: store.set("bank_password", "x"),
        lambda: store.get("bank_password"),
        lambda: store.delete("bank_password"),
    ):
        with pytest.raises(SecretError, match="unknown secret"):
            action()


def test_a_replaced_key_file_reports_rather_than_pretending(store: SecretStore) -> None:
    store.set("wise_api_token", TOKEN)
    from cryptography.fernet import Fernet

    store.key_path.write_bytes(Fernet.generate_key())
    with pytest.raises(SecretError, match="cannot be decrypted"):
        store.get("wise_api_token")


def test_a_corrupt_file_is_an_error_not_an_empty_store(store: SecretStore) -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SecretError, match="not valid JSON"):
        store.get("wise_api_token")


def test_reading_an_unset_secret_returns_none(store: SecretStore) -> None:
    assert store.get("generic_api_key") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", ""), ("abc", "•••"), ("abcdefgh", "••••••••efgh")],
)
def test_mask(value: str, expected: str) -> None:
    assert mask(value) == expected
