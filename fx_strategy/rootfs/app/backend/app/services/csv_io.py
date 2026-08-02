"""CSV import and export.

Imports are always previewed before they are committed: the parser returns the
rows it understood *and* the rows it rejected, with a reason for each, so a
malformed file produces a report rather than a partial import.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.money import MoneyError, quantize_money, quantize_rate, require_currency
from app.providers.generic import parse_provider_timestamp

#: Hard cap on an uploaded file, matching the API's upload limit.
MAX_ROWS = 200_000

RATE_REQUIRED_COLUMNS = ("timestamp", "source_currency", "target_currency", "rate", "provider")
CONVERSION_REQUIRED_COLUMNS = ("executed_at", "source_amount", "target_amount")
CONVERSION_OPTIONAL_COLUMNS = (
    "gross_rate",
    "effective_rate",
    "fee_source",
    "fee_target",
    "provider",
    "transaction_id",
    "tranche",
    "notes",
)


@dataclass(slots=True)
class RowError:
    row_number: int
    message: str
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedRates:
    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    total_rows: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class ParsedConversions:
    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    total_rows: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


class CsvFormatError(ValueError):
    """The file could not be read as CSV at all."""


def _reader(content: str) -> csv.DictReader[str]:
    if not content.strip():
        raise CsvFormatError("The file is empty.")
    # Sniffing handles comma, semicolon and tab exports without asking the user.
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    if not reader.fieldnames:
        raise CsvFormatError("The file has no header row.")
    reader.fieldnames = [(name or "").strip().lower() for name in reader.fieldnames]
    return reader


def _require_columns(reader: csv.DictReader[str], required: Sequence[str]) -> None:
    present = set(reader.fieldnames or [])
    missing = [column for column in required if column not in present]
    if missing:
        raise CsvFormatError(
            f"Missing required column{'s' if len(missing) > 1 else ''}: {', '.join(missing)}. "
            f"Expected: {', '.join(required)}."
        )


def parse_rate_csv(content: str) -> ParsedRates:
    """Parse a rate history file.

    Required columns: ``timestamp,source_currency,target_currency,rate,provider``.
    Timestamps in any common format are accepted and converted to UTC.
    """
    reader = _reader(content)
    _require_columns(reader, RATE_REQUIRED_COLUMNS)
    result = ParsedRates()

    for index, raw in enumerate(reader, start=2):
        result.total_rows += 1
        if result.total_rows > MAX_ROWS:
            result.errors.append(RowError(index, f"File exceeds the {MAX_ROWS:,} row limit."))
            break
        row = {key: (value or "").strip() for key, value in raw.items() if key}

        timestamp = parse_provider_timestamp(row.get("timestamp", ""))
        if timestamp is None:
            result.errors.append(
                RowError(index, f"Unreadable timestamp {row.get('timestamp', '')!r}", row)
            )
            continue
        try:
            source = require_currency(row.get("source_currency", ""), field="source_currency")
            target = require_currency(row.get("target_currency", ""), field="target_currency")
            rate = quantize_rate(row.get("rate", ""), field="rate")
        except MoneyError as exc:
            result.errors.append(RowError(index, str(exc), row))
            continue
        if rate <= 0:
            result.errors.append(RowError(index, f"Rate must be positive, got {rate}", row))
            continue
        provider = row.get("provider") or "csv_import"

        result.rows.append(
            {
                "timestamp": timestamp.astimezone(UTC),
                "source_currency": source,
                "target_currency": target,
                "rate": rate,
                "provider": provider[:32],
            }
        )
    return result


def parse_conversion_csv(content: str) -> ParsedConversions:
    """Parse a conversion history file.

    Required: ``executed_at,source_amount,target_amount``. Everything else is
    optional and filled in from the required fields where it can be derived.
    """
    reader = _reader(content)
    _require_columns(reader, CONVERSION_REQUIRED_COLUMNS)
    result = ParsedConversions()

    for index, raw in enumerate(reader, start=2):
        result.total_rows += 1
        if result.total_rows > MAX_ROWS:
            result.errors.append(RowError(index, f"File exceeds the {MAX_ROWS:,} row limit."))
            break
        row = {key: (value or "").strip() for key, value in raw.items() if key}

        executed_at = parse_provider_timestamp(row.get("executed_at", ""))
        if executed_at is None:
            result.errors.append(
                RowError(index, f"Unreadable executed_at {row.get('executed_at', '')!r}", row)
            )
            continue
        try:
            source_amount = quantize_money(row.get("source_amount", ""), field="source_amount")
            target_amount = quantize_money(row.get("target_amount", ""), field="target_amount")
        except MoneyError as exc:
            result.errors.append(RowError(index, str(exc), row))
            continue
        if source_amount <= 0 or target_amount <= 0:
            result.errors.append(
                RowError(index, "Conversion amounts must be greater than zero.", row)
            )
            continue

        parsed: dict[str, Any] = {
            "executed_at": executed_at.astimezone(UTC),
            "source_amount": source_amount,
            "target_amount": target_amount,
            "provider": row.get("provider") or "csv_import",
            "provider_transaction_id": row.get("transaction_id") or None,
            "notes": row.get("notes", ""),
            "tranche_reference": row.get("tranche") or None,
        }
        try:
            for field_name, target_key in (
                ("gross_rate", "gross_rate"),
                ("effective_rate", "effective_rate"),
            ):
                if row.get(field_name):
                    parsed[target_key] = quantize_rate(row[field_name], field=field_name)
            for field_name, target_key in (
                ("fee_source", "fee_source_currency"),
                ("fee_target", "fee_target_currency"),
            ):
                if row.get(field_name):
                    parsed[target_key] = quantize_money(row[field_name], field=field_name)
        except MoneyError as exc:
            result.errors.append(RowError(index, str(exc), row))
            continue

        result.rows.append(parsed)
    return result


def write_csv(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """Render rows as CSV text, with Decimals kept exact."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return buffer.getvalue()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)
