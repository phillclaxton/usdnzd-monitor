import { formatDecimal, formatQuote } from '@/lib/decimal';
import { formatAge, formatDateTime } from '@/lib/datetime';
import type { CurrentRate, RateStatus } from '@/types';
import { Banner, Card, Stat } from './ui';

/**
 * Status is carried by a word and a glyph as well as a colour, so the header
 * still reads correctly for someone who cannot distinguish the colours.
 */
const STATUS: Record<RateStatus, { label: string; glyph: string; tone: string }> = {
  live: { label: 'Live', glyph: '●', tone: 'var(--fx-positive)' },
  delayed: { label: 'Delayed', glyph: '◐', tone: 'var(--fx-caution)' },
  stale: { label: 'Stale', glyph: '○', tone: 'var(--fx-negative)' },
  unavailable: { label: 'No data', glyph: '×', tone: 'var(--fx-text-muted)' },
};

function Change({ label, value }: { label: string; value: string | null }) {
  const formatted = formatDecimal(value, { places: 4, grouping: false, signed: true });
  const direction = value === null ? '' : value.startsWith('-') ? '▼' : '▲';
  return (
    <div className="fx-stat">
      <div className="fx-stat-label">{label}</div>
      <div className="fx-stat-value is-small">
        <span aria-hidden="true">{value === null ? '' : `${direction} `}</span>
        {formatted}
      </div>
    </div>
  );
}

export default function RateHeader({
  rate,
  timezone,
  ratePlaces,
}: {
  rate: CurrentRate;
  timezone: string;
  ratePlaces: number;
}) {
  const status = STATUS[rate.status];

  return (
    <Card
      title="Current rate"
      subtitle={`${rate.quote_label ?? 'Rate'} from ${rate.provider || 'no provider yet'}`}
    >
      {rate.status === 'stale' && (
        <Banner tone="warning">
          This rate is {formatAge(rate.age_seconds)} and is treated as stale. Targets will not
          trigger from it.
        </Banner>
      )}
      {rate.disagreement_warning && <Banner tone="warning">{rate.disagreement_warning}</Banner>}
      {rate.message && <Banner tone="info">{rate.message}</Banner>}

      <div className="fx-grid">
        <div className="fx-stat">
          <div className="fx-stat-label">
            <span>Exchange rate</span>
            <span style={{ color: status.tone }}>
              <span aria-hidden="true">{status.glyph} </span>
              {status.label}
            </span>
          </div>
          <div className="fx-stat-value">
            {formatQuote(rate.rate, rate.source_currency, rate.target_currency, ratePlaces)}
          </div>
          <div className="fx-stat-note">
            {rate.retrieved_at
              ? `Retrieved ${formatAge(rate.age_seconds)} · ${formatDateTime(rate.retrieved_at, timezone)}`
              : 'Never retrieved'}
            {rate.provider_timestamp && (
              <>
                <br />
                Provider timestamp: {formatDateTime(rate.provider_timestamp, timezone)}
              </>
            )}
          </div>
        </div>

        <Change label="1 hour" value={rate.changes.one_hour} />
        <Change label="24 hours" value={rate.changes.twenty_four_hours} />
        <Change label="7 days" value={rate.changes.seven_days} />
        <Change label="30 days" value={rate.changes.thirty_days} />

        <Stat
          label="24-hour range"
          small
          value={`${formatDecimal(rate.low_24h, { places: ratePlaces, grouping: false })} – ${formatDecimal(
            rate.high_24h,
            { places: ratePlaces, grouping: false },
          )}`}
        />
        <Stat
          label="6-month range"
          small
          value={`${formatDecimal(rate.low_6m, { places: ratePlaces, grouping: false })} – ${formatDecimal(
            rate.high_6m,
            { places: ratePlaces, grouping: false },
          )}`}
        />
      </div>
    </Card>
  );
}
