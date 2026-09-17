import { useState } from 'react';
import { Link } from 'react-router-dom';

import PositionForm from '@/components/PositionForm';
import RateHeader from '@/components/RateHeader';
import { Banner, Card, EmptyState, Field, Loading, Stat, Tag } from '@/components/ui';
import { useFxAlerts, useFxState } from '@/hooks/usePosition';
import { useCurrentRate, useRefreshRate, useSetManualRate } from '@/hooks/useRates';
import { useSettings } from '@/hooks/useSettings';
import { ApiError } from '@/lib/api';
import { formatDateTime } from '@/lib/datetime';
import { formatDecimal, formatMoney, formatRate } from '@/lib/decimal';
import type { FxAlert, LatestConversion, PositionMetrics } from '@/types';

/**
 * What is held, what it is worth, and what has changed.
 *
 * The screen answers one question — "what is my FX position, and has anything
 * important changed?" — and deliberately does not answer "what should I convert
 * next?". Nothing here recommends a conversion; Wise executes and the figures
 * are what this is for.
 *
 * Money and rates arrive as strings and stay strings: `lib/decimal` formats
 * digits, and a `Number()` anywhere near a financial value would reintroduce
 * exactly the rounding the backend is built to avoid.
 */

function ManualRateForm() {
  const manual = useSetManualRate();
  const settings = useSettings();
  const [value, setValue] = useState('');
  const general = settings.data?.general;

  return (
    <Card
      title="Enter a rate manually"
      subtitle="Useful when no provider is configured, or to record a rate you saw in Wise."
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!value) return;
          manual.mutate({ rate: value }, { onSuccess: () => setValue('') });
        }}
      >
        <Field
          label={`${general?.target_currency ?? 'NZD'} per 1 ${general?.source_currency ?? 'USD'}`}
          hint="up to eight decimal places"
          htmlFor="manual-rate"
          error={manual.isError ? (manual.error as Error).message : undefined}
        >
          <input
            id="manual-rate"
            inputMode="decimal"
            // A text input, not number: a number input hands the browser a
            // float and can silently reformat what was typed.
            type="text"
            pattern="^\d+(\.\d{1,8})?$"
            placeholder="1.7600"
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </Field>
        <button type="submit" disabled={manual.isPending || !value}>
          {manual.isPending ? 'Saving…' : 'Record rate'}
        </button>
      </form>
    </Card>
  );
}

/**
 * The headline figures.
 *
 * A metric that cannot be computed says what is missing. It never reads 0.00,
 * because "you have gained nothing" and "there is no baseline to measure
 * against" are different claims and only one of them is true.
 */
function Headline({
  metrics,
  source,
  target,
  ratePlaces,
}: {
  metrics: PositionMetrics;
  source: string;
  target: string;
  ratePlaces: number;
}) {
  return (
    <Card
      title="What you hold"
      subtitle="Computed on read, never stored, so nothing here can drift out of step with the position."
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
              ? 'no trusted rate has arrived yet'
              : `at ${formatRate(metrics.current_rate, ratePlaces)}, ${metrics.rate_status}`
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
          label="Total improvement"
          value={
            metrics.total_improvement_confirmed === null
              ? 'Set a baseline rate'
              : formatMoney(metrics.total_improvement_confirmed, target)
          }
          note="confirmed realised plus unrealised"
        />
        <Stat
          label={`${source} converted so far`}
          value={formatDecimal(metrics.total_source_converted)}
          note={`${formatMoney(metrics.total_target_received, target)} received`}
          small
        />
        <Stat
          label="Fees paid"
          value={
            metrics.total_fees_target === null
              ? 'Not recorded'
              : formatMoney(metrics.total_fees_target, target)
          }
          note="separate from the improvement above, never netted into it"
          small
        />
      </div>
    </Card>
  );
}

/** What the unfunded part of the offset costs while it waits. */
function CarryingCost({ metrics, target }: { metrics: PositionMetrics; target: string }) {
  return (
    <Card
      title="What waiting costs"
      subtitle="Charged on the part of the offset that is still unfunded, never on the whole balance."
    >
      <div className="fx-grid">
        <Stat
          label="A day"
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
        <Stat
          label="Months of spending held"
          value={
            metrics.months_of_burn === null
              ? 'Set your monthly spending'
              : `${formatDecimal(metrics.months_of_burn, { places: 1 })} months`
          }
          note="what is held, at the current rate, against what you spend"
          small
        />
      </div>
    </Card>
  );
}

