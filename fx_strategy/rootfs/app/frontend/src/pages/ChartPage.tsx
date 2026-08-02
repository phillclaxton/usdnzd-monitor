import { useMemo, useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { Banner, Card, EmptyState, Loading } from '@/components/ui';
import { RATE_RANGES, useRateHistory, type RateRange } from '@/hooks/useRates';
import { useSettings } from '@/hooks/useSettings';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/datetime';
import { formatDecimal, toChartNumber } from '@/lib/decimal';

const RANGE_LABEL: Record<RateRange, string> = {
  '24h': '24 hours',
  '7d': '7 days',
  '30d': '30 days',
  '3m': '3 months',
  '6m': '6 months',
  '1y': '1 year',
};

export default function ChartPage() {
  const [range, setRange] = useState<RateRange>('30d');
  const history = useRateHistory(range);
  const settings = useSettings();
  const timezone = settings.data?.general.timezone ?? 'Pacific/Auckland';
  const places = settings.data?.formatting.rate_decimal_places ?? 4;

  const series = useMemo(
    () =>
      (history.data?.points ?? []).map((point) => ({
        time: new Date(point.timestamp).getTime(),
        // Chart geometry needs a number; the exact string is kept for the
        // tooltip so no displayed figure comes from the float.
        value: toChartNumber(point.rate),
        exact: point.rate,
      })),
    [history.data],
  );

  return (
    <>
      <Card
        title="Rate history"
        subtitle={
          history.data
            ? `${series.length} points at ${history.data.resolution} resolution, times in ${timezone}`
            : undefined
        }
        actions={
          <a
            href={api.url(`rates/export?range=${range}`)}
            download
            className="fx-tag"
            style={{ textDecoration: 'none' }}
          >
            Export CSV
          </a>
        }
      >
        <div className="fx-toolbar" role="group" aria-label="Chart range">
          {RATE_RANGES.map((key) => (
            <button
              key={key}
              type="button"
              className={key === range ? 'is-primary' : ''}
              aria-pressed={key === range}
              onClick={() => setRange(key)}
            >
              {RANGE_LABEL[key]}
            </button>
          ))}
        </div>

        {history.isLoading && <Loading label="Loading rate history…" />}
        {history.isError && (
          <Banner tone="error">Rate history could not be loaded from the backend.</Banner>
        )}
        {history.data && series.length === 0 && (
          <EmptyState glyph="📉" title="No rate history yet">
            <p>
              History appears once the app has polled a provider, or after importing a CSV of past
              rates from Settings.
            </p>
          </EmptyState>
        )}

        {series.length > 0 && (
          <div style={{ width: '100%', height: 320 }}>
            <ResponsiveContainer>
              <LineChart data={series} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
                <CartesianGrid stroke="var(--fx-border)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="time"
                  type="number"
                  domain={['dataMin', 'dataMax']}
                  scale="time"
                  tick={{ fontSize: 11, fill: 'var(--fx-text-muted)' }}
                  tickFormatter={(value: number) =>
                    formatDateTime(new Date(value).toISOString(), timezone).split(',')[0] ?? ''
                  }
                />
                <YAxis
                  domain={['auto', 'auto']}
                  width={64}
                  tick={{ fontSize: 11, fill: 'var(--fx-text-muted)' }}
                  tickFormatter={(value: number) => value.toFixed(places)}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--fx-surface)',
                    border: '1px solid var(--fx-border)',
                    borderRadius: 8,
                    color: 'var(--fx-text)',
                  }}
                  labelFormatter={(value) =>
                    formatDateTime(new Date(Number(value)).toISOString(), timezone)
                  }
                  formatter={(_value, _name, item) => [
                    formatDecimal((item?.payload as { exact?: string })?.exact, {
                      places,
                      grouping: false,
                    }),
                    'Rate',
                  ]}
                />
                {history.data?.average && (
                  <ReferenceLine
                    y={toChartNumber(history.data.average)}
                    stroke="var(--fx-neutral)"
                    strokeDasharray="6 4"
                    label={{
                      value: `Average ${formatDecimal(history.data.average, { places, grouping: false })}`,
                      position: 'insideTopLeft',
                      fill: 'var(--fx-text-muted)',
                      fontSize: 11,
                    }}
                  />
                )}
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="var(--fx-accent)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                  name="Rate"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {history.data && (
          <dl className="fx-grid" style={{ marginTop: 'var(--fx-gap)' }}>
            <div className="fx-stat">
              <div className="fx-stat-label">High</div>
              <div className="fx-stat-value is-small">
                {formatDecimal(history.data.high, { places, grouping: false })}
              </div>
            </div>
            <div className="fx-stat">
              <div className="fx-stat-label">Low</div>
              <div className="fx-stat-value is-small">
                {formatDecimal(history.data.low, { places, grouping: false })}
              </div>
            </div>
            <div className="fx-stat">
              <div className="fx-stat-label">Average</div>
              <div className="fx-stat-value is-small">
                {formatDecimal(history.data.average, { places, grouping: false })}
              </div>
            </div>
          </dl>
        )}

        {history.data?.truncated && (
          <p className="fx-stat-note">
            The series was thinned for display. Export the CSV for every stored point.
          </p>
        )}
      </Card>
    </>
  );
}
