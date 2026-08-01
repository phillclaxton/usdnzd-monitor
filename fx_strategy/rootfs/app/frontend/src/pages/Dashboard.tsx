import { useState } from 'react';
import { Link } from 'react-router-dom';

import PortfolioPanel from '@/components/PortfolioPanel';
import RateHeader from '@/components/RateHeader';
import RiskPanel from '@/components/RiskPanel';
import TranchePanel from '@/components/TranchePanel';
import { Banner, Card, EmptyState, Field, Loading } from '@/components/ui';
import { useCurrentRate, useRefreshRate, useSetManualRate } from '@/hooks/useRates';
import { useSettings } from '@/hooks/useSettings';
import { useSummary } from '@/hooks/useStrategy';
import { ApiError } from '@/lib/api';

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

export default function Dashboard() {
  const settings = useSettings();
  const rate = useCurrentRate();
  const summary = useSummary();
  const refresh = useRefreshRate();

  if (settings.isLoading || rate.isLoading) return <Loading label="Loading dashboard…" />;

  const timezone = settings.data?.general.timezone ?? 'Pacific/Auckland';
  const ratePlaces = settings.data?.formatting.rate_decimal_places ?? 4;
  const refreshError = refresh.error as ApiError | null;
  const noStrategy = summary.isError && (summary.error as ApiError).status === 404;

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
          Configured providers disagree by more than the allowed threshold. No target will be
          confirmed until two consecutive samples agree.
        </Banner>
      )}
      {summary.data?.warnings.map((warning) => (
        <Banner key={warning} tone="warning">
          {warning}
        </Banner>
      ))}

      <div className="fx-toolbar">
        <button
          type="button"
          className="is-primary"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
        >
          {refresh.isPending ? 'Refreshing…' : 'Refresh rate'}
        </button>
        <Link to="/strategy" className="fx-tag" style={{ textDecoration: 'none', padding: '10px 14px' }}>
          Edit strategy
        </Link>
        <Link to="/scenarios" className="fx-tag" style={{ textDecoration: 'none', padding: '10px 14px' }}>
          Compare scenarios
        </Link>
      </div>

      {rate.data && <RateHeader rate={rate.data} timezone={timezone} ratePlaces={ratePlaces} />}

      {noStrategy && (
        <Card title="Strategy">
          <EmptyState glyph="🪜" title="No strategy yet">
            <p>
              A strategy holds the amount you are converting, a ladder of target rates and a
              deadline.
            </p>
            <p>
              <Link to="/strategy">Create one now</Link> — the recommended ladder loads with a
              single click.
            </p>
          </EmptyState>
        </Card>
      )}

      {summary.isLoading && <Loading label="Loading strategy…" />}

      {summary.data && (
        <>
          <PortfolioPanel summary={summary.data} ratePlaces={ratePlaces} />
          <TranchePanel summary={summary.data} ratePlaces={ratePlaces} />
          <RiskPanel summary={summary.data} ratePlaces={ratePlaces} />
        </>
      )}

      <ManualRateForm />
    </>
  );
}
