import { Suspense, lazy, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import ObligationForm from '@/components/ObligationForm';
import ObligationDetail from '@/components/ObligationDetail';
import { Banner, Card, EmptyState, Loading, Stat, Tag } from '@/components/ui';
import { api } from '@/lib/api';
import { compareDecimal, formatMoney, formatRate } from '@/lib/decimal';
import type { Allocation, Obligation, ObligationPortfolio } from '@/types';

// Recharts is the largest dependency in the bundle. Splitting it out keeps this
// page quick to open on a phone, which is the same reason the rate chart is
// loaded lazily.
const ObligationCharts = lazy(() => import('@/components/ObligationCharts'));

/** Wording for each recommended action, and how loudly to say it. */
export const ACTION_LABELS: Record<string, { label: string; tone: 'urgent' | 'wait' | 'done' }> = {
  PAY_NOW: { label: 'Pay now', tone: 'urgent' },
  CONVERT_NOW: { label: 'Convert now', tone: 'urgent' },
  CONVERT_PARTIAL: { label: 'Convert part', tone: 'urgent' },
  WAIT_FOR_TARGET: { label: 'Wait for target', tone: 'wait' },
  WAIT_WITH_DEADLINE: { label: 'Wait, with deadline', tone: 'wait' },
  REVIEW: { label: 'Review', tone: 'wait' },
  FUNDED: { label: 'Funded', tone: 'done' },
  OVERDUE: { label: 'Overdue', tone: 'urgent' },
};

const PRIORITY_LABELS: Record<string, string> = {
  critical: 'Critical',
  high: 'High',
  normal: 'Normal',
  low: 'Low',
};

export default function ObligationsPage() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [usdAvailable, setUsdAvailable] = useState('');

  const obligations = useQuery({
    queryKey: ['obligations'],
    queryFn: () => api.get<Obligation[]>('obligations'),
  });

  const portfolio = useQuery({
    queryKey: ['obligations', 'portfolio'],
    queryFn: () => api.get<ObligationPortfolio>('obligations/portfolio'),
  });

  const allocations = useQuery({
    queryKey: ['obligations', 'allocations'],
    queryFn: () => api.get<Allocation[]>('obligations/allocations'),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['obligations'] });

  const custom = useMutation({
    mutationFn: (usd: string) =>
      api.post<Allocation>('obligations/allocations', { usd_available: usd }),
  });

  const rows = obligations.data ?? [];
  const book = portfolio.data;
  const detail = rows.find((row) => row.id === selected) ?? null;

  // Overall rank is the default ordering: it is the one that answers "what next".
  const ordered = [...rows].sort((a, b) => a.overall_rank - b.overall_rank);

  return (
    <>
      <Card
        title="Obligations"
        subtitle="Debts and commitments that may be funded by converting USD. Decision support only — this app never pays, converts or transfers anything."
      >
        {obligations.isLoading && <Loading />}

        {book && book.rate_stale && (
          <Banner tone="warning">
            The exchange rate is stale. USD figures are still shown, but no recommendation to
            wait is made from it.
          </Banner>
        )}
        {book?.warnings.map((warning) => (
          <Banner key={warning} tone="warning">
            {warning}
          </Banner>
        ))}

        {book && book.total_obligations > 0 && (
          <div className="fx-grid">
            <Stat
              label="Total outstanding"
              value={formatMoney(book.total_nzd, 'NZD')}
              note={`${book.total_obligations} active`}
            />
            <Stat
              label="USD required now"
              value={
                book.total_usd_required ? formatMoney(book.total_usd_required, 'USD') : 'Not known'
              }
              quality={book.total_usd_required ? 'estimate' : 'warning'}
              note={book.rate_used ? `at ${formatRate(book.rate_used)}` : 'no rate available'}
            />
            <Stat
              label="Cost of waiting"
              value={`${formatMoney(book.total_monthly_cost_nzd, 'NZD')} / month`}
              note={`${formatMoney(book.total_daily_cost_nzd, 'NZD')} a day`}
            />
            <Stat
              label="Highest priority"
              value={book.highest_priority_obligation_name || '—'}
              note="by overall rank"
            />
            <Stat
              label="Recommended next"
              value={
                book.next_conversion_usd
                  ? formatMoney(book.next_conversion_usd, 'USD')
                  : formatMoney(book.next_conversion_nzd, 'NZD')
              }
              quality="estimate"
              note={book.next_obligation_name ? `for ${book.next_obligation_name}` : undefined}
            />
            <Stat
              label="Weighted break-even rate"
              value={
                book.weighted_break_even_rate ? formatRate(book.weighted_break_even_rate) : '—'
              }
              note="repays 30 days of waiting"
            />
            <Stat
              label="Maximum rational wait"
              value={
                book.max_rational_wait_days === null ? '—' : `${book.max_rational_wait_days} days`
              }
              note="the shortest limit in the book"
            />
            <Stat
              label="Due within 30 days"
              value={formatMoney(book.due_within_30_days_nzd, 'NZD')}
              note={`${formatMoney(book.due_within_7_days_nzd, 'NZD')} within 7`}
            />
          </div>
        )}

        <div className="fx-toolbar">
          <button type="button" className="is-primary" onClick={() => setAdding(true)}>
            Add an obligation
          </button>
        </div>
      </Card>

      {adding && (
        <ObligationForm
          onDone={() => {
            setAdding(false);
            void invalidate();
          }}
          onCancel={() => setAdding(false)}
        />
      )}

      {rows.length === 0 && !obligations.isLoading && (
        <EmptyState title="No obligations recorded" glyph="🧾">
          Add a debt, loan or commitment and this page will show what funding it costs, what
          waiting costs, and which to fund first.
        </EmptyState>
      )}

      {rows.length > 0 && (
        <Card
          title="Priority"
          subtitle="Ordered by overall priority, which counts non-financial importance as well as cost."
        >
          <div className="fx-table-wrap">
            <table className="fx-table">
              <thead>
                <tr>
                  <th className="fx-left">Obligation</th>
                  <th>Remaining</th>
                  <th>Rate</th>
                  <th>Daily cost</th>
                  <th>Due</th>
                  <th>Priority</th>
                  <th>Next target</th>
                  <th>Break-even days</th>
                  <th className="fx-left">Recommendation</th>
                </tr>
              </thead>
              <tbody>
                {ordered.map((row) => {
                  const action = ACTION_LABELS[row.action] ?? {
                    label: row.action,
                    tone: 'wait' as const,
                  };
                  const breakEven = row.break_even_days_at_improvement['0.01'];
                  return (
                    <tr
                      key={row.id}
                      onClick={() => setSelected(row.id === selected ? null : row.id)}
                      style={{ cursor: 'pointer' }}
                    >
                      <td className="fx-left">
                        {row.name}
                        {row.relationship_importance === 'high' && (
                          <span className="fx-hint"> · relationship</span>
                        )}
                      </td>
                      <td>{formatMoney(row.remaining_nzd, 'NZD')}</td>
                      <td>
                        {row.has_interest_cost
                          ? `${(Number(row.annual_rate) * 100).toFixed(2)}%`
                          : 'None'}
                      </td>
                      <td>
                        {row.has_interest_cost ? formatMoney(row.daily_cost_nzd, 'NZD') : '—'}
                      </td>
                      <td>
                        {row.due_date ?? '—'}
                        {row.days_until_due !== null && (
                          <span className="fx-hint"> ({row.days_until_due}d)</span>
                        )}
                      </td>
                      <td>{PRIORITY_LABELS[row.priority] ?? row.priority}</td>
                      <td>{row.target_rate ? formatRate(row.target_rate) : '—'}</td>
                      <td>
                        {/* Blank rather than zero: with no interest there is no
                            break-even period at all. */}
                        {breakEven ? Number(breakEven).toFixed(0) : '—'}
                      </td>
                      <td className="fx-left">
                        <Tag quality={action.tone === 'urgent' ? 'warning' : 'plain'} />{' '}
                        {action.label}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="fx-card-subtitle">Select a row for the full working.</p>
        </Card>
      )}

      {detail && (
        <ObligationDetail
          obligation={detail}
          onChanged={invalidate}
          onClose={() => setSelected(null)}
        />
      )}

      {rows.length > 0 && (
        <Suspense fallback={<Loading label="Loading charts…" />}>
          <ObligationCharts obligations={rows} />
        </Suspense>
      )}

      {rows.length > 0 && (
        <Card
          title="Conversion allocation"
          subtitle="What a conversion would settle, and what would be left. A suggestion — you carry it out in Wise yourself."
        >
          {allocations.isLoading && <Loading />}
          {(allocations.data ?? []).map((plan) => (
            <div key={plan.label} className="fx-allocation">
              <h3>{plan.label}</h3>
              <p className="fx-card-subtitle">{plan.description}</p>
              <div className="fx-grid">
                <Stat
                  label="Convert"
                  value={plan.usd_to_convert ? formatMoney(plan.usd_to_convert, 'USD') : '—'}
                  quality="estimate"
                />
                <Stat label="Obtains" value={formatMoney(plan.nzd_obtained, 'NZD')} />
                <Stat
                  label="Left unfunded"
                  value={formatMoney(plan.unfunded_nzd, 'NZD')}
                  quality={compareDecimal(plan.unfunded_nzd, '0') > 0 ? 'warning' : 'plain'}
                />
              </div>
              {plan.lines.length > 0 && (
                <ul className="fx-list">
                  {plan.lines.map((line) => (
                    <li key={line.obligation_id}>
                      {line.name}: {formatMoney(line.nzd_funded, 'NZD')}
                      {line.fully_funded ? ' (in full)' : ' (part)'}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}

          <div className="fx-field">
            <label htmlFor="usd-available">
              Try a specific amount
              <span className="fx-hint"> — USD available to convert</span>
            </label>
            <input
              id="usd-available"
              type="text"
              inputMode="decimal"
              placeholder="e.g. 100000"
              value={usdAvailable}
              onChange={(event) => setUsdAvailable(event.target.value)}
            />
          </div>
          <div className="fx-toolbar">
            <button
              type="button"
              disabled={!usdAvailable || custom.isPending}
              onClick={() => custom.mutate(usdAvailable)}
            >
              {custom.isPending ? 'Working…' : 'Show what it would settle'}
            </button>
          </div>

          {custom.data && (
            <div className="fx-allocation">
              <h3>{formatMoney(custom.data.usd_to_convert ?? '0', 'USD')} would settle</h3>
              {custom.data.lines.length === 0 ? (
                <Banner tone="warning">
                  That amount funds nothing in full, and every obligation it could reach either
                  refuses partial payment or has a higher minimum.
                </Banner>
              ) : (
                <ul className="fx-list">
                  {custom.data.lines.map((line) => (
                    <li key={line.obligation_id}>
                      {line.name}: {formatMoney(line.nzd_funded, 'NZD')}
                      {line.fully_funded ? ' (in full)' : ' (part)'}
                    </li>
                  ))}
                </ul>
              )}
              <p className="fx-card-subtitle">
                {formatMoney(custom.data.unfunded_nzd, 'NZD')} would remain unfunded.
              </p>
            </div>
          )}

          <Banner tone="info">{book?.disclaimer}</Banner>
        </Card>
      )}
    </>
  );
}
