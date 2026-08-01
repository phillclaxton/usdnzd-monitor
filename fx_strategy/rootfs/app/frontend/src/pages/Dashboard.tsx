import { useState } from 'react';

import RateHeader from '@/components/RateHeader';
import { Banner, Card, EmptyState, Field, Loading } from '@/components/ui';
import { useCurrentRate, useRefreshRate, useSetManualRate } from '@/hooks/useRates';
import { useSettings } from '@/hooks/useSettings';
import { ApiError } from '@/lib/api';

export default function Dashboard() {
  const settings = useSettings();
  const rate = useCurrentRate();
  const refresh = useRefreshRate();
  const manual = useSetManualRate();
  const [manualRate, setManualRate] = useState('');

  if (settings.isLoading || rate.isLoading) return <Loading label="Loading dashboard…" />;

  const general = settings.data?.general;
  const timezone = general?.timezone ?? 'Pacific/Auckland';
  const ratePlaces = settings.data?.formatting.rate_decimal_places ?? 4;

  const refreshError = refresh.error as ApiError | null;

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
          Configured providers disagree by more than the allowed threshold. Targets will not be
          confirmed until two consecutive samples agree.
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
      </div>

      {rate.data && (
        <RateHeader rate={rate.data} timezone={timezone} ratePlaces={ratePlaces} />
      )}

      <Card title="Strategy">
        <EmptyState glyph="🪜" title="No strategy yet">
          <p>
            A strategy holds the amount you are converting, a ladder of target rates and a
            deadline. Creating one turns this page into the full dashboard.
          </p>
        </EmptyState>
      </Card>

      <Card
        title="Enter a rate manually"
        subtitle="Useful when no provider is configured, or to record a rate you saw in Wise."
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!manualRate) return;
            manual.mutate({ rate: manualRate }, { onSuccess: () => setManualRate('') });
          }}
        >
          <Field
            label={`${general?.target_currency ?? 'NZD'} per 1 ${general?.source_currency ?? 'USD'}`}
            hint="four decimal places"
            htmlFor="manual-rate"
            error={manual.isError ? (manual.error as Error).message : undefined}
          >
            <input
              id="manual-rate"
              inputMode="decimal"
              // A text input, not number: a number input would hand the browser
              // a float and can silently reformat what was typed.
              type="text"
              pattern="^\d+(\.\d{1,8})?$"
              placeholder="1.7600"
              value={manualRate}
              onChange={(event) => setManualRate(event.target.value)}
            />
          </Field>
          <button type="submit" disabled={manual.isPending || !manualRate}>
            {manual.isPending ? 'Saving…' : 'Record rate'}
          </button>
        </form>
      </Card>
    </>
  );
}
