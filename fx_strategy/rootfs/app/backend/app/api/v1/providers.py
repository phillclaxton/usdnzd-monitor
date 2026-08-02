"""Rate provider configuration.

Provider *health* lives under ``/rates/providers``; this router is about
setting one up. Only the generic provider needs it — Wise has its own module,
and the manual provider has nothing to configure.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from app.api.deps import ActorDep, SessionDep, SettingsDep
from app.api.errors import ValidationError
from app.providers.generic import PRESETS
from app.schemas.common import RateStr, Schema, StrictSchema
from app.schemas.settings import GenericProviderSettings
from app.services import generic_provider_service as generic

router = APIRouter(prefix="/providers", tags=["providers"])


class PresetOut(Schema):
    key: str
    display_name: str
    base_url: str
    requires_key: bool
    auth_style: str
    notes: str


class GenericStatusOut(Schema):
    enabled: bool
    configured: bool
    display_name: str
    preset: str
    base_url: str
    supports_history: bool
    key_required: bool
    #: Last four characters only; the key itself is never returned.
    key_hint: str
    message: str
    rate: RateStr | None = None
    latency_ms: int | None = None


class GenericConfigOut(Schema):
    """The stored configuration plus its status."""

    config: GenericProviderSettings
    status: GenericStatusOut


class GenericConfigIn(StrictSchema):
    """Every field is optional so the form can save one at a time.

    ``api_key`` is write-only: an empty string removes the stored key, and it is
    never echoed back.
    """

    enabled: bool | None = None
    display_name: str | None = None
    base_url: str | None = None
    rate_path: str | None = None
    history_path: str | None = None
    auth_style: Literal["header", "query", "bearer", "none"] | None = None
    auth_name: str | None = None
    source_param: str | None = None
    target_param: str | None = None
    rate_json_path: str | None = None
    timestamp_json_path: str | None = None
    convention: Literal["target_per_source", "source_per_target"] | None = None
    provider_timezone: str | None = None
    min_seconds_between_calls: int | None = None
    timeout_seconds: float | None = None
    api_key: str | None = None


def _status_out(status: generic.GenericStatus) -> GenericStatusOut:
    return GenericStatusOut(
        enabled=status.enabled,
        configured=status.configured,
        display_name=status.display_name,
        preset=status.preset,
        base_url=status.base_url,
        supports_history=status.supports_history,
        key_required=status.key_required,
        key_hint=status.key_hint,
        message=status.message,
        rate=status.rate,
        latency_ms=status.latency_ms,
    )


@router.get("/presets", response_model=list[PresetOut], summary="Known provider presets")
async def list_presets() -> list[PresetOut]:
    """Defaults for vendors whose response shape is already known.

    A preset is only a starting point: every field stays editable, so a provider
    that is not on this list is configured the same way.
    """
    return [PresetOut.model_validate(preset) for preset in generic.presets()]


@router.get("/generic", response_model=GenericConfigOut, summary="Generic provider settings")
async def get_generic(settings: SettingsDep) -> GenericConfigOut:
    return GenericConfigOut(
        config=settings.providers.generic,
        status=_status_out(generic.status(settings)),
    )


@router.put("/generic", response_model=GenericConfigOut, summary="Update the generic provider")
async def update_generic(
    payload: GenericConfigIn, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> GenericConfigOut:
    """Save the configuration, sending the key to the encrypted store."""
    if payload.api_key is not None:
        await generic.store_credentials(session, api_key=payload.api_key or None, actor=actor)

    updates = payload.model_dump(exclude_none=True, exclude={"api_key"})
    if updates:
        # Revalidated as a model before it reaches patch_section, so no
        # unvalidated value is written.
        merged = GenericProviderSettings.model_validate(
            {**settings.providers.generic.model_dump(), **updates}
        )
        settings = await generic.save(session, settings, merged, actor=actor)

    return await get_generic(settings)


@router.post(
    "/generic/preset/{preset_key}",
    response_model=GenericConfigOut,
    summary="Apply a preset",
)
async def apply_generic_preset(
    preset_key: str, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> GenericConfigOut:
    if preset_key not in PRESETS:
        raise ValidationError(
            f"Unknown preset {preset_key!r}. Choose one of: {', '.join(sorted(PRESETS))}."
        )
    settings = await generic.use_preset(session, settings, preset_key, actor=actor)
    return await get_generic(settings)


@router.post("/generic/test", response_model=GenericStatusOut, summary="Test the provider")
async def test_generic(settings: SettingsDep) -> GenericStatusOut:
    """Make one real call with the saved configuration.

    A failure comes back as a message describing which part went wrong, rather
    than an error status, because that is what makes the form usable.
    """
    return _status_out(await generic.test(settings))


@router.delete(
    "/generic/credentials", response_model=GenericConfigOut, summary="Remove the API key"
)
async def delete_generic_key(
    session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> GenericConfigOut:
    await generic.store_credentials(session, api_key=None, actor=actor)
    return await get_generic(settings)
