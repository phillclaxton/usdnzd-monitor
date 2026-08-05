# Editing a strategy as JSON

Some edits are faster to paste than to click. **Strategy → Edit as JSON** shows
the whole plan as one document you can copy, change and save.

The document is exactly the shape the API accepts. There is no separate export
format that could drift from the real one: what you copy out of one
installation is a valid request body for another.

## What is in it, and what is not

The document carries the **plan**: amounts, the ladder, deadlines, rates and
dated requirements.

It deliberately leaves out anything that was recorded as having happened:

| Left out | Why |
| --- | --- |
| `id` | Assigned by the app |
| `status` | Changed with the activate, pause and complete actions |
| `conversions` | Records of what happened, not settings |
| Tranche `status` | Derived from the rate and from recorded conversions |
| `created_at` / `updated_at` | Maintained by the app |

**Saving a document never alters a conversion, an alert record or an audit
entry.** Those are facts; the document is a plan.

## The shape

```json
{
  "name": "USD to NZD",
  "source_currency": "USD",
  "target_currency": "NZD",
  "initial_source_amount": "800000.0000",
  "funds_available_amount": "800000.0000",
  "funds_arrival_date": null,
  "strategy_start_date": "2026-08-01T00:00:00+00:00",
  "final_deadline": "2026-12-01T00:00:00+00:00",
  "minimum_acceptable_rate": "1.70000000",
  "walk_away_rate": "1.78000000",
  "require_targets_in_order": false,
  "fee_model_id": null,
  "rate_provider_id": null,
  "timezone": "Pacific/Auckland",
  "notes": "",
  "tranches": [
    {
      "sequence": 1,
      "name": "Tranche 1",
      "allocation_type": "percentage",
      "allocation_value": "15.0000",
      "target_rate": "1.72000000",
      "minimum_rate": null,
      "deadline": null,
      "intended_for_auto_conversion": true,
      "notifications_enabled": true,
      "wise_auto_conversion_reference": null
    }
  ],
  "requirements": []
}
```

Money and rates are **strings**, never JSON numbers. A JSON number is a binary
float, and `1.72` does not survive that trip intact. `"1.72"` and `"1.7200"`
mean the same thing and neither is reported as a change.

Omitting an optional field leaves it at its default; writing `null` clears it.
The one exception is `strategy_start_date`, which keeps its existing value when
omitted — it records when the strategy began, and blanking it by accident would
be silent.

## Tranche identity

Tranches are matched by `sequence`, not by position in the list. That is what
lets recorded conversions and per-tranche alert state survive an edit:

- A sequence in the document but not in the strategy is **added**.
- A sequence in the strategy but not in the document is **removed**. Any
  conversions recorded against it are kept and stay in the totals; they simply
  stop being attributed to a tranche.
- Changing a `target_rate` **resets that tranche's reached-and-notified state**,
  so a target that was already passed will alert again once it is reached at the
  new level.

All three are reported before you save.

## What the editor tells you

As you type, the text is checked against the server and the panel shows:

- **Problems**, each with the field path (`tranches[2].target_rate`) or, for a
  syntax error, the line and column. "Invalid JSON" on its own is useless when
  the document is a hundred lines long.
- **Changes**, field by field, with the value now and the value after saving.
- **Warnings** — the consequences above, plus how many recorded conversions are
  untouched by the edit.

Save is refused while the document has problems. An unchanged document says so
rather than pretending there is something to apply.

The buttons are: **Save**, **Copy** (falls back to selecting the text where the
clipboard API is unavailable, which is common on a plain-http LAN),
**Tidy formatting**, and **Discard changes**.

## Copying a strategy between installations

1. Open **Strategy → Edit as JSON** on the source install and press **Copy**.
2. On the target install, open the same panel and paste over everything.
3. Check the change summary, then press **Create from JSON** or **Save JSON**.

`fee_model_id` and `rate_provider_id` refer to records on the installation that
produced the document. Set them to `null` if the target install does not have
the same ones.

## Field and JSON editing together

**The two views are two renderings of one strategy.** Switching between them
converts what is on screen; it does not reload from anywhere. Unsaved work
survives the switch in both directions:

- Change a field, switch to JSON, and the change is in the document.
- Edit the document, switch back, and the fields show it.

If the JSON cannot be read as an object, the switch to fields is **refused**
with the reason. Showing you fields that no longer match the document you are
looking at would be worse than staying put.

Saving from either view updates the other. A save from the JSON view refreshes
the fields from what the server actually stored, so the two never drift.

The field editor carries every part of the strategy, including the parts it has
no input for — dated requirements, per-tranche minimum rates and deadlines, the
rate provider. Both views replace the whole definition when they save, so
anything either one failed to carry would be silently deleted on the next save.

If the strategy is changed somewhere else while you have unsaved edits, the page
says so and leaves your edits alone until you choose to reload.

## An archived strategy

An archived strategy refuses a pasted document, exactly as it refuses a field
edit. Duplicate it instead.
