import { useState } from 'react';

import NotificationSettingsPanel from '@/components/NotificationSettings';
import GenericProviderSettingsPanel from '@/components/GenericProviderSettings';
import WiseSettingsPanel from '@/components/WiseSettings';
import { Banner, Card, Field, Loading, Tag } from '@/components/ui';
import { useProviderStatus } from '@/hooks/useRates';
import { useSettings, useUpdateSettings } from '@/hooks/useSettings';
import { formatDateTime } from '@/lib/datetime';
import type { ProviderSettings, Settings } from '@/types';

const TIMEZONES = [
  'Pacific/Auckland',
  'Australia/Sydney',
  'Europe/London',
  'America/New_York',
  'UTC',
];

const PROVIDER_CHOICES = [
  { value: 'manual', label: 'Manual entry only' },
  { value: 'wise', label: 'Wise' },
  { value: 'generic', label: 'Generic API provider' },
];

export default function SettingsPage() {
  const settings = useSettings();
  const providers = useProviderStatus();
  const update = useUpdateSettings();
  const [saved, setSaved] = useState(false);

  if (settings.isLoading || !settings.data) return <Loading label="Loading settings…" />;
  const { general, formatting, providers: providerSettings } = settings.data;

  const save = (patch: Partial<Settings>) => {
    setSaved(false);
    update.mutate(patch, { onSuccess: () => setSaved(true) });
  };

  const saveProviders = (patch: Partial<ProviderSettings>) =>
    save({ providers: { ...providerSettings, ...patch } });

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
                formatting: { ...formatting, currency_decimal_places: Number(event.target.value) },
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

      <Card
        title="Rate providers"
        subtitle="The primary provider is tried first, then the secondary, then the manual fallback."
      >
        <Field label="Primary provider" htmlFor="primary">
          <select
            id="primary"
            value={providerSettings.primary}
            onChange={(event) => saveProviders({ primary: event.target.value })}
          >
            {PROVIDER_CHOICES.map((choice) => (
              <option key={choice.value} value={choice.value}>
                {choice.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Secondary provider" hint="used for fallback and disagreement checks" htmlFor="secondary">
          <select
            id="secondary"
            value={providerSettings.secondary ?? ''}
            onChange={(event) => saveProviders({ secondary: event.target.value || null })}
          >
            <option value="">None</option>
            {PROVIDER_CHOICES.map((choice) => (
              <option key={choice.value} value={choice.value}>
                {choice.label}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Poll interval while the market is active (seconds)"
          hint="minimum 60; respect your provider's plan"
          htmlFor="poll-active"
        >
          <input
            id="poll-active"
            type="number"
            min={60}
            value={providerSettings.poll_seconds_active}
            onChange={(event) =>
              saveProviders({ poll_seconds_active: Number(event.target.value) })
            }
          />
        </Field>
        <Field label="Treat a rate as stale after (seconds)" htmlFor="stale">
          <input
            id="stale"
            type="number"
            min={60}
            value={providerSettings.stale_after_seconds}
            onChange={(event) =>
              saveProviders({ stale_after_seconds: Number(event.target.value) })
            }
          />
        </Field>
        <Field
          label="Provider disagreement threshold"
          hint="a relative difference above this shows a warning and withholds target confirmation"
          htmlFor="disagreement"
        >
          <input
            id="disagreement"
            type="text"
            inputMode="decimal"
            value={providerSettings.disagreement_threshold}
            onChange={(event) =>
              saveProviders({ disagreement_threshold: event.target.value })
            }
          />
        </Field>
      </Card>

      <Card title="Provider status">
        {providers.isLoading && <Loading />}
        <div className="fx-table-wrap">
          <table className="fx-table">
            <thead>
              <tr>
                <th className="fx-left">Provider</th>
                <th className="fx-left">State</th>
                <th>Failures</th>
                <th className="fx-left">Last success</th>
                <th className="fx-left">Detail</th>
              </tr>
            </thead>
            <tbody>
              {(providers.data ?? []).map((provider) => (
                <tr key={provider.provider}>
                  <td className="fx-left">{provider.display_name || provider.provider}</td>
                  <td className="fx-left">
                    {!provider.configured ? (
                      <Tag quality="plain">Not configured</Tag>
                    ) : provider.healthy ? (
                      <Tag quality="actual">Healthy</Tag>
                    ) : (
                      <Tag quality="warning">Failing</Tag>
                    )}
                  </td>
                  <td>{provider.consecutive_failures}</td>
                  <td className="fx-left">
                    {formatDateTime(provider.last_success_at, general.timezone)}
                  </td>
                  <td className="fx-left">{provider.reason || provider.last_error || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <GenericProviderSettingsPanel />

      <WiseSettingsPanel />

      <NotificationSettingsPanel
        settings={settings.data}
        timezone={general.timezone}
        onSave={save}
      />
    </>
  );
}
