import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import { Banner, Card, Field, Stat } from '@/components/ui';
import { api } from '@/lib/api';
import { formatMoney, formatRate } from '@/lib/decimal';
import type { Obligation } from '@/types';

/**
 * One obligation in full: what it costs to carry, what a better rate is worth,
 * and where the break-even sits in both directions.
 */
export default function ObligationDetail({
  obligation,
  onChanged,
  onClose,
}: {
  obligation: Obligation;
  onChanged: () => void;
  onClose: () => void;
}) {
  const [funding, setFunding] = useState('');

  const addFunding = useMutation({
    mutationFn: (amount: string) =>
      api.post(`obligations/${obligation.id}/funding`, { amount_nzd: amount }),
    onSuccess: () => {
      setFunding('');
      onChanged();
    },
  });

  const complete = useMutation({
    mutationFn: () => api.post(`obligations/${obligation.id}/complete`),
    onSuccess: onChanged,
  });

  const archive = useMutation({
    mutationFn: () => api.post(`obligations/${obligation.id}/archive`),
    onSuccess: () => {
      onChanged();
      onClose();
    },
  });

  const net = (days: number) =>
    obligation.waiting.find((row) => row.days === days)?.net_benefit_nzd ?? null;

  return (
    <Card title={obligation.name} subtitle={obligation.reason}>
      {obligation.warnings.map((warning) => (
        <Banner key={warning} tone="warning">
          {warning}
        </Banner>
      ))}

      <div className="fx-grid">
        <Stat label="Remaining" value={formatMoney(obligation.remaining_nzd, 'NZD')} />
        <Stat
          label="USD required now"
          value={
            obligation.usd_required_now
              ? formatMoney(obligation.usd_required_now, 'USD')
              : 'Not known'
          }
          quality={obligation.usd_required_now ? 'estimate' : 'warning'}
          note={obligation.rate_used ? `at ${formatRate(obligation.rate_used)}` : 'no rate'}
        />
        <Stat
          label="Cost of waiting"
          value={
            obligation.has_interest_cost
              ? `${formatMoney(obligation.daily_cost_nzd, 'NZD')} / day`
              : 'None'
          }
          note={
            obligation.has_interest_cost
              ? `${formatMoney(obligation.monthly_cost_nzd, 'NZD')} a month`
              : 'this obligation accrues no interest'
          }
        />
        <Stat
          label="Target rate"
          value={obligation.target_rate ? formatRate(obligation.target_rate) : 'Not set'}
          note={
            obligation.gain_at_target_nzd
              ? `worth ${formatMoney(obligation.gain_at_target_nzd, 'NZD')}`
              : undefined
          }
        />
      </div>

      <h3>What a better rate is worth</h3>
      <div className="fx-grid">
        <Stat
          label="+0.005"
          value={formatMoney(obligation.gain_at_improvement['0.005'] ?? '0', 'NZD')}
          quality="estimate"
          small
        />
        <Stat
          label="+0.01"
          value={formatMoney(obligation.gain_at_improvement['0.01'] ?? '0', 'NZD')}
          quality="estimate"
          small
        />
        <Stat
          label="Break-even at +0.01"
          value={
            obligation.break_even_days_at_improvement['0.01']
              ? `${Number(obligation.break_even_days_at_improvement['0.01']).toFixed(0)} days`
              : 'No interest cost'
          }
          small
          note={
            obligation.has_interest_cost
              ? 'how long that improvement pays for'
              : 'there is no financial break-even period'
          }
        />
      </div>

      <h3>Net benefit of waiting</h3>
      {obligation.target_rate === null ? (
        <Banner tone="info">
          No target rate is set for this obligation, so there is nothing to weigh the cost of
          waiting against.
        </Banner>
      ) : (
        <div className="fx-grid">
          {[7, 30, 60].map((days) => {
            const value = net(days);
            return (
              <Stat
                key={days}
                label={`After ${days} days`}
                value={value === null ? '—' : formatMoney(value, 'NZD')}
                quality={value !== null && value.startsWith('-') ? 'warning' : 'estimate'}
                small
              />
            );
          })}
        </div>
      )}

      <h3>Rate needed to justify waiting</h3>
      <div className="fx-grid">
        {Object.entries(obligation.break_even_rate_after).map(([days, value]) => (
          <Stat
            key={days}
            label={`After ${days} days`}
            value={value ? formatRate(value) : '—'}
            small
          />
        ))}
      </div>

      <h3>Why it ranks where it does</h3>
      <div className="fx-table-wrap">
        <table className="fx-table">
          <thead>
            <tr>
              <th className="fx-left">Component</th>
              <th>Points</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(obligation.priority_components).map(([key, value]) => (
              <tr key={key}>
                <td className="fx-left">{key.replace(/_/g, ' ')}</td>
                <td>{Number(value).toFixed(2)}</td>
              </tr>
            ))}
            <tr>
              <td className="fx-left">
                <strong>Financial priority</strong>
              </td>
              <td>
                <strong>
                  {Number(obligation.financial_score).toFixed(2)} (rank{' '}
                  {obligation.financial_rank})
                </strong>
              </td>
            </tr>
            <tr>
              <td className="fx-left">
                <strong>Overall priority</strong>
              </td>
              <td>
                <strong>
                  {Number(obligation.overall_score).toFixed(2)} (rank {obligation.overall_rank})
                </strong>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="fx-card-subtitle">
        Financial priority counts cost, size and the date. Overall priority adds the priority you
        set and the non-financial importance — which is why an interest-free family loan can
        outrank a mortgage.
      </p>

      <Field label="Record funding" hint="NZD applied to this obligation" htmlFor="funding-amount">
        <input
          id="funding-amount"
          type="text"
          inputMode="decimal"
          value={funding}
          onChange={(event) => setFunding(event.target.value)}
        />
      </Field>

      <div className="fx-toolbar">
        <button
          type="button"
          className="is-primary"
          disabled={!funding || addFunding.isPending}
          onClick={() => addFunding.mutate(funding)}
        >
          Record funding
        </button>
        <button type="button" disabled={complete.isPending} onClick={() => complete.mutate()}>
          Mark funded
        </button>
        <button type="button" disabled={archive.isPending} onClick={() => archive.mutate()}>
          Archive
        </button>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>

      <Banner tone="info">{obligation.disclaimer}</Banner>
    </Card>
  );
}
