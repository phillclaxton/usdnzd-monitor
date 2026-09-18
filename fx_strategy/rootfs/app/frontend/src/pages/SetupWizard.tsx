import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Banner, Card, Field, Loading } from '@/components/ui';
import { useSaveState } from '@/hooks/usePosition';
import { useCurrentRate, useSetManualRate } from '@/hooks/useRates';
import { useSettings, useUpdateSettings } from '@/hooks/useSettings';
import { formatDecimal } from '@/lib/decimal';

/**
 * Four steps: the pair and where the rate comes from, what you hold, the
 * mortgage the money is waiting for, and how you want to be told about changes.
 *
 * The wizard used to build a conversion ladder over eight steps. Wise executes
 * the conversions now, so there is no ladder to build — the app's job is to
 * know what is held and say when something meaningful changes, and that needs
 * only these four answers.
 *
 * Everything here can be left empty and filled in later. Only the balance is
 * required, and even it can be edited a minute after saving.
 */

const STEPS = ['Rate', 'What you hold', 'Your mortgage', 'Notifications'] as const;

interface Draft {
  provider: string;
  balance: string;
  baselineRate: string;
  baselineDate: string;
  shortfall: string;
  loanRate: string;
  burn: string;
  services: string;
  quietHours: boolean;
}

const INITIAL: Draft = {
  provider: 'manual',
  balance: '',
  baselineRate: '',
  baselineDate: '',
  shortfall: '',
  loanRate: '',
  burn: '',
  services: 'notify.persistent_notification',
  quietHours: false,
};

/** Empty means "not set", which the API expresses as null rather than zero. */
function orNull(value: string): string | null {
  return value.trim() === '' ? null : value.trim();
}