function LatestConversionCard({
  conversion,
  count,
  target,
  timezone,
  ratePlaces,
}: {
  conversion: LatestConversion | null;
  count: number;
  target: string;
  timezone: string;
  ratePlaces: number;
}) {
  return (
    <Card
      title="Latest conversion"
      actions={
        <Link to="/conversions" className="fx-tag" style={{ textDecoration: 'none' }}>
          All {count}
        </Link>
      }
    >
      {conversion === null ? (
        <EmptyState glyph="↔" title="Nothing recorded yet">
          <p>
            Record what Wise has already converted on the{' '}
            <Link to="/conversions">conversions page</Link>, or import a whole history from the{' '}
            <Link to="/position">position page</Link>.
          </p>
        </EmptyState>
      ) : (
        <div className="fx-grid">
          <Stat
            label="Converted"
            quality={conversion.amounts_estimated ? 'estimate' : 'plain'}
            value={formatDecimal(conversion.source_amount)}
            note={`at ${formatRate(conversion.gross_rate, ratePlaces)}`}
          />
          <Stat
            label="Received"
            value={formatMoney(conversion.target_amount, target)}
            note={formatDateTime(conversion.executed_at, timezone)}
          />
        </div>
      )}
    </Card>
  );
}

/**
 * What the app has said lately.
 *
 * Undelivered rows are shown rather than hidden: a notification that failed to
 * send is the one most worth seeing, and silently dropping it would make the
 * app look quiet when it was actually broken.
 */
function RecentAlerts({ alerts, timezone }: { alerts: FxAlert[]; timezone: string }) {
  return (
    <Card title="Recent alerts" subtitle="What changed, and what it was worth. Never what to do.">
      {alerts.length === 0 ? (
        <EmptyState glyph="🔔" title="Nothing to report">
          <p>Alerts appear here when the rate moves enough to be worth mentioning.</p>
        </EmptyState>
      ) : (
        <ul className="fx-alert-list">
          {alerts.map((alert) => (
            <li key={alert.id}>
              <div className="fx-inline" style={{ justifyContent: 'space-between' }}>
                <strong>{alert.title}</strong>
                {!alert.delivered && <Tag quality="warning">Not delivered</Tag>}
              </div>
              <p className="fx-stat-note">{alert.message}</p>
              <p className="fx-stat-note">{formatDateTime(alert.created_at, timezone)}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function Dashboard() {
  const settings = useSettings();
  const rate = useCurrentRate();
  const state = useFxState();
  const alerts = useFxAlerts(5);
  const refresh = useRefreshRate();

  if (settings.isLoading || rate.isLoading || state.isLoading) {
    return <Loading label="Loading dashboard…" />;
  }

  const timezone = settings.data?.general.timezone ?? 'Pacific/Auckland';
  const ratePlaces = settings.data?.formatting.rate_decimal_places ?? 4;
  const refreshError = refresh.error as ApiError | null;

  const position = state.data?.position ?? null;
  const metrics = state.data?.metrics ?? null;
  const source = position?.source_currency ?? settings.data?.general.source_currency ?? 'USD';
  const target = position?.target_currency ?? settings.data?.general.target_currency ?? 'NZD';

  return (
    <>
      {refreshError && (
        <Banner tone="error">
          {refreshError.message}
          {refreshError.details ? (
            <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
              {Object.entries(
                (refreshError.details as { errors?: Record<string, string> }).errors ?? {},
              ).map(([provider, message]) => (
                <li key={provider}>
                  <strong>{provider}</strong>: {message}
                </li>
              ))}
            </ul>
          ) : null}
        </Banner>
      )}
      {refresh.isSuccess && refresh.data.disagreement_exceeded && (
        <Banner tone="warning">
          Configured providers disagree by more than the allowed threshold. Nothing is alerted on a
          rate the app does not trust.
        </Banner>
      )}

      <div className="fx-toolbar">
        <button
          type="button"
          className="is-primary"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
        >
          {refresh.isPending ? 'Refreshing…' : 'Refresh rate'}
        </button>
        <Link
          to="/position"
          className="fx-tag"
          style={{ textDecoration: 'none', padding: '10px 14px' }}
        >
          Edit position
        </Link>
        <Link
          to="/conversions"
          className="fx-tag"
          style={{ textDecoration: 'none', padding: '10px 14px' }}
        >
          Conversions
        </Link>
      </div>

      {rate.data && <RateHeader rate={rate.data} timezone={timezone} ratePlaces={ratePlaces} />}

      {position === null ? (
        <>
          <Banner tone="info">
            Nothing is saved yet. Tell the app what you hold and it can tell you what it is worth,
            what it has gained, and what waiting for it costs. Only the balance is required.
          </Banner>
          <PositionForm />
        </>
      ) : (
        <>
          {metrics && (
            <Headline metrics={metrics} source={source} target={target} ratePlaces={ratePlaces} />
          )}
          {metrics && <CarryingCost metrics={metrics} target={target} />}
          <LatestConversionCard
            conversion={state.data?.latest_conversion ?? null}
            count={state.data?.conversion_count ?? 0}
            target={target}
            timezone={timezone}
            ratePlaces={ratePlaces}
          />
          <RecentAlerts alerts={alerts.data ?? []} timezone={timezone} />
        </>
      )}

      <ManualRateForm />
    </>
  );
}
