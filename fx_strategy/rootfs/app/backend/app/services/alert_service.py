"""Alerts about the app itself rather than about the market.

What is left here after the conversion ladder went is the provider outage
notification: the movement alerts live in :mod:`app.services.fx_alerts`, and
the confirmation rule they share with everything else is in
:mod:`app.services.alert_common`.
"""

from __future__ import annotations

from app.models.alert import AlertRuleType, Severity
from app.services.notifications import Notification


def provider_error_notification(provider: str, error: str, minutes_down: int) -> Notification:
    return Notification(
        rule_type=AlertRuleType.PROVIDER_ERROR,
        title=f"Rate provider {provider} has been failing for {minutes_down} minutes",
        message=(
            f"{error}\n\n"
            "Rate collection has stopped or fallen back to another provider. "
            "Nothing is alerted from a rate the app does not trust."
        ),
        severity=Severity.CRITICAL,
        entity_type="provider",
        entity_id=provider,
    )
