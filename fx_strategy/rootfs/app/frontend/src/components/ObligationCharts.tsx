import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { Card } from '@/components/ui';
import { toChartNumber } from '@/lib/decimal';
import type { Obligation } from '@/types';

const DAYS = [0, 7, 14, 30, 60, 90];

/**
 * The three comparisons worth seeing at a glance.
 *
 * Charts convert to numbers, which is acceptable here and nowhere else: a pixel
 * position cannot be exact anyway. Every figure shown as text elsewhere comes
 * from the decimal strings untouched.
 */
export default function ObligationCharts({ obligations }: { obligations: Obligation[] }) {
  const live = obligations.filter((row) => !row.completed);
  const interestBearing = live.filter((row) => row.has_interest_cost);

  // Cumulative cost of waiting across the whole book.
  const cumulative = DAYS.map((days) => {
    const total = interestBearing.reduce(
      (sum, row) => sum + toChartNumber(row.daily_cost_nzd) * days,
      0,
    );
    return { days, cost: Math.round(total * 100) / 100 };
  });

  // FX gain against waiting cost, for obligations with a target set.
  const withTarget = live.filter((row) => row.target_rate !== null);
  const comparison = DAYS.filter((d) => d > 0).map((days) => {
    const cost = withTarget.reduce(
      (sum, row) => sum + toChartNumber(row.daily_cost_nzd) * days,
      0,
    );
    const gain = withTarget.reduce(
      (sum, row) => sum + toChartNumber(row.gain_at_target_nzd),
      0,
    );
    return {
      days,
      cost: Math.round(cost * 100) / 100,
      gain: Math.round(gain * 100) / 100,
    };
  });

  // Remaining balance grouped by the priority the user set.
  const byPriority = ['critical', 'high', 'normal', 'low'].map((priority) => ({
    priority: `${priority.charAt(0).toUpperCase()}${priority.slice(1)}`,
    nzd: Math.round(
      live
        .filter((row) => row.priority === priority)
        .reduce((sum, row) => sum + toChartNumber(row.remaining_nzd), 0) * 100,
    ) / 100,
  }));

  return (
    <>
      {interestBearing.length > 0 && (
        <Card
          title="Cost of waiting over time"
          subtitle="Cumulative interest across every obligation that accrues it."
        >
          <div style={{ width: '100%', height: 240 }}>
            <ResponsiveContainer>
              <LineChart data={cumulative} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
                <CartesianGrid stroke="var(--fx-border)" strokeDasharray="3 3" />
                <XAxis dataKey="days" unit="d" stroke="var(--fx-text-muted)" fontSize={12} />
                <YAxis stroke="var(--fx-text-muted)" fontSize={12} width={70} />
                <Tooltip
                  formatter={(value: number) => [`NZ$${value.toLocaleString()}`, 'Cost']}
                  labelFormatter={(days) => `After ${days} days`}
                />
                <Line
                  type="monotone"
                  dataKey="cost"
                  stroke="var(--fx-caution)"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      {withTarget.length > 0 && (
        <Card
          title="FX gain against waiting cost"
          subtitle="Where the lines cross is the point past which waiting stops paying."
        >
          <div style={{ width: '100%', height: 240 }}>
            <ResponsiveContainer>
              <LineChart data={comparison} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
                <CartesianGrid stroke="var(--fx-border)" strokeDasharray="3 3" />
                <XAxis dataKey="days" unit="d" stroke="var(--fx-text-muted)" fontSize={12} />
                <YAxis stroke="var(--fx-text-muted)" fontSize={12} width={70} />
                <Tooltip formatter={(value: number) => `NZ$${value.toLocaleString()}`} />
                <Legend />
                <Line
                  type="monotone"
                  name="Cost of waiting"
                  dataKey="cost"
                  stroke="var(--fx-caution)"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  name="Gain at target"
                  dataKey="gain"
                  stroke="var(--fx-accent)"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      <Card title="Remaining by priority" subtitle="The priority you set, not the computed rank.">
        <div style={{ width: '100%', height: 220 }}>
          <ResponsiveContainer>
            <BarChart data={byPriority} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
              <CartesianGrid stroke="var(--fx-border)" strokeDasharray="3 3" />
              <XAxis dataKey="priority" stroke="var(--fx-text-muted)" fontSize={12} />
              <YAxis stroke="var(--fx-text-muted)" fontSize={12} width={70} />
              <Tooltip formatter={(value: number) => [`NZ$${value.toLocaleString()}`, 'Remaining']} />
              <Bar dataKey="nzd" fill="var(--fx-accent)" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>
    </>
  );
}
