"""Configuring the vendor-neutral HTTP rate provider.

The provider itself has always been able to talk to any JSON rate API; this
module is what lets a user set one up without editing files. Presets supply the
defaults for a known vendor, and every field stays editable afterwards, because
the point of the generic provider is not to be tied to the list.

The API key lives in the encrypted secret store, never in the settings
document, the audit trail or any response.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import get_logger
from app.models.audit import AuditEventType
from app.providers.base import ProviderError
from app.providers.generic import PRESETS, GenericProvider, apply_preset
from app.schemas.settings import GenericProviderSettings, Settings
from app.security.secrets import get_secret_store, mask
from app.services import audit, settings_service

log = get_logger(__name__)

#: The name under which the key is filed in the secret store — not a secret itself.
CREDENTIAL_NAME = "generic_api_key"


@dataclass(frozen=True, slots=True)
class GenericStatus:
    """Everything the settings screen needs, with nothing sensitive in it."""

    enabled: bool
    configured: bool
    display_name: str
    preset: str
    base_url: str
    supports_history: bool
    key_required: bool
    key_hint: str
    message: str
    #: Populated only by an explicit test.
    rate: Decimal | None = None
    latency_ms: int | None = None


def _key() -> str | None:
    return get_secret_store().get(CREDENTIAL_NAME)


def status(settings: Settings) -> GenericStatus:
    """Describe the configuration without calling the provider."""
    generic = settings.providers.generic
    stored = _key()
    key_required = generic.auth_style != "none"

    if not generic.base_url:
        message = "No base URL yet. Choose a preset or enter one."
    elif key_required and not stored:
        message = "An API key is needed for the selected authentication style."
    else:
        message = "Ready. Use Test to make one live call."

    return GenericStatus(
        enabled=generic.enabled,
        configured=bool(generic.base_url) and (not key_required or bool(stored)),
        display_name=generic.display_name or "Generic API provider",
        preset=generic.preset,
        base_url=generic.base_url,
        supports_history=bool(generic.history_path),
        key_required=key_required,
        key_hint=mask(stored),
        message=message,
    )


async def test(settings: Settings) -> GenericStatus:
    """Make one real call and report precisely what happened.

    A failure is returned as a message rather than raised, because the point of
    the button is to tell the user which part of their configuration is wrong.
    """
    current = status(settings)
    generic = settings.providers.generic

    if not generic.base_url:
        return current

    provider: GenericProvider | None = None
    try:
        provider = GenericProvider(generic, _key())
        quote = await provider.get_spot_rate(
            settings.general.source_currency, settings.general.target_currency
        )
    except ProviderError as exc:
        log.warning("generic_provider_test_failed", error=exc.message)
        return replace(current, message=exc.message)
    finally:
        if provider is not None:
            await provider.aclose()

    return replace(
        current,
        configured=True,
        message=(
            f"Success: 1 {quote.source_currency} = {quote.rate} {quote.target_currency}"
            + ("" if quote.provider_timestamp else " (the provider sent no timestamp)")
        ),
        rate=quote.rate,
        latency_ms=quote.latency_ms,
    )


async def store_credentials(
    session: AsyncSession, *, api_key: str | None, actor: str = "user"
) -> None:
    """Save or clear the API key, recording only that it changed."""
    store = get_secret_store()
    if api_key:
        store.set(CREDENTIAL_NAME, api_key)
        message = "Generic provider API key stored"
    else:
        store.delete(CREDENTIAL_NAME)
        message = "Generic provider API key removed"

    await audit.record(
        session,
        event_type=AuditEventType.CREDENTIAL_CHANGED,
        entity_type="credential",
        entity_id=CREDENTIAL_NAME,
        message=message,
        after={"configured": bool(api_key)},
        actor=actor,
    )


async def save(
    session: AsyncSession,
    settings: Settings,
    values: GenericProviderSettings,
    *,
    actor: str = "user",
) -> Settings:
    """Persist the non-secret configuration."""
    return await settings_service.patch_section(
        session, "providers", {"generic": values}, actor=actor
    )


async def use_preset(
    session: AsyncSession, settings: Settings, preset_key: str, *, actor: str = "user"
) -> Settings:
    """Apply a preset's defaults, leaving everything else untouched.

    The preset is applied server-side so the defaults have exactly one
    definition, the same one the provider tests run against.
    """
    updated = apply_preset(settings.providers.generic, preset_key)
    return await save(session, settings, updated, actor=actor)


def presets() -> list[dict[str, object]]:
    """The full preset list, including the fields the form will fill in."""
    return [
        {
            "key": preset.key,
            "display_name": preset.display_name,
            "base_url": preset.base_url,
            "requires_key": preset.requires_key,
            "auth_style": preset.auth_style,
            "notes": preset.notes,
        }
        for preset in PRESETS.values()
    ]
