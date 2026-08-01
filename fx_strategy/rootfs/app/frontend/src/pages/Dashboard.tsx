import { useHealth, useSettings } from '@/hooks/useSettings';
import { Card, EmptyState, Loading } from '@/components/ui';

/**
 * Phase 1 dashboard: proves the shell, the API path resolution and the theme.
 * The rate, portfolio, opportunity, tranche and risk panels arrive with the
 * rate and strategy phases.
 */
export default function Dashboard() {
  const settings = useSettings();
  const health = useHealth();

  if (settings.isLoading) return <Loading label="Loading settings…" />;

  const general = settings.data?.general;

  return (
    <>
      <Card title="Getting started">
        <EmptyState glyph="🪜" title="No strategy yet">
          <p>
            FX Strategy Manager watches an exchange rate and tracks a staged conversion plan. It
            does not predict rates, and it never moves money.
          </p>
          <p>
            Configured pair:{' '}
            <strong>
              {general?.source_currency ?? 'USD'} → {general?.target_currency ?? 'NZD'}
            </strong>
            , shown as {general?.target_currency ?? 'NZD'} per 1{' '}
            {general?.source_currency ?? 'USD'}.
          </p>
        </EmptyState>
      </Card>

      <Card title="Connection">
        <dl className="fx-grid">
          <div className="fx-stat">
            <div className="fx-stat-label">Backend version</div>
            <div className="fx-stat-value is-small">{health.data?.version ?? '—'}</div>
          </div>
          <div className="fx-stat">
            <div className="fx-stat-label">Architecture</div>
            <div className="fx-stat-value is-small">{health.data?.arch ?? '—'}</div>
          </div>
          <div className="fx-stat">
            <div className="fx-stat-label">Timezone</div>
            <div className="fx-stat-value is-small">{general?.timezone ?? '—'}</div>
          </div>
        </dl>
      </Card>
    </>
  );
}
