/**
 * Reviewing stored rate observations, and taking bad ones out of the figures.
 *
 * A provider glitch puts a point on the chart that never happened. Excluding it
 * keeps the row — what a provider actually returned is the evidence for why the
 * figure was wrong — while removing it from the chart, the high and low, the
 * averages and the aggregates behind the long ranges.
 */
import { useState } from 'react';

import { Banner, Card, EmptyState, Loading } from '@/components/ui';
import {
  useExcludeSamples,
  useRateSamples,
  useRestoreSamples,
  type RateRange,
} from '@/hooks/useRates';
import { useSettings } from '@/hooks/useSettings';
import { formatDateTime } from '@/lib/datetime';
import { formatDecimal } from '@/lib/decimal';
import type { RateSample } from '@/types';

/** A proportion such as "0.0320" as a percentage for display. */
function asPercent(value: string | null): string {
  if (value === null) return '—';
  const scaled = (Number(value) * 100).toFixed(2);
  return `${scaled}%`;
}

export default function RateSampleReview({ range }: { range: RateRange }) {
  const [suspiciousOnly, setSuspiciousOnly] = useState(true);
  const samples = useRateSamples(range, suspiciousOnly);
  const exclude = useExcludeSamples();
  const restore = useRestoreSamples();
  const settings = useSettings();
  const timezone = settings.data?.general.timezone ?? 'Pacific/Auckland';
  const places = settings.data?.formatting.rate_decimal_places ?? 4;

  const rows = samples.data?.samples ?? [];
  const busy = exclude.isPending || restore.isPending;

  const excludeOne = (sample: RateSample) =>
    exclude.mutate({
      sample_ids: [sample.id],
      reason: `Excluded by hand: ${formatDecimal(sample.rate, { places, grouping: false })} from ${
        sample.provider
      } did not happen.`,
    });

  const excludeAllSuspicious = () => {
    const ids = rows.filter((row) => row.suspicious && !row.excluded).map((row) => row.id);
    if (ids.length === 0) return;
    exclude.mutate({
      sample_ids: ids,
      reason: 'Excluded by hand: flagged as standing well away from the surrounding rates.',
    });
  };

  return (
    <Card
      title="Rate data points"
      subtitle={
        samples.data
          ? `${samples.data.total} stored in this range · ${samples.data.suspicious_count} ` +
            `standing out · ${samples.data.excluded_count} excluded`
          : undefined
      }
    >
      <Banner tone="info">
        Excluding a point removes it from the chart and from every figure derived from it. The
        observation itself is kept, and can be restored.
      </Banner>

      <div className="fx-toolbar">
        <button
          type="button"
          aria-pressed={suspiciousOnly}
          className={suspiciousOnly ? 'is-primary' : undefined}
          onClick={() => setSuspiciousOnly(true)}
        >
          Only the ones that stand out
        </button>
        <button
          type="button"
          aria-pressed={!suspiciousOnly}
          className={!suspiciousOnly ? 'is-primary' : undefined}
          onClick={() => setSuspiciousOnly(false)}
        >
          Every point
        </button>
        {(samples.data?.suspicious_count ?? 0) > 0 && (
          <button type="button" onClick={excludeAllSuspicious} disabled={busy}>
            Exclude all {samples.data?.suspicious_count} that stand out
          </button>
        )}
      </div>

      {samples.isLoading && <Loading label="Loading rate data…" />}
      {samples.isError && (
        <Banner tone="error">The stored rate data could not be loaded from the backend.</Banner>
      )}
      {exclude.isError && <Banner tone="error">{(exclude.error as Error).message}</Banner>}
      {restore.isError && <Banner tone="error">{(restore.error as Error).message}</Banner>}
      {exclude.isSuccess && <Banner tone="info">{exclude.data.message}</Banner>}
      {restore.isSuccess && <Banner tone="info">{restore.data.message}</Banner>}

      {samples.data && rows.length === 0 && (
        <EmptyState glyph="✓" title="Nothing stands out">
          <p>
            No point in this range sits more than{' '}
            {asPercent(samples.data.threshold)} away from the rates around it. Choose
            &ldquo;Every point&rdquo; to see the full list.
          </p>
        </EmptyState>
      )}

      {rows.length > 0 && (
        <div className="fx-table-wrap">
          <table className="fx-table">
            <thead>
              <tr>
                <th className="fx-left">Time</th>
                <th>Rate</th>
                <th className="fx-left">Provider</th>
                <th>Away from neighbours</th>
                <th className="fx-left">Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className="fx-left">{formatDateTime(row.timestamp, timezone)}</td>
                  <td>{formatDecimal(row.rate, { places, grouping: false })}</td>
                  <td className="fx-left">{row.provider}</td>
                  <td>{asPercent(row.deviation)}</td>
                  <td className="fx-left">
                    {row.excluded ? (
                      <span className="fx-tag is-warning" title={row.excluded_reason ?? ''}>
                        Excluded
                      </span>
                    ) : row.suspicious ? (
                      <span className="fx-tag is-warning">Stands out</span>
                    ) : (
                      <span className="fx-tag is-actual">In use</span>
                    )}
                  </td>
                  <td>
                    {row.excluded ? (
                      <button
                        type="button"
                        className="fx-link-button"
                        disabled={busy}
                        onClick={() => restore.mutate({ sample_ids: [row.id] })}
                      >
                        Restore
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="fx-link-button"
                        disabled={busy}
                        onClick={() => excludeOne(row)}
                      >
                        Exclude
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rows.some((row) => row.excluded && row.excluded_reason) && (
        <p className="fx-stat-note">
          Hover an <strong>Excluded</strong> tag to see why that point was taken out.
        </p>
      )}
    </Card>
  );
}
