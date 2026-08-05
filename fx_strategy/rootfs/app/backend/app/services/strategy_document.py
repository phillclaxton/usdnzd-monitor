"""Editing a strategy as a JSON document.

The document is exactly the shape the create and update endpoints already
accept, so what you copy out is what you can paste back in. There is no
separate export format to drift from the real one.

What the document does *not* contain is as important as what it does. It carries
the strategy's **definition** — amounts, ladder, deadlines — and nothing that was
recorded as having happened. Conversions, alert history and audit entries are
facts, not settings, and pasting a document never touches them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.models.strategy import Strategy
from app.money import decimal_to_str
from app.schemas.strategy import StrategyIn

#: Fields the document deliberately omits, with the reason. Shown to the user
#: rather than left to be discovered.
OMITTED: dict[str, str] = {
    "id": "assigned by the app",
    "status": "changed with the activate, pause and complete actions",
    "conversions": "records of what happened, not settings — never altered by an edit",
    "tranche status": "derived from the rate and from recorded conversions",
    "created_at / updated_at": "maintained by the app",
}

#: Scalar keys, in the order the document emits them.
SCALAR_KEYS: tuple[str, ...] = (
    "name",
    "source_currency",
    "target_currency",
    "initial_source_amount",
    "funds_available_amount",
    "funds_arrival_date",
    "strategy_start_date",
    "final_deadline",
    "minimum_acceptable_rate",
    "walk_away_rate",
    "require_targets_in_order",
    "fee_model_id",
    "rate_provider_id",
    "timezone",
    "notes",
)

#: Per-tranche keys compared when reporting changes.
TRANCHE_KEYS: tuple[str, ...] = (
    "name",
    "allocation_type",
    "allocation_value",
    "target_rate",
    "minimum_rate",
    "deadline",
    "intended_for_auto_conversion",
    "notifications_enabled",
    "wise_auto_conversion_reference",
)


class DocumentError(ValueError):
    """The pasted text is not a usable strategy document."""

    def __init__(self, message: str, problems: list[Problem] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.problems = problems or []


@dataclass(frozen=True, slots=True)
class Problem:
    """One thing wrong, and where."""

    #: Dotted path into the document, e.g. ``tranches[2].target_rate``.
    path: str
    message: str
    #: Set for a syntax error, where a field path does not exist yet.
    line: int | None = None
    column: int | None = None


@dataclass(slots=True)
class Change:
    """One difference the document would make."""

    path: str
    before: str
    after: str


@dataclass(slots=True)
class Preview:
    """What applying the document would do."""

    valid: bool
    problems: list[Problem] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    #: Consequences worth stating before the user commits.
    warnings: list[str] = field(default_factory=list)
    tranches_added: list[int] = field(default_factory=list)
    tranches_removed: list[int] = field(default_factory=list)
    tranches_retargeted: list[int] = field(default_factory=list)
    conversions_preserved: int = 0


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _money(value: Decimal | None) -> str | None:
    return decimal_to_str(value) if value is not None else None


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def to_document(strategy: Strategy | StrategyIn) -> dict[str, Any]:
    """The strategy as the JSON a user can edit and paste back.

    Accepts either a stored strategy or a parsed one. Rendering both through the
    same function is what lets the preview compare them without a formatting
    difference masquerading as an edit.

    Keys are emitted in a stable order so a copy, an edit and a re-copy produce
    a readable diff rather than a reshuffle.
    """
    return {
        "name": strategy.name,
        "source_currency": strategy.source_currency,
        "target_currency": strategy.target_currency,
        "initial_source_amount": _money(strategy.initial_source_amount),
        "funds_available_amount": _money(strategy.funds_available_amount),
        "funds_arrival_date": _iso(strategy.funds_arrival_date),
        "strategy_start_date": _iso(strategy.strategy_start_date),
        "final_deadline": _iso(strategy.final_deadline),
        "minimum_acceptable_rate": _money(strategy.minimum_acceptable_rate),
        "walk_away_rate": _money(strategy.walk_away_rate),
        "require_targets_in_order": strategy.require_targets_in_order,
        "fee_model_id": strategy.fee_model_id,
        "rate_provider_id": strategy.rate_provider_id,
        "timezone": strategy.timezone,
        "notes": strategy.notes,
        "tranches": [_tranche_document(tranche) for tranche in _by_sequence(strategy.tranches)],
        "requirements": [
            _requirement_document(requirement)
            for requirement in _by_due_date(strategy.requirements)
        ],
    }


def _by_sequence(tranches: Any) -> list[Any]:
    return sorted(tranches, key=lambda item: item.sequence)


def _by_due_date(requirements: Any) -> list[Any]:
    return sorted(requirements, key=lambda item: item.due_date)


def _requirement_document(requirement: Any) -> dict[str, Any]:
    return {
        "due_date": _iso(requirement.due_date),
        "required_source_amount": _money(requirement.required_source_amount),
        "required_percentage": _money(requirement.required_percentage),
        "description": requirement.description,
    }


def _tranche_document(tranche: Any) -> dict[str, Any]:
    return {
        "sequence": tranche.sequence,
        "name": tranche.name,
        "allocation_type": tranche.allocation_type,
        "allocation_value": _money(tranche.allocation_value),
        "target_rate": _money(tranche.target_rate),
        "minimum_rate": _money(tranche.minimum_rate),
        "deadline": _iso(tranche.deadline),
        "intended_for_auto_conversion": tranche.intended_for_auto_conversion,
        "notifications_enabled": tranche.notifications_enabled,
        "wise_auto_conversion_reference": tranche.wise_auto_conversion_reference,
    }


def to_json(strategy: Strategy | StrategyIn) -> str:
    """The document as indented text, ready to show in an editor."""
    return json.dumps(to_document(strategy), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------


def parse(text: str) -> StrategyIn:
    """Read pasted text into a validated strategy definition.

    Every failure names where it happened: a syntax error gives a line and
    column, a validation failure gives the field path. "Invalid JSON" on its own
    is useless when the document is a hundred lines long.
    """
    stripped = text.strip()
    if not stripped:
        raise DocumentError("The document is empty.")

    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise DocumentError(
            f"This is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.",
            [Problem(path="", message=exc.msg, line=exc.lineno, column=exc.colno)],
        ) from exc

    if not isinstance(raw, dict):
        raise DocumentError(
            f"A strategy document must be a JSON object, but this is a {type(raw).__name__}."
        )

    try:
        return StrategyIn.model_validate(raw)
    except ValidationError as exc:
        problems = [
            Problem(path=_path(error["loc"]), message=_readable(error)) for error in exc.errors()
        ]
        count = len(problems)
        raise DocumentError(
            f"The document has {count} problem{'s' if count != 1 else ''}.", problems
        ) from exc


def _path(location: tuple[Any, ...]) -> str:
    """Render a pydantic location as a path a user can find in their text."""
    out = ""
    for part in location:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out = f"{out}.{part}" if out else str(part)
    return out


def _readable(error: Any) -> str:
    """Pydantic's message, with its noisier phrasings replaced."""
    message = str(error.get("msg", "is not valid"))
    if error.get("type") == "extra_forbidden":
        return "Unknown field. Check the spelling, or remove it."
    if error.get("type") == "missing":
        return "This field is required."
    return message.removeprefix("Value error, ")


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def preview(strategy: Strategy | None, text: str) -> Preview:
    """Say what applying the document would change, without changing anything.

    A pasted ladder can silently reset alert state or drop a tranche that has
    conversions against it. Those consequences are reported before the user
    commits, not discovered afterwards.
    """
    try:
        payload = parse(text)
    except DocumentError as exc:
        return Preview(valid=False, problems=exc.problems or [Problem("", exc.message)])

    result = Preview(valid=True)
    if strategy is None:
        result.changes.append(Change("name", "(new strategy)", payload.name))
        return result

    current = to_document(strategy)
    proposed = to_document(payload)

    for key in SCALAR_KEYS:
        before, after = current.get(key), proposed.get(key)
        if key == "strategy_start_date" and after is None:
            # Omitting the start date keeps the one the strategy already has,
            # so an absent value here is not a change to report.
            continue
        if _differs(before, after):
            result.changes.append(Change(key, _render(before), _render(after)))

    existing = {tranche.sequence: tranche for tranche in strategy.tranches}
    wanted = {item.sequence: item for item in payload.tranches}

    result.tranches_added = sorted(set(wanted) - set(existing))
    result.tranches_removed = sorted(set(existing) - set(wanted))
    result.tranches_retargeted = sorted(
        sequence
        for sequence, item in wanted.items()
        if sequence in existing and existing[sequence].target_rate != item.target_rate
    )

    for sequence in sorted(set(wanted) & set(existing)):
        before_tranche = _tranche_document(existing[sequence])
        after_tranche = _tranche_document(wanted[sequence])
        for key in TRANCHE_KEYS:
            if _differs(before_tranche[key], after_tranche[key]):
                result.changes.append(
                    Change(
                        f"tranches[sequence={sequence}].{key}",
                        _render(before_tranche[key]),
                        _render(after_tranche[key]),
                    )
                )

    for sequence in result.tranches_added:
        result.changes.append(Change(f"tranches[sequence={sequence}]", "not present", "added"))
    for sequence in result.tranches_removed:
        result.changes.append(Change(f"tranches[sequence={sequence}]", "present", "removed"))

    if current["requirements"] != proposed["requirements"]:
        result.changes.append(
            Change(
                "requirements",
                f"{len(current['requirements'])} dated requirement(s)",
                f"{len(proposed['requirements'])} dated requirement(s)",
            )
        )

    for sequence in result.tranches_removed:
        converted = sum(
            1
            for conversion in strategy.conversions
            if conversion.tranche_id == existing[sequence].id
        )
        if converted:
            result.warnings.append(
                f"Tranche {sequence} is being removed and has {converted} recorded "
                "conversion(s). The conversions are kept and stay in the totals, but they "
                "will no longer be attributed to a tranche."
            )

    if result.tranches_retargeted:
        result.warnings.append(
            "Target rates changed on tranche(s) "
            + ", ".join(str(s) for s in result.tranches_retargeted)
            + ". Their reached-and-notified state resets, so a target already passed will "
            "alert again once it is reached at the new level."
        )

    result.conversions_preserved = len(strategy.conversions)
    if result.conversions_preserved:
        result.warnings.append(
            f"{result.conversions_preserved} recorded conversion(s) are untouched by this "
            "edit. The document describes the plan, not what has happened."
        )

    return result


def _differs(before: Any, after: Any) -> bool:
    if before is None or after is None:
        return before is not after
    if isinstance(before, str) and isinstance(after, str):
        # "1.7200" and "1.72" mean the same rate; compare numerically where both
        # sides look numeric so formatting alone is not reported as a change.
        try:
            return Decimal(before) != Decimal(after)
        except ArithmeticError:
            return before != after
    return bool(before != after)


def _render(value: Any) -> str:
    if value is None:
        return "not set"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)
