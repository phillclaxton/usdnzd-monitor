import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Banner, Card, Field, Loading } from '@/components/ui';
import { api } from '@/lib/api';
import type {
  GenericProviderSettings,
  GenericProviderStatus,
  ProviderPreset,
} from '@/types';

/**
 * Configuration for the vendor-neutral HTTP rate provider.
 *
 * A preset fills the fields in for a known vendor, but every one stays
 * editable: the point of this provider is that it is not tied to the list.
 */
export default function GenericProviderSettingsPanel() {
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState('');
  const [draft, setDraft] = useState<Partial<GenericProviderSettings>>({});

  const presets = useQuery({
    queryKey: ['providers', 'presets'],
    queryFn: () => api.get<ProviderPreset[]>('providers/presets'),
  });

  const generic = useQuery({
    queryKey: ['providers', 'generic'],
    queryFn: () =>
      api.get<{ config: GenericProviderSettings; status: GenericProviderStatus }>(
        'providers/generic',
      ),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['providers'] });
    void queryClient.invalidateQueries({ queryKey: ['settings'] });
    void queryClient.invalidateQueries({ queryKey: ['rates'] });
  };

  const save = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.put('providers/generic', payload),
    onSuccess: () => {
      setApiKey('');
      setDraft({});
      invalidate();
    },
  });

  const usePreset = useMutation({
    mutationFn: (key: string) => api.post(`providers/generic/preset/${key}`),
    onSuccess: () => {
      setDraft({});
      invalidate();
    },
  });

  const removeKey = useMutation({
    mutationFn: () => api.del('providers/generic/credentials'),
    onSuccess: invalidate,
  });

  const test = useMutation({
    mutationFn: () => api.post<GenericProviderStatus>('providers/generic/test'),
    onSuccess: invalidate,
  });

  const config = generic.data?.config;
  const status = generic.data?.status;
  const selectedPreset = presets.data?.find((preset) => preset.key === config?.preset);

  // The form edits a draft so a half-typed URL is never saved on every keystroke.
  const value = <K extends keyof GenericProviderSettings>(key: K): GenericProviderSettings[K] =>
    (draft[key] ?? config?.[key]) as GenericProviderSettings[K];

  const set = (key: keyof GenericProviderSettings, next: unknown) =>
    setDraft((previous) => ({ ...previous, [key]: next }));

  const dirty = Object.keys(draft).length > 0 || apiKey !== '';

  // Nothing is rendered until the configuration has arrived: drawing the form
  // from undefined values shows fields that the saved authentication style
  // does not use, which then vanish a moment later.
  if (!config || !status) {
    return (
      <Card
        title="Generic API provider"
        subtitle="Any JSON rate API. Start from a preset or enter the details yourself."
      >
        <Loading />
      </Card>
    );
  }

  return (
    <Card
      title="Generic API provider"
      subtitle="Any JSON rate API. Start from a preset or enter the details yourself."
    >
      {status && (
        <Banner tone={status.configured ? 'info' : 'warning'}>
          {status.message}
          {status.key_hint && <> · key stored ({status.key_hint})</>}
          {status.supports_history && <> · history supported</>}
        </Banner>
      )}

      {save.isError && <Banner tone="error">{(save.error as Error).message}</Banner>}
      {test.isError && <Banner tone="error">{(test.error as Error).message}</Banner>}
      {test.data && (
        <Banner tone={test.data.rate ? 'info' : 'error'}>{test.data.message}</Banner>
      )}

      <Field
        label="Preset"
        hint="fills in the fields below; you can edit any of them afterwards"
        htmlFor="generic-preset"
      >
        <select
          id="generic-preset"
          value={config?.preset ?? ''}
          disabled={usePreset.isPending}
          onChange={(event) => event.target.value && usePreset.mutate(event.target.value)}
        >
          <option value="">Custom (no preset)</option>
          {(presets.data ?? []).map((preset) => (
            <option key={preset.key} value={preset.key}>
              {preset.display_name}
              {preset.requires_key ? ' — key required' : ' — no key needed'}
            </option>
          ))}
        </select>
        {selectedPreset && <span className="fx-hint">{selectedPreset.notes}</span>}
      </Field>

      <Field label="Display name" htmlFor="generic-name">
        <input
          id="generic-name"
          type="text"
          value={value('display_name') ?? ''}
          onChange={(event) => set('display_name', event.target.value)}
        />
      </Field>

      <Field label="Base URL" hint="e.g. https://api.frankfurter.app" htmlFor="generic-base">
        <input
          id="generic-base"
          type="url"
          placeholder="https://api.example.com"
          value={value('base_url') ?? ''}
          onChange={(event) => set('base_url', event.target.value)}
        />
      </Field>

      <Field label="Rate path" hint="appended to the base URL" htmlFor="generic-rate-path">
        <input
          id="generic-rate-path"
          type="text"
          value={value('rate_path') ?? ''}
          onChange={(event) => set('rate_path', event.target.value)}
        />
      </Field>

      <Field
        label="History path"
        hint="leave empty if the provider has no history endpoint"
        htmlFor="generic-history-path"
      >
        <input
          id="generic-history-path"
          type="text"
          value={value('history_path') ?? ''}
          onChange={(event) => set('history_path', event.target.value)}
        />
      </Field>

      <Field label="Authentication" htmlFor="generic-auth-style">
        <select
          id="generic-auth-style"
          value={value('auth_style') ?? 'header'}
          onChange={(event) => set('auth_style', event.target.value)}
        >
          <option value="header">Key in a header</option>
          <option value="query">Key in a query parameter</option>
          <option value="bearer">Bearer token</option>
          <option value="none">No authentication</option>
        </select>
      </Field>

      {value('auth_style') !== 'bearer' && value('auth_style') !== 'none' && (
        <Field
          label="Key name"
          hint="the header or parameter the key is sent as, e.g. apikey"
          htmlFor="generic-auth-name"
        >
          <input
            id="generic-auth-name"
            type="text"
            value={value('auth_name') ?? ''}
            onChange={(event) => set('auth_name', event.target.value)}
          />
        </Field>
      )}

      {value('auth_style') !== 'none' && (
        <Field
          label="API key"
          hint="stored encrypted outside the database, never shown again"
          htmlFor="generic-key"
        >
          <input
            id="generic-key"
            type="password"
            autoComplete="off"
            placeholder={status?.key_hint || 'Paste your API key'}
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
          />
        </Field>
      )}

      <Field
        label="Source parameter"
        hint="the query parameter naming the source currency"
        htmlFor="generic-source-param"
      >
        <input
          id="generic-source-param"
          type="text"
          value={value('source_param') ?? ''}
          onChange={(event) => set('source_param', event.target.value)}
        />
      </Field>

      <Field
        label="Target parameter"
        hint="leave empty for providers taking a single pair, e.g. symbol=USD/NZD"
        htmlFor="generic-target-param"
      >
        <input
          id="generic-target-param"
          type="text"
          value={value('target_param') ?? ''}
          onChange={(event) => set('target_param', event.target.value)}
        />
      </Field>

      <Field
        label="Rate path in the response"
        hint="dotted path; {target} and {source} expand to the currency codes"
        htmlFor="generic-rate-json"
      >
        <input
          id="generic-rate-json"
          type="text"
          placeholder="rates.{target}"
          value={value('rate_json_path') ?? ''}
          onChange={(event) => set('rate_json_path', event.target.value)}
        />
      </Field>

      <Field
        label="Timestamp path in the response"
        hint="optional; when absent the rate is recorded without a provider timestamp"
        htmlFor="generic-ts-json"
      >
        <input
          id="generic-ts-json"
          type="text"
          value={value('timestamp_json_path') ?? ''}
          onChange={(event) => set('timestamp_json_path', event.target.value)}
        />
      </Field>

      <Field
        label="Quote direction"
        hint="how many target units one source unit buys, or the other way round"
        htmlFor="generic-convention"
      >
        <select
          id="generic-convention"
          value={value('convention') ?? 'target_per_source'}
          onChange={(event) => set('convention', event.target.value)}
        >
          <option value="target_per_source">Target per source (1 USD = x NZD)</option>
          <option value="source_per_target">Source per target (1 NZD = x USD)</option>
        </select>
      </Field>

      <Field
        label="Minimum seconds between calls"
        hint="respect your plan's rate limit"
        htmlFor="generic-min-seconds"
      >
        <input
          id="generic-min-seconds"
          type="number"
          min={1}
          value={value('min_seconds_between_calls') ?? 60}
          onChange={(event) => set('min_seconds_between_calls', Number(event.target.value))}
        />
      </Field>

      <div className="fx-toolbar">
        <button
          type="button"
          className="is-primary"
          disabled={!dirty || save.isPending}
          onClick={() =>
            save.mutate({
              ...draft,
              ...(apiKey ? { api_key: apiKey } : {}),
            })
          }
        >
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          disabled={test.isPending || !status?.base_url}
          onClick={() => test.mutate()}
        >
          {test.isPending ? 'Testing…' : 'Test'}
        </button>
        <button
          type="button"
          disabled={save.isPending}
          onClick={() => save.mutate({ enabled: !config?.enabled })}
        >
          {config?.enabled ? 'Disable' : 'Enable'}
        </button>
        {status?.key_hint && (
          <button type="button" disabled={removeKey.isPending} onClick={() => removeKey.mutate()}>
            Remove key
          </button>
        )}
      </div>

      <p className="fx-card-subtitle">
        Enabling this provider does not select it. Choose it as the primary or secondary
        provider above for it to be used.
      </p>
    </Card>
  );
}
