import { Banner, Card, Stat } from '@/components/ui';
import { formatDecimal, formatMoney, formatRate } from '@/lib/decimal';
import type { StrategySummary } from '@/types';

const SEVERITY_TONE: Record<string, 'info' | 'warning' | 'error'> = {
  normal: 'info',
  notice: 'info',
  review: 'warning',
  warning: 'warning',
  critical: 'error',
  overdue: 'error',
};

export default function RiskPanel({
  summary,
  ratePlaces,
}: {
  summary: StrategySummary;
  ratePlaces: number;
}) {
  const target = summary.strategy.target_currency;
  const source = summary.strategy.source_currency;
  const byMovement = Object.fromEntries(
    summary.sensitivity.map((row) => [formatDecimal(row.movement, { places: 4 }), row]),
  );
  const walkAway = summary.walk_away;

  return (
    <>
      <Card
        title="Exposure"
        subtitle={`On the ${formatMoney(summary.remaining_source_amount, source)} still unconverted.`}
      >
        <div className="fx-grid">
          <Stat
            label="One cent"
            value={formatMoney(summary.one_cent_exposure, target)}
            note="What a 0.0100 move is worth, either way."
          />
          <Stat
            label="Three cents down"
            value={formatMoney(byMovement['0.0300']?.downside, target)}
          />
          <Stat
            label="Five cents down"
            value={formatMoney(byMovement['0.0500']?.downside, target)}
          />
          <Stat
            label="Upside to the next target"
            value={formatMoney(summary.next_target_upside, target)}
            quality="estimate"
            note={
              summary.next_target_rate
                ? `Target ${formatRate(summary.next_target_rate, ratePlaces)}`
                : 'No open targets.'
            }
            small
          />
          <Stat
            label="Upside to the highest target"
            value={formatMoney(walkAway.difference_versus_waiting, target)}
            quality="estimate"
            note={
              walkAway.highest_outstanding_target
                ? `Highest ${formatRate(walkAway.highest_outstanding_target, ratePlaces)}`
                : undefined
            }
            small
          />
          <Stat
            label="Days until the deadline"
            value={summary.days_to_deadline === null ? 'No deadline' : summary.days_to_deadline}
            note={summary.deadline_message}
            small
          />
        </div>

        <div className="fx-table-wrap" style={{ marginTop: 'var(--fx-gap)' }}>
          <table className="fx-table">
            <thead>
              <tr>
                <th className="fx-left">Rate movement</th>
                <th>If the rate falls</th>
                <th>If the rate rises</th>
              </tr>
            </thead>
            <tbody>
              {summary.sensitivity.map((row) => (
                <tr key={row.movement}>
                  <td className="fx-left">
                    {formatDecimal(row.movement, { places: 4, grouping: false })}
                  </td>
                  <td>{formatDecimal(row.downside)}</td>
                  <td>{formatDecimal(row.upside, { signed: true })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {summary.days_to_deadline !== null && summary.deadline_severity !== 'normal' && (
        <Banner tone={SEVERITY_TONE[summary.deadline_severity] ?? 'info'}>
          {summary.deadline_message} {summary.days_to_deadline} day
          {summary.days_to_deadline === 1 ? '' : 's'} remain, with{' '}
          {formatMoney(summary.remaining_source_amount, source)} unconverted. This is a reminder
          only — no target has been changed and nothing has been converted.
        </Banner>
      )}

      {summary.requirements.length > 0 && (
        <Card title="Dated requirements">
          <div className="fx-table-wrap">
            <table className="fx-table">
              <thead>
                <tr>
                  <th className="fx-left">Due</th>
                  <th className="fx-left">Description</th>
                  <th>Required {source}</th>
                  <th>Still to convert</th>
                  <th>Days left</th>
                </tr>
              </thead>
              <tbody>
                {summary.requirements.map((row) => (
                  <tr key={row.requirement.id}>
                    <td className="fx-left">{row.requirement.due_date.slice(0, 10)}</td>
                    <td className="fx-left">{row.requirement.description || '—'}</td>
                    <td>{formatDecimal(row.required_source_amount)}</td>
                    <td>
                      {formatDecimal(row.shortfall)}
                      {row.overdue && ' (overdue)'}
                    </td>
                    <td>{row.days_remaining ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {walkAway.walk_away_rate && (
        <Card
          title="Walk-away analysis"
          subtitle={`Your walk-away rate is ${formatRate(walkAway.walk_away_rate, ratePlaces)}.`}
        >
          {walkAway.reached && (
            <Banner tone="info">
              The walk-away rate has been reached. Below is what converting the remainder now would
              produce, and what waiting would add — and risk.
            </Banner>
          )}
          <div className="fx-grid">
            <Stat
              label={`${source} remaining`}
              value={formatMoney(walkAway.remaining_source_amount, source)}
              small
            />
            <Stat
              label="Net if converted now"
              value={
                walkAway.convert_now?.net_target_amount
                  ? formatMoney(walkAway.convert_now.net_target_amount, target)
                  : formatMoney(walkAway.convert_now?.gross_target_amount ?? null, target)
              }
              quality={walkAway.convert_now?.net_target_amount ? 'estimate' : 'gross'}
              small
            />
            <Stat
              label="Blended rate now"
              value={formatRate(walkAway.existing_blended_rate, ratePlaces)}
              note="From conversions already recorded."
              small
            />
            <Stat
              label="Blended rate if you finish now"
              value={formatRate(walkAway.blended_if_converted_now, ratePlaces)}
              quality="estimate"
              small
            />
            <Stat
              label="Waiting for the highest target adds"
              value={formatMoney(walkAway.difference_versus_waiting, target)}
              note={`…and leaves ${formatMoney(walkAway.remaining_source_amount, source)} exposed meanwhile.`}
              quality="estimate"
              small
            />
            <Stat
              label="Movement needed for the next target"
              value={formatDecimal(walkAway.rate_movement_to_next_target, {
                places: ratePlaces,
                grouping: false,
                signed: true,
              })}
              small
            />
          </div>
        </Card>
      )}
    </>
  );
}
