import { useState } from 'react';

import { Banner, Card, Field, Loading } from '@/components/ui';
import { useSettings, useUpdateSettings } from '@/hooks/useSettings';

const TIMEZONES = [
  'Pacific/Auckland',
  'Australia/Sydney',
  'Europe/London',
  'America/New_York',
  'UTC',
];

export default function SettingsPage() {
  const settings = useSettings();
  const update = useUpdateSettings();
  const [saved, setSaved] = useState(false);

  if (settings.isLoading || !settings.data) return <Loading label="Loading settings…" />;
  const { general, formatting } = settings.data;

  const save = (patch: Parameters<typeof update.mutate>[0]) => {
    setSaved(false);
    update.mutate(patch, { onSuccess: () => setSaved(true) });
  };

  return (
    <>
      <Banner tone="info">
        This application does not automatically convert or transfer money.
      </Banner>

      {update.isError && <Banner tone="error">{(update.error as Error).message}</Banner>}
      {saved && <Banner tone="info">Settings saved.</Banner>}

      <Card title="General">
        <Field label="Timezone" hint="used for every displayed date and time" htmlFor="tz">
          <select
            id="tz"
            value={general.timezone}
            onChange={(event) => save({ general: { ...general, timezone: event.target.value } })}
          >
            {(TIMEZONES.includes(general.timezone)
              ? TIMEZONES
              : [general.timezone, ...TIMEZONES]
            ).map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Currency pair" hint="rates are shown as target per 1 source">
          <div className="fx-inline">
            <input aria-label="Source currency" value={general.source_currency} readOnly />
            <span aria-hidden="true">→</span>
            <input aria-label="Target currency" value={general.target_currency} readOnly />
          </div>
          <span className="fx-hint">
            The pair is fixed once a strategy exists, so historical records stay comparable.
          </span>
        </Field>
      </Card>

      <Card title="Currency formatting">
        <Field label="Currency decimal places" htmlFor="cdp">
          <input
            id="cdp"
            type="number"
            min={0}
            max={4}
            value={formatting.currency_decimal_places}
            onChange={(event) =>
              save({
                formatting: {
                  ...formatting,
                  currency_decimal_places: Number(event.target.value),
                },
              })
            }
          />
        </Field>
        <Field label="Rate decimal places" htmlFor="rdp">
          <input
            id="rdp"
            type="number"
            min={2}
            max={8}
            value={formatting.rate_decimal_places}
            onChange={(event) =>
              save({
                formatting: { ...formatting, rate_decimal_places: Number(event.target.value) },
              })
            }
          />
        </Field>
      </Card>
    </>
  );
}
