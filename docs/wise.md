# Wise API setup

The Wise integration is **read-only**. It reads rates, balances and completed
conversions. It never converts or transfers anything.

## Getting a token

1. Sign in to Wise on the web.
2. **Settings → API tokens**.
3. Create a **read-only** token. This app needs nothing more.
4. Copy it once — Wise does not show it again.

## Entering it

**Settings → Wise**:

| Field | Needed for |
| --- | --- |
| API token | Everything |
| Profile ID | Balances, conversions, authenticated quotes |
| Source balance ID | Reading completed conversions |

Save, then **Test connection**. The result reports which call failed, not just
"failed" — a token that works for rates but not for profiles is a different
problem from a rejected token.

The token is encrypted and stored in `/data/secrets.json` (mode 0600) with its
key in a separate file. It never appears in the API, the logs, the audit trail,
a diagnostics bundle or a backup.

## Authentication note

Personal API tokens use `Authorization: Bearer <token>`, which is what this app
sends. Affiliate integrations authenticate with Basic client credentials
instead. If your account is the affiliate type, the connection test will report
the rejection rather than silently returning nothing.

## What the app reads

| Feature | Endpoint |
| --- | --- |
| Current rate | `GET /v1/rates?source=&target=` |
| Historical rates | `GET /v1/rates` with `from`, `to`, `group` |
| Profiles | `GET /v2/profiles` |
| Balances | `GET /v4/profiles/{id}/balances?types=STANDARD` |
| Completed conversions | `GET /v1/profiles/{id}/balance-statements/{balance}/statement.json` |
| Quotes | `POST /v3/profiles/{id}/quotes` (or `/v3/quotes` unauthenticated) |

## Rates, quotes and what they mean

The app distinguishes these everywhere, because conflating them is how people
end up surprised by what arrives:

| Kind | What it is |
| --- | --- |
| Mid-market reference | What `/v1/rates` returns. Not what a transfer settles at. |
| Provider displayed | What Wise shows in its interface. |
| Guaranteed quote | A specific quote with a fee and an expiry. |
| Estimated fee | Calculated from your fee model, not from Wise. |
| Actual net | What you recorded from your statement. |

An unauthenticated quote is never described as executable. Neither is an
authenticated one — this application will not act on either.

## Auto Conversions

Wise's scheduled conversions are created **in Wise**, not through this API.

The workflow is:

1. Define your ladder here.
2. The app shows the instructions: amount and target rate per tranche.
3. You create the matching Auto Conversions in Wise.
4. The app watches the rate and tells you when a target is reached.
5. Wise performs the conversion.
6. You record it here, or reconcile from the API.

## Reconciliation

**Settings → Wise → Reconcile** compares Wise's completed conversions with what
is recorded here. It defaults to a preview.

Matching is on the Wise reference, so running it twice imports nothing twice.
Conversions for a different currency pair are skipped and counted separately.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| "The credential was rejected" | Wrong token, or an affiliate account needing Basic auth. |
| "The credential is not permitted" | The token lacks the scope; a read-only token is enough for everything here. |
| "A source balance ID is required" | Set it in Settings; find it in the balances table. |
| Reconciliation finds nothing | Check the date window (default 90 days) and that the balance ID is the source balance. |
