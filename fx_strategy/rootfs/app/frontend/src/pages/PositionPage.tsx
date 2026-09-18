import { useState } from 'react';

import PositionForm from '@/components/PositionForm';
import { Banner, Card, Field, Loading, Stat, Tag } from '@/components/ui';
import { useFxState } from '@/hooks/usePosition';
import { useCurrentRate } from '@/hooks/useRates';
import { useSettings } from '@/hooks/useSettings';
import { api } from '@/lib/api';
import { formatDecimal, formatMoney, formatRate, isDecimalString } from '@/lib/decimal';

/**
 * What is held, what it is measured against, and what waiting costs.
 *
 * The form itself lives in `PositionForm`, because the dashboard shows it too:
 * on a fresh install there is nothing to put on the dashboard until it has been
 * filled in, and sending someone to another screen to fill it in is a worse
 * first five minutes than simply showing them the form.
 */

export default function PositionPage() {
  const settings = useSettings();
  const state = useFxState();
  const currentRate = useCurrentRate();

  const position = state.data?.position ?? null;
  const metrics = state.data?.metrics ?? null;
  const [whatIf, setWhatIf] = useState('');

  const source = position?.source_currency ?? settings.data?.general.source_currency ?? 'USD';
  const target = position?.target_currency ?? settings.data?.general.target_currency ?? 'NZD';
  const ratePlaces = settings.data?.formatting.rate_decimal_places ?? 4;

  if (state.isLoading) return <Loading label="Reading your position…" />;

  return (
    <>
      {!position && (
        <Banner tone="info">
          Nothing is saved yet. Fill this in and the dashboard will have something to show. Every
          field except the balance can be left empty and added later.
        </Banner>
      )}

      <PositionForm />

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
