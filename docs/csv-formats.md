# CSV formats

Both importers **preview by default**. You see what would be imported, and what
would be rejected with the reason for each row, before anything is written.

Comma, semicolon and tab delimiters are detected automatically. Files must be
UTF-8 (a byte-order mark is fine) and under 8 MB.

## Rate history

Required columns:

```csv
timestamp,source_currency,target_currency,rate,provider
2026-07-01T00:00:00Z,USD,NZD,1.7100,csv
2026-07-02T09:30:00+12:00,USD,NZD,1.7200,csv
```

| Column | Notes |
| --- | --- |
| `timestamp` | ISO-8601, epoch seconds or milliseconds, or a plain date. Converted to UTC. |
| `source_currency` / `target_currency` | Must match your strategy's pair; other rows are skipped and counted. |
| `rate` | Target per 1 source. Must be positive. |
| `provider` | Free text, up to 32 characters. |

Import: **Settings → Rate providers**, or `POST /api/v1/rates/import`.
De-duplication is on (provider, timestamp), so re-importing the same file adds
nothing.

Export from the chart page produces exactly this format.

## Conversion history

Required:

```csv
executed_at,source_amount,target_amount
2026-09-15T10:30:00Z,120000,207840
```

Optional: `gross_rate`, `effective_rate`, `fee_source`, `fee_target`,
`provider`, `transaction_id`, `tranche`, `notes`.

```csv
executed_at,source_amount,target_amount,transaction_id,tranche,notes
2026-09-15T10:30:00Z,120000,207840,WISE-1,1,Auto conversion
2026-09-20T09:00:00Z,160000,278400,WISE-2,2,
```

| Column | Notes |
| --- | --- |
| `executed_at` | When it happened. Any common format; converted to UTC. |
| `source_amount` / `target_amount` | Both required, both positive. `target_amount` is what actually arrived. |
| `gross_rate` | Optional. Derived from the amounts if omitted. |
| `fee_source` / `fee_target` | Fee on each side. Omit both and the fee is recorded as unknown — not as zero. |
| `transaction_id` | Strongly recommended: it is what prevents double-counting. |
| `tranche` | A tranche ID or a sequence number. Unresolvable values import the row unassigned, with a warning. |

Import: **Conversions → Import from CSV**, or
`POST /api/v1/conversions/import?strategy_id=…`.

A row whose `transaction_id` already exists is **skipped and counted as a
duplicate**, not imported again — so re-running an import is safe.

Imported rows are marked `record_source: csv_import` and are allowed to exceed
the current remaining balance, because history may pre-date it.

## Exports

| Export | Contents |
| --- | --- |
| Rate history | The visible chart range, in the importable format |
| Conversions | Every recorded conversion with rates and fees |
| Backup (JSON) | Everything except credentials |

Both CSV exports round-trip through their own importer, and a test asserts it.

## Common rejections

| Message | Meaning |
| --- | --- |
| `Missing required column: …` | The header row is wrong. Check the spelling; matching is case-insensitive. |
| `Unreadable timestamp '…'` | The format could not be parsed. Prefer ISO-8601. |
| `Rate must be positive` | A zero or negative rate. |
| `… is not a supported currency code` | Outside the allow-list. |
| `… is already recorded and was skipped` | Duplicate transaction ID. Working as intended. |
| `… rows are for a different currency pair` | The file mixes pairs; only your strategy's pair is imported. |
