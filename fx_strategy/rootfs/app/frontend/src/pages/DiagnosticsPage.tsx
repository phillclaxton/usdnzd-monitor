import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Banner, Card, Field, Loading, Tag } from '@/components/ui';
import { useSettings } from '@/hooks/useSettings';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/datetime';
import type { Diagnostics, SimulationStatus } from '@/types';

export default function DiagnosticsPage() {
  const queryClient = useQueryClient();
  const settings = useSettings();
  const timezone = settings.data?.general.timezone ?? 'Pacific/Auckland';
  const [replayRates, setReplayRates] = useState('1.7400, 1.7550, 1.7610, 1.7620');
  const [message, setMessage] = useState<string | null>(null);
  const restoreInput = useRef<HTMLInputElement>(null);

  const diagnostics = useQuery({
    queryKey: ['diagnostics'],
    queryFn: () => api.get<Diagnostics>('diagnostics'),
    refetchInterval: 60_000,
  });

  const simulation = useQuery({
    queryKey: ['simulation'],
    queryFn: () => api.get<SimulationStatus>('simulation'),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries();
  };

  const toggleSimulation = useMutation({
    mutationFn: (enabled: boolean) => api.put<SimulationStatus>('simulation', { enabled }),
    onSuccess: invalidate,
  });

  const replay = useMutation({
    mutationFn: (rates: string[]) =>
      api.post<{ steps: number; notifications: number; final_rate: string | null }>(
        'simulation/replay',
        { rates, seconds_between: 60 },
      ),
    onSuccess: (result) => {
      setMessage(
        `Replayed ${result.steps} rate(s), producing ${result.notifications} notification(s). ` +
          `Final rate ${result.final_rate ?? '—'}.`,
      );
      invalidate();
    },
  });

  const reset = useMutation({
    mutationFn: () => api.post<{ message: string }>('simulation/reset'),
    onSuccess: (result) => {
      setMessage(result.message);
      invalidate();
    },
  });

  const integrity = useMutation({
    mutationFn: () =>
      api.post<{ ok: boolean; problems: string[]; note: string }>(
        'diagnostics/integrity-check',
      ),
    onSuccess: (result) =>
      setMessage(
        result.ok
          ? 'Database integrity check passed.'
          : `Problems reported: ${result.problems.join('; ')}. ${result.note}`,
      ),
  });

  const restore = useMutation({
    mutationFn: async ({ file, replace }: { file: File; replace: boolean }) => {
      const body = new FormData();
      body.append('file', file);
      const response = await fetch(api.url(`restore?replace=${replace}`), {
        method: 'POST',
        body,
        credentials: 'same-origin',
      });
      const payload = (await response.json()) as {
        message?: string;
        error?: { message?: string };
      };
      if (!response.ok) throw new Error(payload.error?.message ?? 'Restore failed.');
      return payload as { message: string };
    },
    onSuccess: (result) => {
      setMessage(result.message);
      invalidate();
    },
  });

  if (diagnostics.isLoading) return <Loading label="Gathering diagnostics…" />;
  const data = diagnostics.data;

  return (
    <>
      {message && <Banner tone="info">{message}</Banner>}
      {restore.isError && <Banner tone="error">{(restore.error as Error).message}</Banner>}
      {replay.isError && <Banner tone="error">{(replay.error as Error).message}</Banner>}

      <Card
        title="Simulation"
        subtitle="Exercise the whole workflow — targets, notifications, deadlines — without touching live data."
      >
        {simulation.data?.enabled && <Banner tone="simulation">{simulation.data.banner}</Banner>}
        <div className="fx-inline">
          <input
            id="sim-enabled"
            type="checkbox"
            checked={simulation.data?.enabled ?? false}
            onChange={(event) => toggleSimulation.mutate(event.target.checked)}
          />
          <label htmlFor="sim-enabled">Simulation mode</label>
        </div>

        <Field
          label="Replay a rate series"
          hint="comma-separated; spaced 60 simulated seconds apart so the confirmation rules apply"
          htmlFor="replay"
        >
          <input
            id="replay"
            type="text"
            value={replayRates}
            onChange={(event) => setReplayRates(event.target.value)}
          />
        </Field>
        <div className="fx-toolbar">
          <button
            type="button"
            disabled={!simulation.data?.enabled || replay.isPending}
            onClick={() =>
              replay.mutate(
                replayRates
                  .split(',')
                  .map((value) => value.trim())
                  .filter(Boolean),
              )
            }
          >
            {replay.isPending ? 'Replaying…' : 'Replay'}
          </button>
          <button
            type="button"
            className="is-danger"
            onClick={() => {
              if (
                window.confirm(
                  'Delete every simulated rate and conversion? Real records are not touched.',
                )
              )
                reset.mutate();
            }}
          >
            Reset simulated data
          </button>
        </div>
        {simulation.data && (
          <p className="fx-stat-note">
            {simulation.data.simulated_samples} simulated sample(s) ·{' '}
            {simulation.data.simulated_conversions} simulated conversion(s)
          </p>
        )}
      </Card>

      <Card
        title="Backup and restore"
        subtitle="A backup contains everything except your API credentials."
        actions={
          <a href={api.url('backup')} download className="fx-tag" style={{ textDecoration: 'none' }}>
            Download backup
          </a>
        }
      >
        <input ref={restoreInput} type="file" accept=".json,application/json" aria-label="Backup file" />
        <div className="fx-toolbar" style={{ marginTop: 8 }}>
          <button
            type="button"
            className="is-danger"
            disabled={restore.isPending}
            onClick={() => {
              const file = restoreInput.current?.files?.[0];
              if (!file) return;
              if (
                window.confirm(
                  'Restoring replaces every strategy, conversion and rate in this installation. Continue?',
                )
              ) {
                restore.mutate({ file, replace: true });
              }
            }}
          >
            Restore (replaces existing data)
          </button>
        </div>
        <p className="fx-stat-note">
          Home Assistant&apos;s own backups already include this app&apos;s <code>/data</code>{' '}
          directory. This export is for moving between installations.
        </p>
      </Card>

      {data && (
        <>
          <Card title="Application">
            <div className="fx-grid">
              <div className="fx-stat">
                <div className="fx-stat-label">Version</div>
                <div className="fx-stat-value is-small">{data.app.version}</div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Architecture</div>
                <div className="fx-stat-value is-small">{data.app.architecture}</div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Python</div>
                <div className="fx-stat-value is-small">{data.app.python}</div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Database size</div>
                <div className="fx-stat-value is-small">
                  {(data.database.size_bytes / 1024).toFixed(0)} kB
                </div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Scheduler</div>
                <div className="fx-stat-value is-small">
                  {data.scheduler.running ? 'Running' : 'Stopped'}
                </div>
                <div className="fx-stat-note">
                  Last run {formatDateTime(data.scheduler.last_run_at, timezone)}
                </div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">MQTT</div>
                <div className="fx-stat-value is-small">
                  {data.mqtt.mqtt_connected
                    ? 'Connected'
                    : data.mqtt.mqtt_configured
                      ? 'Configured, not connected'
                      : 'Not configured'}
                </div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Home Assistant</div>
                <div className="fx-stat-value is-small">
                  {data.home_assistant.available ? 'Connected' : 'Unavailable'}
                </div>
                <div className="fx-stat-note">{data.home_assistant.message}</div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Secrets file mode</div>
                <div className="fx-stat-value is-small">
                  {data.secrets_file_mode ?? 'no secrets stored'}
                </div>
              </div>
            </div>

            {data.rates.clock_warning && (
              <Banner tone="warning">{data.rates.clock_warning}</Banner>
            )}
            {data.database.integrity_problems.length > 0 && (
              <Banner tone="error">
                Database integrity problems: {data.database.integrity_problems.join('; ')}
              </Banner>
            )}

            <div className="fx-toolbar" style={{ marginTop: 'var(--fx-gap)' }}>
              <button type="button" onClick={() => integrity.mutate()}>
                Run integrity check
              </button>
              <a
                href={api.url('diagnostics/bundle')}
                download
                className="fx-tag"
                style={{ textDecoration: 'none', padding: '10px 14px' }}
              >
                Download diagnostics bundle
              </a>
            </div>
            <p className="fx-stat-note">{data.note}</p>
          </Card>

          <Card title="Rate providers">
            <div className="fx-table-wrap">
              <table className="fx-table">
                <thead>
                  <tr>
                    <th className="fx-left">Provider</th>
                    <th className="fx-left">State</th>
                    <th>Failures</th>
                    <th>Latency</th>
                    <th className="fx-left">Last success</th>
                    <th className="fx-left">Last error</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rates.providers.map((provider) => (
                    <tr key={provider.provider}>
                      <td className="fx-left">{provider.provider}</td>
                      <td className="fx-left">
                        {provider.healthy ? (
                          <Tag quality="actual">Healthy</Tag>
                        ) : (
                          <Tag quality="warning">Failing</Tag>
                        )}
                      </td>
                      <td>{provider.consecutive_failures}</td>
                      <td>{provider.last_latency_ms ?? '—'} ms</td>
                      <td className="fx-left">
                        {formatDateTime(provider.last_success_at, timezone)}
                      </td>
                      <td className="fx-left">{provider.last_error ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Recent log">
            <pre
              style={{
                maxHeight: 300,
                overflow: 'auto',
                fontSize: '0.72rem',
                background: 'var(--fx-surface-raised)',
                padding: 12,
                borderRadius: 8,
                border: '1px solid var(--fx-border)',
              }}
            >
              {data.recent_logs.slice(-60).join('\n') || 'No log lines yet.'}
            </pre>
            <p className="fx-stat-note">
              Credential-shaped values are scrubbed from every log line before it is stored.
            </p>
          </Card>
        </>
      )}
    </>
  );
}
