"""Rules shared by every kind of alert.

Extracted from :mod:`app.services.alert_service`, which had them typed to the
tranche state row. The movement alerts need the same rule against a different
row, and two copies of "have we seen this long enough to believe it?" would
drift apart the first time one of them was tuned.

Plain arguments rather than a state object, so neither caller has to own the
other's schema.
"""

from __future__ import annotations

from datetime import datetime


def confirmation_passed(
    *,
    qualifying_samples: int,
    first_qualifying_at: datetime | None,
    sample_at: datetime,
    required_samples: int,
    minimum_seconds: int,
) -> bool:
    """Has the condition held for long enough to be believed?

    Consecutive qualifying samples, far enough apart in time. One sample can be
    a provider hiccup; two, thirty seconds apart, is the market.

    ``sample_at`` is when the observation was *made*, never when this code ran,
    so a replayed series is judged by the same rule as a live one.
    """
    if qualifying_samples < required_samples:
        return False
    if required_samples <= 1:
        return True
    if first_qualifying_at is None:
        return False
    gap = (sample_at - first_qualifying_at).total_seconds()
    return gap >= minimum_seconds
