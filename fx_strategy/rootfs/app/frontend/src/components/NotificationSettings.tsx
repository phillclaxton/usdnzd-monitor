import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

import { Banner, Card, Field, Tag } from '@/components/ui';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/datetime';
import type { NotificationLogEntry, NotificationSettings, Settings } from '@/types';

interface HomeAssistantStatus {
  available: boolean;
  message: string;
  notify_services: string[];
  latency_ms: number | null;
  configured_services: string[];
  mqtt_configured: boolean;
}

interface DeliveryResult {
  delivered: boolean;
  queued: boolean;
  suppressed_reason: string | null;
  services: string[];
  errors: Record<string, string>;
}

export default function NotificationSettingsPanel({
  settings,
  timezone,
  onSave,
}: {
  settings: Settings;
  timezone: string;
  onSave: (patch: Partial<Settings>) => void;
}) {
  const notifications = settings.notifications;
  const [testResult, setTestResult] = useState<DeliveryResult | null>(null);

  const status = useQuery({
    queryKey: ['home-assistant', 'status'],
    queryFn: () => api.get<HomeAssistantStatus>('home-assistant/status'),
    refetchInterval: 120_000,
  });

  const history = useQuery({
    queryKey: ['home-assistant', 'notifications'],
    queryFn: () => api.get<NotificationLogEntry[]>('home-assistant/notifications?limit=20'),
  });

  const test = useMutation({
    mutationFn: () =>
      api.post<DeliveryResult>('home-assistant/test-notification', { services: null }),
    onSuccess: (result) => {
      setTestResult(result);
      void history.refetch();
    },
  });

  const patch = (changes: Partial<NotificationSettings>) =>
    onSave({ notifications: { ...notifications, ...changes } });

  const discovered = status.data?.notify_services ?? [];

  return (
    <>
      <Card
        title="Notifications"
        subtitle="Notifications are sent through Home Assistant's own notify services, so they reach whatever you already use."
      >
        {status.data && !status.data.available && (
          <Banner tone="warning">
            Home Assistant is not reachable: {status.data.message} Notifications will be queued
            and retried.
          </Banner>
        )}

        <div className="fx-inline" style={{ marginBottom: 12 }}>
          <input
            id="notify-enabled"
            type="checkbox"
            checked={notifications.enabled}
            onChange={(event) => patch({ enabled: event.target.checked })}
          />
          <label htmlFor="notify-enabled">Send notifications</label>
        </div>

        <Field
          label="Notify services"
          hint="one per line — no device name is hard-coded"
          htmlFor="notify-services"
        >
          <textarea
            id="notify-services"
            rows={3}
            defaultValue={notifications.services.join('\n')}
            onBlur={(event) =>
              patch({
                services: event.target.value
                  .split('\n')
                  .map((line) => line.trim())
                  .filter(Boolean),
              })
            }
          />
          {discovered.length > 0 && (
            <span className="fx-hint">
              Discovered on this installation: {discovered.join(', ')}
            </span>
          )}
        </Field>

        <Field
          label="Alert when within this distance of a target"
          hint="the approaching alert threshold"
          htmlFor="near"
        >
          <input
            id="near"
            type="text"
            inputMode="decimal"
            value={notifications.near_threshold}
            onChange={(event) => patch({ near_threshold: event.target.value })}
          />
        </Field>

        <Field
          label="Reset hysteresis"
          hint="how far the rate must fall below a target before it can alert again"
          htmlFor="hysteresis"
        >
          <input
            id="hysteresis"
            type="text"
            inputMode="decimal"
            value={notifications.reset_hysteresis}
            onChange={(event) => patch({ reset_hysteresis: event.target.value })}
          />
        </Field>

        <Field
          label="Confirmation samples"
          hint="consecutive qualifying samples before a target is confirmed"
          htmlFor="samples"
        >
          <input
            id="samples"
            type="number"
            min={1}
            max={10}
            value={notifications.confirmation_samples}
            onChange={(event) => patch({ confirmation_samples: Number(event.target.value) })}
          />
        </Field>

        <Field label="Cooldown (minutes)" htmlFor="cooldown">
          <input
            id="cooldown"
            type="number"
            min={0}
            value={notifications.default_cooldown_minutes}
            onChange={(event) =>
              patch({ default_cooldown_minutes: Number(event.target.value) })
            }
          />
        </Field>

        <fieldset style={{ border: '1px solid var(--fx-border)', borderRadius: 8, padding: 12 }}>
          <legend style={{ fontSize: '0.8rem', fontWeight: 600 }}>Quiet hours</legend>
          <div className="fx-inline">
            <input
              id="quiet-enabled"
              type="checkbox"
              checked={notifications.quiet_hours.enabled}
              onChange={(event) =>
                patch({
                  quiet_hours: { ...notifications.quiet_hours, enabled: event.target.checked },
                })
              }
            />
            <label htmlFor="quiet-enabled">Hold non-critical alerts overnight</label>
          </div>
          <div className="fx-inline" style={{ marginTop: 8 }}>
            <input
              aria-label="Quiet hours start"
              type="time"
              value={notifications.quiet_hours.start}
              onChange={(event) =>
                patch({
                  quiet_hours: { ...notifications.quiet_hours, start: event.target.value },
                })
              }
            />
            <span>to</span>
            <input
              aria-label="Quiet hours end"
              type="time"
              value={notifications.quiet_hours.end}
              onChange={(event) =>
                patch({ quiet_hours: { ...notifications.quiet_hours, end: event.target.value } })
              }
            />
          </div>
          <div className="fx-inline" style={{ marginTop: 8 }}>
            <input
              id="quiet-critical"
              type="checkbox"
              checked={notifications.quiet_hours.allow_critical}
              onChange={(event) =>
                patch({
                  quiet_hours: {
                    ...notifications.quiet_hours,
                    allow_critical: event.target.checked,
                  },
                })
              }
            />
            <label htmlFor="quiet-critical">
              Still send critical alerts (missed deadline, provider down)
            </label>
          </div>
        </fieldset>

        <div className="fx-toolbar" style={{ marginTop: 'var(--fx-gap)' }}>
          <button type="button" onClick={() => test.mutate()} disabled={test.isPending}>
            {test.isPending ? 'Sending…' : 'Send a test notification'}
          </button>
        </div>

        {testResult && (
          <Banner tone={testResult.delivered ? 'info' : 'warning'}>
            {testResult.delivered
              ? `Test notification sent to ${testResult.services.join(', ')}.`
              : `Not delivered: ${testResult.suppressed_reason ?? 'unknown reason'}${
                  Object.keys(testResult.errors).length
                    ? ` — ${Object.entries(testResult.errors)
                        .map(([service, error]) => `${service}: ${error}`)
                        .join('; ')}`
                    : ''
                }`}
          </Banner>
        )}
      </Card>

      <Card title="Recent notifications">
        {(history.data ?? []).length === 0 ? (
          <p className="fx-loading">Nothing has been sent yet.</p>
        ) : (
          <div className="fx-table-wrap">
            <table className="fx-table">
              <thead>
                <tr>
                  <th className="fx-left">When</th>
                  <th className="fx-left">Type</th>
                  <th className="fx-left">Title</th>
                  <th className="fx-left">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {(history.data ?? []).map((entry) => (
                  <tr key={entry.id}>
                    <td className="fx-left">{formatDateTime(entry.created_at, timezone)}</td>
                    <td className="fx-left">{entry.rule_type}</td>
                    <td className="fx-left">{entry.title}</td>
                    <td className="fx-left">
                      {entry.delivered ? (
                        <Tag quality="actual">Delivered</Tag>
                      ) : entry.queued ? (
                        <Tag quality="estimate">Queued</Tag>
                      ) : (
                        <Tag quality="warning">
                          {entry.suppressed_reason ?? 'Not delivered'}
                        </Tag>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
