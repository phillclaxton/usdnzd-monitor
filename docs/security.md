# Security model

## What is trusted

| Party | Trust | Why |
| --- | --- | --- |
| Home Assistant Ingress | Trusted to authenticate | Requests reach this app only after Home Assistant has authenticated the user. There is no second login. |
| The local network | Not trusted with an open port | The app binds inside the container and exposes no port. There is no listener to reach. |
| Rate providers and Wise | Not trusted | Every response is validated defensively. A malformed payload produces an error, never a fabricated rate. |
| A CSV or backup a user uploads | Not trusted | Parsed with size limits, previewed before writing, and rejected with per-row reasons. |
| Another browser tab | Not trusted | State-changing requests with a mismatched `Origin` are refused. |

## Boundaries

- No external port. `config.yaml` declares no `ports:` mapping.
- No host networking, no privileged mode, no Docker socket.
- The only mapped directory is `app_config` (`/config`), plus the add-on's own
  `/data`. No access to unrelated Home Assistant directories.
- A strict Content Security Policy: `default-src 'self'`, `connect-src 'self'`.
  There is no CDN, no external font, no remote script. A test asserts the policy
  contains no `https://` host.
- Secure response headers on every response: `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: same-origin`, `X-Frame-Options: SAMEORIGIN`, a restrictive
  `Permissions-Policy`, and `Cross-Origin-Opener-Policy: same-origin`.
- A fixed-window rate limit on endpoints that reach a third party or touch
  credentials, protecting the upstream quota and slowing a runaway automation.

## Credentials

Stored in `/data/secrets.json`, mode `0600`, encrypted with a Fernet key held in
a separate file (`/data/secret.key`, also `0600`).

Splitting the key from the ciphertext does not defend against an attacker who
already has root on the host — nothing at this layer could. What it buys is that
a leaked backup archive, a copied database, or a diagnostics bundle does not on
its own hand over a working token.

Credentials never appear in:

| Surface | Mechanism | Test |
| --- | --- | --- |
| API responses | Only a masked hint (`••••••••3456`) is ever returned | `test_the_token_never_appears_in_an_api_response` |
| Logs | A structlog processor redacts sensitive keys and credential-shaped substrings from every record | `logging_setup.scrub_processor` |
| The audit trail | Events record *that* a credential changed, never its value | `test_storing_and_removing_a_token_is_audited_without_the_value` |
| Diagnostics | Only a `configured` flag; account identifiers are masked | `test_diagnostics_report_state_without_credentials` |
| Backups | Excluded entirely; `contains_secrets` is `false` | `test_a_backup_contains_the_data_but_never_a_credential` |
| The end-to-end bundle | Asserted absent from the whole downloaded file | `no credential appears anywhere in the diagnostics bundle` |

TLS verification is on and there is no configuration switch to disable it.

## Financial safety

- Every monetary value is a `Decimal`. Storage round-trips exactly.
- NaN and infinity are rejected at the boundary; so are non-finite floats.
- Currency codes come from an allow-list.
- Conversion amounts must be positive, and cannot exceed the unconverted balance
  unless the user explicitly says they are correcting an earlier record.
- Repeated provider transaction IDs are refused, so reconciliation and CSV
  re-import cannot double-count.
- All queries go through SQLAlchemy's parameter binding. No SQL is built by
  string concatenation.
- Uploads are capped (8 MB for CSV, 64 MB for a restore) and read with a limit
  rather than into unbounded memory.

## Money movement

There is none. The application has no code path that converts or transfers
funds:

- `services/execution.py` contains the interface a future module would
  implement, the eleven conditions it would have to satisfy, and
  `DisabledExecutor`, whose two methods raise. An execution attempt is logged at
  error level.
- `POST /api/v1/wise/execute` exists solely to return an explicit refusal, so
  probing for it does not produce a 404 that reads as "not built yet".
- A structural test walks the entire route table and asserts no conversion or
  transfer endpoint exists.

## Reporting a problem

Open an issue at <https://github.com/phillclaxton/usdnzd-monitor/issues>. Please
do not attach a diagnostics bundle without reading it first — it is designed to
be safe to share, but it is your data.
