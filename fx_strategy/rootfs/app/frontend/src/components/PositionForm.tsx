import { useEffect, useMemo, useState } from 'react';

import { Banner, Card, Field } from '@/components/ui';
import { useFxState, useSaveState } from '@/hooks/usePosition';
import { useSettings } from '@/hooks/useSettings';
import { ApiError } from '@/lib/api';
import { compareDecimal, isDecimalString } from '@/lib/decimal';
import type { Position } from '@/types';

/**
 * The position form, on its own so the dashboard can be the place you fill it
 * in.
 *
 * `GET /fx/state` answers 200 with a null position, which makes "nothing saved
 * yet" a state to show rather than an error to report — and the most useful
 * thing to show in it is the form itself, not a link to somewhere else.
 *
 * Every input is `type="text"` with `inputMode="decimal"`, as the rest of the
 * app already does: `type="number"` hands the browser a float, which is exactly
 * the rounding the backend's Decimal pipeline exists to prevent.
 */

interface FormState {
  current_source_balance: string;
  baseline_rate: string;
  baseline_date: string;
  floating_loan_rate: string;
  current_offset_shortfall_nzd: string;
  monthly_nzd_burn: string;
  notes: string;
}

const EMPTY: FormState = {
  current_source_balance: '',
  baseline_rate: '',
  baseline_date: '',
  floating_loan_rate: '',
  current_offset_shortfall_nzd: '',
  monthly_nzd_burn: '',
  notes: '',
};

function formFrom(position: Position): FormState {
  return {
    current_source_balance: position.current_source_balance,
    baseline_rate: position.baseline_rate ?? '',
    baseline_date: position.baseline_date ?? '',
    floating_loan_rate: position.floating_loan_rate ?? '',
    current_offset_shortfall_nzd: position.current_offset_shortfall_nzd ?? '',
    monthly_nzd_burn: position.monthly_nzd_burn ?? '',
    notes: position.notes,
  };
}

/** Empty means "not set", which the API expresses as null rather than zero. */
function orNull(value: string): string | null {
  return value.trim() === '' ? null : value.trim();
}

