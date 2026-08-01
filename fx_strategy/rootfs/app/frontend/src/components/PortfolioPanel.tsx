import { Card, Stat, Tag } from '@/components/ui';
import { formatDecimal, formatMoney, formatRate, toChartNumber } from '@/lib/decimal';
import type { StrategySummary } from '@/types';

/**
 * The portfolio and opportunity panels.
 *
 * Every figure states whether it is gross, an estimate or an actual result, and
 * a missing fee shows as "Fee not included" rather than as a zero.
 */
export default function PortfolioPanel({
  summary,
  ratePlaces,
}: {
  summary: StrategySummary;
  ratePlaces: number;
}) {
  const source = summary.strategy.source_currency;
  const target = summary.strategy.target_currency;
  const percent = toChartNumber(summary.percent_converted ?? '0');
  const convertNow = summary.convert_all_now;

  return (
    <>
      <Card title="Position">
        <div className="fx-grid">
          <Stat
            label={`${source} total`}
            value={formatMoney(summary.initial_source_amount, source)}
            small
          />
          <Stat
            label={`${source} available`}
            value={formatMoney(summary.available_source_amount, source)}
            note={
              summary.available_source_amount !== summary.initial_source_amount
                ? 'Exposure figures use the amount that has actually arrived.'
                : undefined
            }
            small
          />
          <Stat
            label={`${source} converted`}
            value={formatMoney(summary.converted_source_amount, source)}
            quality="actual"
            small
          />
          <Stat
            label={`${source} remaining`}
            value={formatMoney(summary.remaining_source_amount, source)}
            small
          />
          <div className="fx-stat">
            <div className="fx-stat-label">Converted</div>
            <div className="fx-stat-value is-small">
              {formatDecimal(summary.percent_converted, { places: 1 })}%
            </div>
            <div
              className="fx-progress"
              role="progressbar"
              aria-valuenow={Number.isFinite(percent) ? Math.round(percent) : 0}
              aria-valuemin={0}
              aria-valuemax={100}
              style={{ marginTop: 8 }}
            >
              <span style={{ width: `${Math.min(Math.max(percent, 0), 100)}%` }} />
            </div>
          </div>
          <Stat
            label={`${target} received (gross)`}
            value={formatMoney(summary.gross_target_received, target)}
            quality="gross"
            small
          />
          <Stat
            label={`${target} received (net)`}
            value={formatMoney(summary.net_target_received, target)}
            quality="actual"
            note="As recorded from your statements."
            small
          />
          <Stat
            label="Fees paid"
            value={
              summary.total_fees === null
                ? 'Not recorded'
                : formatMoney(summary.total_fees, target)
            }
            quality={summary.total_fees === null ? 'plain' : 'actual'}
            small
          />
          <Stat
            label="Blended effective rate"
            value={formatRate(summary.blended_effective_rate, ratePlaces)}
            note={
              summary.blended_gross_rate
                ? `Gross ${formatRate(summary.blended_gross_rate, ratePlaces)}`
                : 'No conversions recorded yet.'
            }
            quality={summary.blended_effective_rate ? 'actual' : 'plain'}
            small
          />
        </div>
      </Card>

      <Card
        title="If you converted everything now"
        subtitle={
          convertNow
            ? `At ${formatRate(convertNow.rate, ratePlaces)}, on the ${formatMoney(
                summary.remaining_source_amount,
                source,
              )} still unconverted.`
            : 'A current rate is needed to calculate this.'
        }
      >
        {convertNow ? (
          <div className="fx-grid">
            <Stat
              label={`${target} gross`}
              value={formatMoney(convertNow.gross_target_amount, target)}
              quality="gross"
            />
            <Stat
              label="Estimated fee"
              value={
                convertNow.fee.available
                  ? formatMoney(convertNow.fee.amount_target_currency, target)
                  : 'Fee not included'
              }
              note={convertNow.fee.basis}
              quality={convertNow.fee.available ? 'estimate' : 'warning'}
              small
            />
            <Stat
              label={`${target} net`}
              value={
                convertNow.net_target_amount === null
                  ? 'Not calculable'
                  : formatMoney(convertNow.net_target_amount, target)
              }
              note={
                convertNow.net_target_amount === null
                  ? 'Configure a fee model to see net proceeds.'
                  : `Effective rate ${formatRate(convertNow.effective_rate, ratePlaces)}`
              }
              quality={convertNow.net_target_amount === null ? 'warning' : 'estimate'}
            />
            <Stat
              label="Cost of a 1-cent reversal"
              value={formatMoney(summary.one_cent_exposure, target)}
              note="On the amount still unconverted."
            />
            {summary.next_target_rate && (
              <Stat
                label="Value of reaching the next target"
                value={formatMoney(summary.next_target_upside, target)}
                note={`Next target ${formatRate(summary.next_target_rate, ratePlaces)} on ${formatMoney(
                  summary.next_target_source_amount,
                  source,
                )}`}
                quality="estimate"
                small
              />
            )}
          </div>
        ) : (
          <p className="fx-loading">
            No rate is available. Refresh a provider or enter a rate manually.
          </p>
        )}
        {summary.rate_zone && (
          <p className="fx-stat-note" style={{ marginTop: 12 }}>
            <Tag quality="plain">{summary.rate_zone.label}</Tag> {summary.rate_zone.guidance} These
            labels are your own configuration, not a prediction.
          </p>
        )}
      </Card>
    </>
  );
}
