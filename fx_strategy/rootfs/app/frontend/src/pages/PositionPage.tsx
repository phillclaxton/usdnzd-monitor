import { useEffect, useMemo, useState } from 'react';

import { Banner, Card, Field, Loading, Stat, Tag } from '@/components/ui';
import { useFxState, useSaveState } from '@/hooks/usePosition';
import { useCurrentRate } from '@/hooks/useRates';
import { useSettings } from '@/hooks/useSettings';
import { ApiError, api } from '@/lib/api';
import {
  compareDecimal,
  formatDecimal,
  formatMoney,
  formatRate,
  isDecimalString,
} from '@/lib/decimal';
import type { Position } from '@/types';

/**
 * What is held, what it is measured against, and what waiting costs.
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

export default function PositionPage() {
  const settings = useSettings();
  const state = useFxState();
  const save = useSaveState();
  const currentRate = useCurrentRate();

  const position = state.data?.position ?? null;
  const metrics = state.data?.metrics ?? null;
  const [form, setForm] = useState<FormState>(EMPTY);
  const [loaded, setLoaded] = useState(false);
  const [whatIf, setWhatIf] = useState('');

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
  const ratePlaces = settings.data?.formatting.rate_decimal_places ?? 4;

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

  if (state.isLoading) return <Loading label="Reading your position…" />;

  return (
    <>
      {!position && (
        <Banner tone="info">
          Nothing is saved yet. Fill this in and the dashboard will have something to show. Every
          field except the balance can be left empty and added later.
        </Banner>
      )}

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

      {metrics && (
        <Card
          title="What that comes to"
          subtitle="Computed on read, never stored, so these cannot drift out of step with the figures above."
        >
          <div className="fx-grid">
            <Stat
              label={`${target} value of what is held`}
              value={
                metrics.current_target_value === null
                  ? 'No rate yet'
                  : formatMoney(metrics.current_target_value, target)
              }
              note={
                metrics.current_rate === null
                  ? undefined
                  : `at ${formatRate(metrics.current_rate, ratePlaces)}`
              }
            />
            <Stat
              label="Unrealised improvement"
              value={
                metrics.unrealised_improvement === null
                  ? 'Set a baseline rate'
                  : formatMoney(metrics.unrealised_improvement, target)
              }
              note="on paper, against the baseline"
            />
            <Stat
              label="Realised improvement"
              quality={metrics.realised.includes_estimates ? 'estimate' : 'plain'}
              value={
                metrics.realised.confirmed === null
                  ? 'Set a baseline rate'
                  : formatMoney(metrics.realised.confirmed, target)
              }
              note={
                metrics.realised.includes_estimates
                  ? `plus about ${formatMoney(metrics.realised.estimated, target)} from rows whose amounts are estimated`
                  : 'confirmed'
              }
            />
            <Stat
              label="Daily carrying cost"
              value={
                metrics.daily_carrying_cost === null
                  ? 'Set a shortfall and loan rate'
                  : formatMoney(metrics.daily_carrying_cost, target)
              }
              note={
                metrics.monthly_carrying_cost === null
                  ? undefined
                  : `${formatMoney(metrics.monthly_carrying_cost, target)} a month`
              }
            />
          </div>
        </Card>
      )}

      <Card
        title="What if the rate were…"
        subtitle="Arithmetic on what you hold now. It is not a forecast, and nothing here says what to do about it."
      >
        <Field label="A rate to try" htmlFor="what-if">
          <input
            id="what-if"
            type="text"
            inputMode="decimal"
            placeholder={currentRate.data?.rate ?? '1.7500'}
            value={whatIf}
            onChange={(event) => setWhatIf(event.target.value)}
          />
        </Field>
        <WhatIf
          rate={whatIf}
          balance={position?.current_source_balance ?? null}
          baseline={position?.baseline_rate ?? null}
          source={source}
          target={target}
          ratePlaces={ratePlaces}
        />
      </Card>

      <Card
        title="Import or export this position"
        subtitle="A whole position and its history as one JSON document. Importing writes the balance exactly as given: the conversions in it are history, and do not reduce it a second time."
        actions={
          <a
            href={api.url('fx/state/export')}
            download
            className="fx-tag"
            style={{ textDecoration: 'none' }}
          >
            Export JSON
          </a>
        }
      >
        <p className="fx-stat-note">
          To import, send the document to <code>POST /api/v1/fx/state/import</code>. It previews by
          default; add <code>?commit=true</code> to write it. An example document with made-up
          figures is in <code>docs/examples/fx-state-example.json</code>.
        </p>
      </Card>
    </>
  );
}

/**
 * The single rate what-if that replaces the scenarios page.
 *
 * Deliberately the smallest useful thing: what the held balance would be worth,
 * and how that compares with the baseline. No ladder, no recommendation.
 */
function WhatIf({
  rate,
  balance,
  baseline,
  source,
  target,
  ratePlaces,
}: {
  rate: string;
  balance: string | null;
  baseline: string | null;
  source: string;
  target: string;
  ratePlaces: number;
}) {
  if (balance === null) {
    return <p className="fx-stat-note">Save a position first.</p>;
  }
  if (rate.trim() === '') {
    return <p className="fx-stat-note">Enter a rate to see what it would be worth.</p>;
  }
  if (!isDecimalString(rate)) {
    return <p className="fx-stat-note">That is not a rate.</p>;
  }

  // Multiplication of two exact decimal strings, done on the digits so the
  // figure shown is the same one the backend would compute.
  const value = multiplyDecimals(balance, rate.trim());
  const atBaseline = baseline === null ? null : multiplyDecimals(balance, baseline);
  const difference = atBaseline === null ? null : subtractDecimals(value, atBaseline);

  return (
    <div className="fx-grid">
      <Stat
        label={`${target} value at ${formatRate(rate.trim(), ratePlaces)}`}
        value={formatMoney(value, target)}
        note={`on ${formatDecimal(balance)} ${source}`}
      />
      {difference !== null && baseline !== null && (
        <Stat
          label="Against your baseline"
          value={formatMoney(difference, target, { signed: true })}
          note={`baseline ${formatRate(baseline, ratePlaces)}`}
        />
      )}
      {baseline === null && (
        <Stat
          label="Against your baseline"
          value="Set a baseline rate"
          note={<Tag quality="warning" />}
        />
      )}
    </div>
  );
}

/** Exact decimal multiplication on digit strings. Never touches Number. */
function multiplyDecimals(left: string, right: string): string {
  const scale = (value: string) => (value.split('.')[1] ?? '').length;
  const digits = (value: string) => BigInt(value.replace('.', '').replace(/^(-?)0+(?=\d)/, '$1'));
  const places = scale(left) + scale(right);
  const product = digits(left) * digits(right);
  return insertPoint(product, places);
}

function subtractDecimals(left: string, right: string): string {
  const scale = (value: string) => (value.split('.')[1] ?? '').length;
  const places = Math.max(scale(left), scale(right));
  const shift = (value: string) => {
    const [whole = '0', fraction = ''] = value.split('.');
    return BigInt(`${whole}${fraction.padEnd(places, '0')}`);
  };
  return insertPoint(shift(left) - shift(right), places);
}

function insertPoint(value: bigint, places: number): string {
  if (places === 0) return value.toString();
  const negative = value < 0n;
  const digits = (negative ? -value : value).toString().padStart(places + 1, '0');
  const cut = digits.length - places;
  return `${negative ? '-' : ''}${digits.slice(0, cut)}.${digits.slice(cut)}`;
}
