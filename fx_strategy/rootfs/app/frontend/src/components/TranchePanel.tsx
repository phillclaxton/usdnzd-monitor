import { Card, EmptyState, Tag } from '@/components/ui';
import { useTrancheAction } from '@/hooks/useStrategy';
import { formatDateTime } from '@/lib/datetime';
import { formatDecimal, formatRate, toChartNumber } from '@/lib/decimal';
import type { StrategySummary, TrancheStatus } from '@/types';

const STATUS_LABEL: Record<TrancheStatus, string> = {
  pending: 'Waiting',
  armed: 'Armed',
  target_reached: 'Target reached',
  partially_completed: 'Partly converted',
  completed: 'Converted',
  skipped: 'Skipped',
  cancelled: 'Cancelled',
};

export default function TranchePanel({
  summary,
  ratePlaces,
}: {
  summary: StrategySummary;
  ratePlaces: number;
}) {
  const action = useTrancheAction();
  const source = summary.strategy.source_currency;
  const target = summary.strategy.target_currency;

  if (summary.tranche_progress.length === 0) {
    return (
      <Card title="Tranches">
        <EmptyState glyph="🪜" title="No tranches in this strategy">
          <p>
            This strategy is monitor-only. Add tranches in the editor to set target rates and get
            notified when they are reached.
          </p>
        </EmptyState>
      </Card>
    );
  }

  return (
    <Card
      title="Tranche progress"
      subtitle="A target being reached does not convert anything. Record the conversion once Wise has done it."
    >
      <div className="fx-table-wrap">
        <table className="fx-table">
          <thead>
            <tr>
              <th className="fx-left">#</th>
              <th className="fx-left">Allocation</th>
              <th>{source}</th>
              <th>Target</th>
              <th>Distance</th>
              <th>Gross {target}</th>
              <th>Est. fee</th>
              <th>Net {target}</th>
              <th className="fx-left">Status</th>
              <th className="fx-left">First reached</th>
              <th className="fx-left">Wise reference</th>
              <th className="fx-left">Actions</th>
            </tr>
          </thead>
          <tbody>
            {summary.tranche_progress.map((row) => {
              const tranche = row.tranche;
              const percent = toChartNumber(row.percent_complete ?? '0');
              return (
                <tr key={tranche.id}>
                  <td className="fx-left">{tranche.sequence}</td>
                  <td className="fx-left">
                    {tranche.allocation_type === 'percentage'
                      ? `${formatDecimal(tranche.allocation_value, { places: 2 })}%`
                      : tranche.allocation_type === 'remainder'
                        ? 'Remainder'
                        : formatDecimal(tranche.allocation_value)}
                  </td>
                  <td>{formatDecimal(tranche.calculated_source_amount)}</td>
                  <td>{formatRate(tranche.target_rate, ratePlaces)}</td>
                  <td>
                    {row.distance_to_target === null ? (
                      '—'
                    ) : (
                      <span>
                        {formatDecimal(row.distance_to_target, {
                          places: ratePlaces,
                          grouping: false,
                          signed: true,
                        })}
                        {row.target_reached_now && (
                          <>
                            {' '}
                            <Tag quality="actual">At or above</Tag>
                          </>
                        )}
                      </span>
                    )}
                  </td>
                  <td>{formatDecimal(row.estimated_gross)}</td>
                  <td>
                    {row.estimated_fee.available
                      ? formatDecimal(row.estimated_fee.amount_target_currency)
                      : 'Not included'}
                  </td>
                  <td>{row.estimated_net === null ? '—' : formatDecimal(row.estimated_net)}</td>
                  <td className="fx-left">
                    <div>{STATUS_LABEL[tranche.status]}</div>
                    <div className="fx-progress" style={{ marginTop: 4 }}>
                      <span style={{ width: `${Math.min(Math.max(percent, 0), 100)}%` }} />
                    </div>
                  </td>
                  <td className="fx-left">
                    {formatDateTime(tranche.target_first_reached_at, summary.strategy.timezone)}
                  </td>
                  <td className="fx-left">{tranche.wise_auto_conversion_reference ?? '—'}</td>
                  <td className="fx-left">
                    <div className="fx-inline">
                      <button
                        type="button"
                        onClick={() => action.mutate({ id: tranche.id, action: 'acknowledge' })}
                        disabled={action.isPending}
                        title="Stop repeat alerts for this target without marking it converted"
                      >
                        Acknowledge
                      </button>
                      {tranche.status !== 'skipped' && (
                        <button
                          type="button"
                          onClick={() => action.mutate({ id: tranche.id, action: 'skip' })}
                          disabled={action.isPending}
                        >
                          Skip
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
