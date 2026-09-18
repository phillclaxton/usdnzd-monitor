import { Banner, Card, Field } from '@/components/ui';
import type { FxAlertSettings, Settings } from '@/types';

/**
 * What earns an alert, and how far the rate has to move before it says so again.
 *
 * Separate from the notifications card above it, and the split is deliberate:
 * that one is about *delivery* — which services, quiet hours, the cooldown — and
 * this one is about *whether there is anything worth delivering*. Both gates
 * have to be open for a message to reach a phone.
 *
 * Every rate and money value stays a string all the way to the API. A
 * `type="number"` input hands the browser a float, which is the rounding the
 * backend's Decimal pipeline exists to avoid; the counts are genuinely integers
 * and use number inputs.
 *
 * Text fields save on blur rather than on every keystroke. The settings document
 * lives on the server, so a controlled input would mean one PUT per character
 * and a field that fights whoever is typing into it while the round trip is in
 * flight. A checkbox is one discrete decision, so it saves at once.
 */

/** A list of decimals edited as one line of text, which is how people think of them. */
function decimalList(value: string): string[] {
  return value
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean);
}

export default function FxAlertSettingsPanel({
  settings,
  onSave,
}: {
  settings: Settings;
  onSave: (patch: Partial<Settings>) => void;
}) {
  const alerts = settings.fx_alerts;
  const target = settings.general.target_currency;

  const patch = (changes: Partial<FxAlertSettings>) =>
    onSave({ fx_alerts: { ...alerts, ...changes } });

  return (
    <Card
      title="FX alerts"
      subtitle="What the app says something about. Nothing here tells you what to convert — these decide when a change is worth interrupting you for."
    >
      <div className="fx-inline" style={{ marginBottom: 12 }}>
        <input
          id="fx-alerts-enabled"
          type="checkbox"
          checked={alerts.enabled}
          onChange={(event) => patch({ enabled: event.target.checked })}
        />
        <label htmlFor="fx-alerts-enabled">Alert on movement in the rate and the position</label>
      </div>

      {!alerts.enabled && (
        <Banner tone="warning">
          Every condition below is switched off. The rate is still recorded and the position is
          still valued; you will simply not hear about either.
        </Banner>
      )}

      <Field
        label="Say nothing again until the rate has moved"
        hint="the volume knob — raise this first if the alerts are too talkative"
        htmlFor="fx-minimum-change"
      >
        <input
          id="fx-minimum-change"
          type="text"
          inputMode="decimal"
          defaultValue={alerts.minimum_change_since_last_alert}
          onBlur={(event) => patch({ minimum_change_since_last_alert: event.target.value.trim() })}
        />
      </Field>

      <fieldset style={{ border: '1px solid var(--fx-border)', borderRadius: 8, padding: 12 }}>
        <legend style={{ fontSize: '0.8rem', fontWeight: 600 }}>The rate moving</legend>

        <Field
          label="Absolute move"
          hint="since the last time anything was said — 0.0050 is half a cent"
          htmlFor="fx-absolute-move"
        >
          <input
            id="fx-absolute-move"
            type="text"
            inputMode="decimal"
            defaultValue={alerts.absolute_rate_move}
            onBlur={(event) => patch({ absolute_rate_move: event.target.value.trim() })}
          />
        </Field>

        <Field
          label="Intraday move (%)"
          hint="against the first rate of the day in your timezone, so 0.50 is half a percent"
          htmlFor="fx-intraday"
        >
          <input
            id="fx-intraday"
            type="text"
            inputMode="decimal"
            defaultValue={alerts.intraday_percent_move}
            onBlur={(event) => patch({ intraday_percent_move: event.target.value.trim() })}
          />
        </Field>
      </fieldset>

      <fieldset
        style={{
          border: '1px solid var(--fx-border)',
          borderRadius: 8,
          padding: 12,
          marginTop: 12,
        }}
      >
        <legend style={{ fontSize: '0.8rem', fontWeight: 600 }}>New highs</legend>

        {(
          [
            ['alert_new_7d_high', 'A new 7-day high'],
            ['alert_new_30d_high', 'A new 30-day high'],
            ['alert_new_90d_high', 'A new 90-day high'],
          ] as const
        ).map(([key, label]) => (
          <div className="fx-inline" key={key}>
            <input
              id={`fx-${key}`}
              type="checkbox"
              checked={alerts[key]}
              onChange={(event) => patch({ [key]: event.target.checked })}
            />
            <label htmlFor={`fx-${key}`}>{label}</label>
          </div>
        ))}

        <Field
          label="Quiet for (minutes) after reporting a high"
          hint="unless the rate has also moved another minimum change"
          htmlFor="fx-high-cooldown"
        >
          <input
            id="fx-high-cooldown"
            type="number"
            min={0}
            defaultValue={alerts.new_high_cooldown_minutes}
            onBlur={(event) => patch({ new_high_cooldown_minutes: Number(event.target.value) })}
          />
        </Field>
      </fieldset>

      <fieldset
        style={{
          border: '1px solid var(--fx-border)',
          borderRadius: 8,
          padding: 12,
          marginTop: 12,
        }}
      >
        <legend style={{ fontSize: '0.8rem', fontWeight: 600 }}>Levels</legend>

        <div className="fx-inline">
          <input
            id="fx-round-numbers"
            type="checkbox"
            checked={alerts.alert_round_number_breaks}
            onChange={(event) => patch({ alert_round_number_breaks: event.target.checked })}
          />
          <label htmlFor="fx-round-numbers">Round numbers being broken</label>
        </div>

        <Field
          label="Watch levels"
          hint="comma separated. Crossing one is worth knowing; it is not an instruction to do anything"
          htmlFor="fx-watch-levels"
        >
          <input
            id="fx-watch-levels"
            type="text"
            inputMode="decimal"
            defaultValue={alerts.watch_levels.join(', ')}
            onBlur={(event) => patch({ watch_levels: decimalList(event.target.value) })}
          />
        </Field>
      </fieldset>

      <fieldset
        style={{
          border: '1px solid var(--fx-border)',
          borderRadius: 8,
          padding: 12,
          marginTop: 12,
        }}
      >
        <legend style={{ fontSize: '0.8rem', fontWeight: 600 }}>What the position is worth</legend>

        <Field
          label={`Worth mentioning (${target})`}
          hint="the value of what you still hold changing by this much"
          htmlFor="fx-material-change"
        >
          <input
            id="fx-material-change"
            type="text"
            inputMode="decimal"
            defaultValue={alerts.material_nzd_value_change}
            onBlur={(event) => patch({ material_nzd_value_change: event.target.value.trim() })}
          />
        </Field>

        <Field
          label={`Worth interrupting you for (${target})`}
          hint="the louder threshold"
          htmlFor="fx-secondary-change"
        >
          <input
            id="fx-secondary-change"
            type="text"
            inputMode="decimal"
            defaultValue={alerts.secondary_nzd_value_change}
            onBlur={(event) => patch({ secondary_nzd_value_change: event.target.value.trim() })}
          />
        </Field>
      </fieldset>

      <fieldset
        style={{
          border: '1px solid var(--fx-border)',
          borderRadius: 8,
          padding: 12,
          marginTop: 12,
        }}
      >
        <legend style={{ fontSize: '0.8rem', fontWeight: 600 }}>Your mortgage</legend>

        <Field
          label={`Offset shortfall levels (${target})`}
          hint="comma separated, largest first"
          htmlFor="fx-shortfall-thresholds"
        >
          <input
            id="fx-shortfall-thresholds"
            type="text"
            inputMode="decimal"
            defaultValue={alerts.offset_shortfall_thresholds.join(', ')}
            onBlur={(event) =>
              patch({ offset_shortfall_thresholds: decimalList(event.target.value) })
            }
          />
        </Field>

        <Field
          label={`Daily carrying cost levels (${target})`}
          hint="comma separated — say something when the cost of waiting passes one of these"
          htmlFor="fx-cost-thresholds"
        >
          <input
            id="fx-cost-thresholds"
            type="text"
            inputMode="decimal"
            defaultValue={alerts.daily_cost_thresholds.join(', ')}
            onBlur={(event) => patch({ daily_cost_thresholds: decimalList(event.target.value) })}
          />
        </Field>
      </fieldset>

      <p className="fx-stat-note" style={{ marginTop: 12 }}>
        A condition says nothing the first time it is evaluated: it records where things stand
        instead, so an upgrade or a new setting does not fire everything at once. After that it has
        to have cleared and re-armed, moved at least the minimum change above, and survived the
        confirmation rule and the cooldown in Notifications.
      </p>
    </Card>
  );
}