export default function PositionForm() {
  const settings = useSettings();
  const state = useFxState();
  const save = useSaveState();

  const position = state.data?.position ?? null;
  const [form, setForm] = useState<FormState>(EMPTY);
  const [loaded, setLoaded] = useState(false);

  // Fill the form once, from whatever was stored. Re-filling on every refetch
  // would overwrite whatever the user was in the middle of typing.
  useEffect(() => {
    if (position && !loaded) {
      setForm(formFrom(position));
      setLoaded(true);
    }
  }, [position, loaded]);

  const source = position?.source_currency ?? settings.data?.general.source_currency ?? 'USD';
  const target = position?.target_currency ?? settings.data?.general.target_currency ?? 'NZD';

  const invalid = useMemo(() => {
    const problems: string[] = [];
    const numeric: [string, string][] = [
      ['balance', form.current_source_balance],
      ['baseline rate', form.baseline_rate],
      ['loan rate', form.floating_loan_rate],
      ['offset shortfall', form.current_offset_shortfall_nzd],
      ['monthly spending', form.monthly_nzd_burn],
    ];
    for (const [label, value] of numeric) {
      if (value.trim() !== '' && !isDecimalString(value))
        problems.push(`The ${label} is not a number.`);
    }
    if (form.floating_loan_rate.trim() !== '' && isDecimalString(form.floating_loan_rate)) {
      // 6.04 instead of 0.0604 would overstate the carrying cost a hundredfold
      // and still look plausible. The backend refuses it; saying so here saves
      // a round trip and explains what to type instead.
      if (compareDecimal(form.floating_loan_rate.trim(), '1') > 0) {
        problems.push('The loan rate is a fraction, not a percentage: enter 6.04% as 0.0604.');
      }
    }
    return problems;
  }, [form]);

  const submit = () => {
    save.mutate({
      source_currency: source,
      target_currency: target,
      current_source_balance: form.current_source_balance.trim() || '0',
      baseline_rate: orNull(form.baseline_rate),
      baseline_date: orNull(form.baseline_date),
      floating_loan_rate: orNull(form.floating_loan_rate),
      current_offset_shortfall_nzd: orNull(form.current_offset_shortfall_nzd),
      monthly_nzd_burn: orNull(form.monthly_nzd_burn),
      notes: form.notes,
    });
  };

  return (
    <Card
      title="Your position"
      subtitle="These are facts about your money, not settings. Every change is recorded in the audit trail."
    >
      {save.isError && <Banner tone="error">{(save.error as ApiError).message}</Banner>}
      {save.isSuccess && !save.isPending && <Banner tone="info">Saved.</Banner>}
      {invalid.length > 0 && (
        <Banner tone="warning">
          {invalid.map((problem) => (
            <div key={problem}>{problem}</div>
          ))}
        </Banner>
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Field
          label={`${source} still held`}
          hint="what has not been converted yet"
          htmlFor="balance"
        >
          <input
            id="balance"
            type="text"
            inputMode="decimal"
            required
            placeholder="691536"
            value={form.current_source_balance}
            onChange={(event) => setForm({ ...form, current_source_balance: event.target.value })}
          />
        </Field>

        <Field
          label="Baseline rate"
          hint="the rate you would otherwise have accepted — every improvement figure is measured against it"
          htmlFor="baseline-rate"
        >
          <input
            id="baseline-rate"
            type="text"
            inputMode="decimal"
            placeholder="1.6890"
            value={form.baseline_rate}
            onChange={(event) => setForm({ ...form, baseline_rate: event.target.value })}
          />
        </Field>

        <Field label="Baseline date" hint="optional" htmlFor="baseline-date">
          <input
            id="baseline-date"
            type="date"
            value={form.baseline_date}
            onChange={(event) => setForm({ ...form, baseline_date: event.target.value })}
          />
        </Field>

        <Field
          label="Floating loan rate"
          hint="a fraction, not a percentage: 6.04% is 0.0604"
          htmlFor="loan-rate"
        >
          <input
            id="loan-rate"
            type="text"
            inputMode="decimal"
            placeholder="0.0604"
            value={form.floating_loan_rate}
            onChange={(event) => setForm({ ...form, floating_loan_rate: event.target.value })}
          />
        </Field>

        <Field
          label={`Offset shortfall (${target})`}
          hint="the part of the offset that is still unfunded — the carrying cost is charged on this, never on the whole balance"
          htmlFor="shortfall"
        >
          <input
            id="shortfall"
            type="text"
            inputMode="decimal"
            placeholder="35585"
            value={form.current_offset_shortfall_nzd}
            onChange={(event) =>
              setForm({
                ...form,
                current_offset_shortfall_nzd: event.target.value,
              })
            }
          />
        </Field>

        <Field label={`Monthly spending (${target})`} hint="optional" htmlFor="burn">
          <input
            id="burn"
            type="text"
            inputMode="decimal"
            placeholder="5000"
            value={form.monthly_nzd_burn}
            onChange={(event) => setForm({ ...form, monthly_nzd_burn: event.target.value })}
          />
        </Field>

        <Field label="Notes" htmlFor="position-notes">
          <textarea
            id="position-notes"
            rows={2}
            value={form.notes}
            onChange={(event) => setForm({ ...form, notes: event.target.value })}
          />
        </Field>

        <div className="fx-toolbar" style={{ marginTop: 'var(--fx-gap)' }}>
          <button
            type="submit"
            className="is-primary"
            disabled={save.isPending || invalid.length > 0}
          >
            {save.isPending ? 'Saving…' : 'Save position'}
          </button>
        </div>
      </form>
    </Card>
  );
}
