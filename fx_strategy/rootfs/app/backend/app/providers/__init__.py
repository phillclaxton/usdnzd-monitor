"""Exchange-rate providers.

Only this package knows about specific vendors.
"""

from app.providers.base import (
    FxRateProvider,
    ProviderConfigurationError,
    ProviderError,
    ProviderHealth,
    ProviderResponseError,
    ProviderUnavailableError,
    QuoteType,
    RatePoint,
    RateQuote,
)
from app.providers.registry import ProviderRegistry

__all__ = [
    "FxRateProvider",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderHealth",
    "ProviderRegistry",
    "ProviderResponseError",
    "ProviderUnavailableError",
    "QuoteType",
    "RatePoint",
    "RateQuote",
]
