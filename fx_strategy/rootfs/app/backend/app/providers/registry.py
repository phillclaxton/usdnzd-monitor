"""Building providers from settings.

The rest of the application asks for "the primary provider" or "the fallback
chain" and gets objects satisfying :class:`~app.providers.base.FxRateProvider`.
It never constructs a vendor client itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.logging_setup import get_logger
from app.providers.base import FxRateProvider, ProviderConfigurationError
from app.providers.generic import PRESETS, GenericProvider
from app.providers.manual import ManualProvider
from app.providers.wise import WiseProvider
from app.schemas.settings import Settings
from app.security.secrets import SecretError, SecretStore, get_secret_store

log = get_logger(__name__)

MANUAL = "manual"
SIMULATION = "simulation"
WISE = "wise"
GENERIC = "generic"

#: Every provider name the settings may refer to.
PROVIDER_NAMES: tuple[str, ...] = (MANUAL, SIMULATION, WISE, GENERIC)


@dataclass(frozen=True, slots=True)
class ProviderDescription:
    name: str
    display_name: str
    configured: bool
    supports_history: bool
    reason: str = ""


class ProviderRegistry:
    """Creates and caches provider instances for a settings document."""

    def __init__(
        self,
        settings: Settings,
        *,
        manual_rate: Decimal | None = None,
        manual_entered_at: object = None,
        secret_store: SecretStore | None = None,
    ) -> None:
        self._settings = settings
        self._manual_rate = manual_rate
        self._manual_entered_at = manual_entered_at
        self._secrets = secret_store or get_secret_store()
        self._instances: dict[str, FxRateProvider] = {}

    # -- construction -----------------------------------------------------

    def _secret(self, key: str) -> str | None:
        try:
            return self._secrets.get(key)
        except SecretError as exc:
            log.error("secret_unavailable", key=key, error=str(exc))
            return None

    def create(self, name: str) -> FxRateProvider:
        """Build a provider by name, raising if it is not usable."""
        if name in self._instances:
            return self._instances[name]

        provider: FxRateProvider
        if name in (MANUAL, SIMULATION):
            provider = ManualProvider(
                rate=(
                    self._settings.simulation.simulated_rate
                    if name == SIMULATION
                    else self._manual_rate
                ),
                entered_at=self._manual_entered_at,  # type: ignore[arg-type]
                simulated=name == SIMULATION,
            )
        elif name == WISE:
            if not self._settings.providers.wise.enabled:
                raise ProviderConfigurationError(WISE, "The Wise provider is not enabled.")
            provider = WiseProvider(self._settings.providers.wise, self._secret("wise_api_token"))
        elif name == GENERIC:
            generic = self._settings.providers.generic
            if not generic.enabled:
                raise ProviderConfigurationError(
                    GENERIC, "The generic API provider is not enabled."
                )
            provider = GenericProvider(generic, self._secret("generic_api_key"))
        else:
            raise ProviderConfigurationError(name, f"unknown rate provider {name!r}")

        self._instances[name] = provider
        return provider

    # -- selection --------------------------------------------------------

    def chain(self) -> list[str]:
        """The provider names to try, in order, ending with the manual fallback.

        Simulation, when enabled, replaces the chain entirely so simulated data
        can never be mixed with live provider data by accident.
        """
        if self._settings.simulation.enabled:
            return [SIMULATION]

        providers = self._settings.providers
        names: list[str] = [providers.primary]
        if providers.secondary and providers.secondary not in names:
            names.append(providers.secondary)
        if providers.manual_fallback and MANUAL not in names:
            names.append(MANUAL)
        return names

    def comparison_pair(self) -> list[str]:
        """The providers compared for disagreement: primary and secondary."""
        providers = self._settings.providers
        names = [providers.primary]
        if providers.secondary and providers.secondary != providers.primary:
            names.append(providers.secondary)
        return names

    def describe(self) -> list[ProviderDescription]:
        """Report which providers are usable, for the settings and diagnostics."""
        descriptions: list[ProviderDescription] = []
        for name in PROVIDER_NAMES:
            try:
                provider = self.create(name)
            except Exception as exc:
                # Deliberately broad. This is a reporting call on the polling
                # path, so an adapter that raises something unexpected must be
                # described as unusable, not stop the refresh. The reason is
                # shown in the settings panel either way.
                reason = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
                if not isinstance(exc, ProviderConfigurationError):
                    log.warning("provider_describe_failed", provider=name, error=reason)
                descriptions.append(
                    ProviderDescription(
                        name=name,
                        display_name=name.replace("_", " ").title(),
                        configured=False,
                        supports_history=False,
                        reason=str(reason),
                    )
                )
                continue
            # A provider can be constructible and still have nothing to work
            # with — the manual one with no rate entered. That is "not
            # configured", not "failing".
            usable = getattr(provider, "configured", True)
            descriptions.append(
                ProviderDescription(
                    name=name,
                    display_name=provider.display_name,
                    configured=usable,
                    supports_history=provider.supports_history,
                    reason="" if usable else _unconfigured_reason(name),
                )
            )
        return descriptions

    async def aclose(self) -> None:
        for provider in self._instances.values():
            await provider.aclose()
        self._instances.clear()


def _unconfigured_reason(name: str) -> str:
    if name in (MANUAL, SIMULATION):
        return "No manual rate has been entered yet."
    return "Not configured."


def preset_catalogue() -> list[dict[str, object]]:
    """Presets offered in the settings UI."""
    return [
        {
            "key": preset.key,
            "display_name": preset.display_name,
            "base_url": preset.base_url,
            "requires_key": preset.requires_key,
            "notes": preset.notes,
        }
        for preset in PRESETS.values()
    ]