export default function SetupWizard() {
  const navigate = useNavigate();
  const settings = useSettings();
  const rate = useCurrentRate();
  const updateSettings = useUpdateSettings();
  const manualRate = useSetManualRate();
  const savePosition = useSaveState();

  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(INITIAL);
  const [manualValue, setManualValue] = useState('');
  const [error, setError] = useState<string | null>(null);

  if (settings.isLoading || !settings.data) return <Loading label="Loading…" />;
  const general = settings.data.general;

  const patch = (changes: Partial<Draft>) => setDraft({ ...draft, ...changes });

  const finish = async () => {
    setError(null);
    try {
      await savePosition.mutateAsync({
        source_currency: general.source_currency,
        target_currency: general.target_currency,
        current_source_balance: draft.balance.trim() || '0',
        baseline_rate: orNull(draft.baselineRate),
        baseline_date: orNull(draft.baselineDate),
        floating_loan_rate: orNull(draft.loanRate),
        current_offset_shortfall_nzd: orNull(draft.shortfall),
        monthly_nzd_burn: orNull(draft.burn),
      });

      await updateSettings.mutateAsync({
        general: { ...general, setup_complete: true },
        notifications: {
          ...settings.data.notifications,
          services: draft.services
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean),
          quiet_hours: { ...settings.data.notifications.quiet_hours, enabled: draft.quietHours },
        },
        providers: { ...settings.data.providers, primary: draft.provider },
      });
      navigate('/');
    } catch (caught) {
      setError((caught as Error).message);
    }
  };

  return (
    <Card
      title={`Setup — step ${step + 1} of ${STEPS.length}: ${STEPS[step]}`}
      subtitle="You can change every one of these later."
    >
      {error && <Banner tone="error">{error}</Banner>}

      {step === 0 && (
        <div>
          <p>
            This app watches the {general.source_currency}/{general.target_currency} rate and keeps
            a record of what you have converted. It does not predict rates, it never moves money,
            and it will not tell you what to convert next — your provider executes, and this says
            what the position is and when something has changed.
          </p>
          <Field label="Where should the rate come from?" htmlFor="provider">
            <select
              id="provider"
              value={draft.provider}
              onChange={(event) => patch({ provider: event.target.value })}
            >
              <option value="manual">Manual entry or simulation</option>
              <option value="wise">Wise</option>
              <option value="generic">Generic API provider</option>
            </select>
          </Field>
          <p className="fx-stat-note">
            Rates are shown as {general.target_currency} per 1 {general.source_currency}. Wise and
            API providers are configured fully in Settings; manual entry works immediately and needs
            no account.
          </p>
          <Field label="Enter a rate now to try it" htmlFor="test-rate">
            <input
              id="test-rate"
              type="text"
              inputMode="decimal"
              placeholder="1.7550"
              value={manualValue}
              onChange={(event) => setManualValue(event.target.value)}
            />
          </Field>
          <button
            type="button"
            disabled={!manualValue || manualRate.isPending}
            onClick={() => manualRate.mutate({ rate: manualValue })}
          >
            Test
          </button>
          {rate.data?.rate && (
            <p className="fx-stat-note">
              Current rate: {formatDecimal(rate.data.rate, { places: 4, grouping: false })}{' '}
              {general.target_currency} per 1 {general.source_currency}
            </p>
          )}
        </div>
      )}

      {step === 1 && (
        <div>
          <Field
            label={`${general.source_currency} still held`}
            hint="what has not been converted yet"
            htmlFor="balance"
          >
            <input
              id="balance"
              type="text"
              inputMode="decimal"
              placeholder="691536"
              value={draft.balance}
              onChange={(event) => patch({ balance: event.target.value })}
            />
          </Field>
          <Field
            label="Baseline rate"
            hint="the rate you would otherwise have accepted"
            htmlFor="baseline-rate"
          >
            <input
              id="baseline-rate"
              type="text"
              inputMode="decimal"
              placeholder="1.6890"
              value={draft.baselineRate}
              onChange={(event) => patch({ baselineRate: event.target.value })}
            />
          </Field>
          <Field label="Baseline date" hint="optional" htmlFor="baseline-date">
            <input
              id="baseline-date"
              type="date"
              value={draft.baselineDate}
              onChange={(event) => patch({ baselineDate: event.target.value })}
            />
          </Field>
          <p className="fx-stat-note">
            Every improvement figure is measured against the baseline. Without one the app still
            records what you hold and what it is worth, and says “set a baseline rate” wherever a
            gain would otherwise appear — rather than showing a gain of zero, which would be a
            different and wrong claim.
          </p>
        </div>
      )}

      {step === 2 && (
        <div>
          <Field
            label={`Offset shortfall (${general.target_currency})`}
            hint="the part of the offset account that is still unfunded"
            htmlFor="shortfall"
          >
            <input
              id="shortfall"
              type="text"
              inputMode="decimal"
              placeholder="35585"
              value={draft.shortfall}
              onChange={(event) => patch({ shortfall: event.target.value })}
            />
          </Field>
          <Field
            label="Floating loan rate"
            hint="a fraction, not a percentage: 6.04% is 0.0604"
            htmlFor="loan-rate"
          >
            <input
              id="loan-rate"
              type="text"
              inputMode="decimal"
              placeholder="0.0604"
              value={draft.loanRate}
              onChange={(event) => patch({ loanRate: event.target.value })}
            />
          </Field>
          <Field
            label={`Monthly spending (${general.target_currency})`}
            hint="optional"
            htmlFor="burn"
          >
            <input
              id="burn"
              type="text"
              inputMode="decimal"
              placeholder="5000"
              value={draft.burn}
              onChange={(event) => patch({ burn: event.target.value })}
            />
          </Field>
          <p className="fx-stat-note">
            These two give the carrying cost: what the unfunded part of the offset costs you for
            every day the money is still in {general.source_currency}. The cost is charged on the
            shortfall, never on the whole balance.
          </p>
        </div>
      )}

      {step === 3 && (
        <div>
          <Field
            label="Home Assistant notify services"
            hint="one per line; Settings lists what your installation offers"
            htmlFor="services"
          >
            <textarea
              id="services"
              rows={3}
              value={draft.services}
              onChange={(event) => patch({ services: event.target.value })}
            />
          </Field>
          <div className="fx-inline">
            <input
              id="quiet"
              type="checkbox"
              checked={draft.quietHours}
              onChange={(event) => patch({ quietHours: event.target.checked })}
            />
            <label htmlFor="quiet">Hold non-critical alerts overnight (22:00–07:00)</label>
          </div>
          <p className="fx-stat-note">
            What gets an alert, and how far the rate has to move before it says so again, is in
            Settings under FX alerts.
          </p>
        </div>
      )}

      <div className="fx-toolbar" style={{ marginTop: 'var(--fx-gap)' }}>
        <button type="button" onClick={() => setStep(Math.max(step - 1, 0))} disabled={step === 0}>
          Back
        </button>
        {step < STEPS.length - 1 ? (
          <button type="button" className="is-primary" onClick={() => setStep(step + 1)}>
            Next
          </button>
        ) : (
          <button
            type="button"
            className="is-primary"
            onClick={() => void finish()}
            disabled={savePosition.isPending}
          >
            {savePosition.isPending ? 'Saving…' : 'Save position'}
          </button>
        )}
        <button type="button" onClick={() => navigate('/')}>
          Skip setup
        </button>
      </div>
    </Card>
  );
}
