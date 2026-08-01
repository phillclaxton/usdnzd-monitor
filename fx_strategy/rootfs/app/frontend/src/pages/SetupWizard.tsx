import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';

import { Banner, Card, Field, Loading } from '@/components/ui';
import { useCurrentRate, useSetManualRate } from '@/hooks/useRates';
import { useSettings, useUpdateSettings } from '@/hooks/useSettings';
import { useCreateStrategy, useFeeModels, useTemplates } from '@/hooks/useStrategy';
import { api } from '@/lib/api';
import { formatDecimal, formatMoney, roundTo } from '@/lib/decimal';
import type { FeeModel, StrategyInput, TrancheInput } from '@/types';

const STEPS = [
  'Welcome',
  'Currency pair',
  'Amount',
  'Rate provider',
  'Strategy',
  'Fees',
  'Notifications',
  'Review',
] as const;

interface Draft {
  total: string;
  available: string;
  arrival: string;
  deadline: string;
  walkAway: string;
  provider: string;
  template: string;
  tranches: TrancheInput[];
  feeMode: 'quote' | 'percentage' | 'fixed_plus_percentage' | 'none';
  feePercentage: string;
  feeFixed: string;
  services: string;
  quietHours: boolean;
}

const INITIAL: Draft = {
  total: '800000',
  available: '0',
  arrival: '',
  deadline: '',
  walkAway: '1.7800',
  provider: 'manual',
  template: 'recommended',
  tranches: [],
  feeMode: 'none',
  feePercentage: '0.41',
  feeFixed: '0',
  services: 'notify.persistent_notification',
  quietHours: false,
};

export default function SetupWizard() {
  const navigate = useNavigate();
  const settings = useSettings();
  const templates = useTemplates();
  const feeModels = useFeeModels();
  const rate = useCurrentRate();
  const updateSettings = useUpdateSettings();
  const createStrategy = useCreateStrategy();
  const manualRate = useSetManualRate();

  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(INITIAL);
  const [manualValue, setManualValue] = useState('');
  const [error, setError] = useState<string | null>(null);

  const createFeeModel = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.post<FeeModel>('fee-models', payload),
  });

  if (settings.isLoading || !settings.data) return <Loading label="Loading…" />;
  const general = settings.data.general;

  const patch = (changes: Partial<Draft>) => setDraft({ ...draft, ...changes });

  const chosenTemplate = templates.data?.find((item) => item.key === draft.template);
  const tranches = draft.tranches.length > 0 ? draft.tranches : (chosenTemplate?.tranches ?? []);

  const grossAtTargets = tranches.reduce((total, tranche) => {
    const amount =
      tranche.allocation_type === 'percentage'
        ? (Number(draft.total) * Number(tranche.allocation_value || 0)) / 100
        : Number(tranche.allocation_value || 0);
    return total + amount * Number(tranche.target_rate || 0);
  }, 0);
  const allocated = tranches.reduce((total, tranche) => {
    const amount =
      tranche.allocation_type === 'percentage'
        ? (Number(draft.total) * Number(tranche.allocation_value || 0)) / 100
        : Number(tranche.allocation_value || 0);
    return total + amount;
  }, 0);
  const blended = allocated > 0 ? roundTo(String(grossAtTargets / allocated), 4) : null;
  const estimatedFee =
    draft.feeMode === 'percentage'
      ? (Number(draft.total) * Number(draft.feePercentage)) / 100
      : draft.feeMode === 'fixed_plus_percentage'
        ? Number(draft.feeFixed) + (Number(draft.total) * Number(draft.feePercentage)) / 100
        : null;

  const finish = async () => {
    setError(null);
    try {
      let feeModelId: number | null = null;
      if (draft.feeMode === 'percentage' || draft.feeMode === 'fixed_plus_percentage') {
        const model = await createFeeModel.mutateAsync({
          name: draft.feeMode === 'percentage' ? 'Percentage estimate' : 'Fixed plus percentage',
          fee_type: draft.feeMode,
          percentage_fee: draft.feePercentage,
          fixed_fee: draft.feeFixed,
          currency: general.source_currency,
        });
        feeModelId = model.id;
      } else if (draft.feeMode === 'quote') {
        const model = await createFeeModel.mutateAsync({
          name: 'Live Wise quote',
          fee_type: 'quote_only',
          currency: general.source_currency,
        });
        feeModelId = model.id;
      }

      const payload: StrategyInput = {
        name: `${general.source_currency} to ${general.target_currency}`,
        source_currency: general.source_currency,
        target_currency: general.target_currency,
        initial_source_amount: draft.total,
        funds_available_amount: draft.available,
        funds_arrival_date: draft.arrival ? `${draft.arrival}T00:00:00Z` : null,
        final_deadline: draft.deadline ? `${draft.deadline}T00:00:00Z` : null,
        walk_away_rate: draft.walkAway || null,
        fee_model_id: feeModelId,
        tranches,
      };
      await createStrategy.mutateAsync(payload);

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
    <>
      <Card
        title={`Setup — step ${step + 1} of ${STEPS.length}: ${STEPS[step]}`}
        subtitle="You can change every one of these later."
      >
        {error && <Banner tone="error">{error}</Banner>}

        {step === 0 && (
          <div>
            <p>
              FX Strategy Manager watches an exchange rate and helps you convert a large balance
              in stages.
            </p>
            <ul>
              <li>It does not predict rates. Nothing here is a forecast.</li>
              <li>It never moves money. You make every conversion in your provider.</li>
              <li>
                The target levels are yours to set, and to change whenever you want.
              </li>
              <li>It keeps a permanent record of what you converted and at what rate.</li>
            </ul>
          </div>
        )}

        {step === 1 && (
          <div>
            <Field label="Source currency">
              <input value={general.source_currency} readOnly aria-label="Source currency" />
            </Field>
            <Field label="Target currency">
              <input value={general.target_currency} readOnly aria-label="Target currency" />
            </Field>
            <p className="fx-stat-note">
              Rates are shown as {general.target_currency} per 1 {general.source_currency} — for
              example, 1 {general.source_currency} = 1.7500 {general.target_currency}.
            </p>
          </div>
        )}

        {step === 2 && (
          <div>
            <Field label={`Total ${general.source_currency} you expect to convert`} htmlFor="total">
              <input
                id="total"
                type="text"
                inputMode="decimal"
                value={draft.total}
                onChange={(event) => patch({ total: event.target.value })}
              />
            </Field>
            <Field
              label={`${general.source_currency} available now`}
              hint="exposure figures use this, not the total"
              htmlFor="available"
            >
              <input
                id="available"
                type="text"
                inputMode="decimal"
                value={draft.available}
                onChange={(event) => patch({ available: event.target.value })}
              />
            </Field>
            <Field label="Expected arrival date" htmlFor="arrival">
              <input
                id="arrival"
                type="date"
                value={draft.arrival}
                onChange={(event) => patch({ arrival: event.target.value })}
              />
            </Field>
            <Field label="Final conversion deadline" htmlFor="deadline">
              <input
                id="deadline"
                type="date"
                value={draft.deadline}
                onChange={(event) => patch({ deadline: event.target.value })}
              />
            </Field>
          </div>
        )}

        {step === 3 && (
          <div>
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
              You can configure Wise or an API provider fully in Settings. Manual entry works
              immediately and needs no account.
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

        {step === 4 && (
          <div>
            <Field label="Choose a starting ladder" htmlFor="template">
              <select
                id="template"
                value={draft.template}
                onChange={(event) => patch({ template: event.target.value, tranches: [] })}
              >
                {(templates.data ?? []).map((template) => (
                  <option key={template.key} value={template.key}>
                    {template.name}
                  </option>
                ))}
              </select>
            </Field>
            <p className="fx-stat-note">{chosenTemplate?.description}</p>
            {tranches.length > 0 && (
              <div className="fx-table-wrap">
                <table className="fx-table">
                  <thead>
                    <tr>
                      <th className="fx-left">#</th>
                      <th>Share</th>
                      <th>Target</th>
                      <th>{general.source_currency}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tranches.map((tranche) => (
                      <tr key={tranche.sequence}>
                        <td className="fx-left">{tranche.sequence}</td>
                        <td>{formatDecimal(tranche.allocation_value, { places: 0 })}%</td>
                        <td>{formatDecimal(tranche.target_rate, { places: 4, grouping: false })}</td>
                        <td>
                          {formatDecimal(
                            String(
                              (Number(draft.total) * Number(tranche.allocation_value)) / 100,
                            ),
                            { places: 0 },
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <Field label="Walk-away rate" hint="the level at which finishing is good enough" htmlFor="walkaway">
              <input
                id="walkaway"
                type="text"
                inputMode="decimal"
                value={draft.walkAway}
                onChange={(event) => patch({ walkAway: event.target.value })}
              />
            </Field>
          </div>
        )}

        {step === 5 && (
          <div>
            <Field label="How should fees be estimated?" htmlFor="fee-mode">
              <select
                id="fee-mode"
                value={draft.feeMode}
                onChange={(event) => patch({ feeMode: event.target.value as Draft['feeMode'] })}
              >
                <option value="none">Exclude fees</option>
                <option value="percentage">Percentage estimate</option>
                <option value="fixed_plus_percentage">Fixed plus percentage</option>
                <option value="quote">Use a live Wise quote</option>
              </select>
            </Field>
            {draft.feeMode === 'none' && (
              <Banner tone="warning">
                Without a fee estimate, every figure is shown gross and labelled “Fee not
                included”. Net proceeds cannot be calculated.
              </Banner>
            )}
            {(draft.feeMode === 'percentage' || draft.feeMode === 'fixed_plus_percentage') && (
              <>
                <Field label="Percentage fee" htmlFor="fee-pct">
                  <input
                    id="fee-pct"
                    type="text"
                    inputMode="decimal"
                    value={draft.feePercentage}
                    onChange={(event) => patch({ feePercentage: event.target.value })}
                  />
                </Field>
                {draft.feeMode === 'fixed_plus_percentage' && (
                  <Field label="Fixed fee" htmlFor="fee-fixed">
                    <input
                      id="fee-fixed"
                      type="text"
                      inputMode="decimal"
                      value={draft.feeFixed}
                      onChange={(event) => patch({ feeFixed: event.target.value })}
                    />
                  </Field>
                )}
              </>
            )}
            {feeModels.data && feeModels.data.length > 0 && (
              <p className="fx-stat-note">
                Existing fee models: {feeModels.data.map((model) => model.name).join(', ')}
              </p>
            )}
          </div>
        )}

        {step === 6 && (
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
          </div>
        )}

        {step === 7 && (
          <div>
            <div className="fx-grid">
              <div className="fx-stat">
                <div className="fx-stat-label">Total allocation</div>
                <div className="fx-stat-value is-small">
                  {formatMoney(String(allocated), general.source_currency)}
                </div>
                <div className="fx-stat-note">
                  of {formatMoney(draft.total, general.source_currency)}
                </div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Gross at every target</div>
                <div className="fx-stat-value is-small">
                  {formatMoney(String(grossAtTargets), general.target_currency)}
                </div>
                <div className="fx-stat-note">assumes every target is reached</div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Blended target rate</div>
                <div className="fx-stat-value is-small">{blended ?? '—'}</div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Estimated fees</div>
                <div className="fx-stat-value is-small">
                  {estimatedFee === null
                    ? 'Not included'
                    : formatMoney(String(estimatedFee), general.source_currency)}
                </div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Estimated net</div>
                <div className="fx-stat-value is-small">
                  {estimatedFee === null
                    ? 'Not calculable'
                    : formatMoney(
                        String(grossAtTargets - estimatedFee * Number(blended ?? 1)),
                        general.target_currency,
                      )}
                </div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">One-cent exposure</div>
                <div className="fx-stat-value is-small">
                  {formatMoney(
                    String(Number(draft.available || 0) * 0.01),
                    general.target_currency,
                  )}
                </div>
                <div className="fx-stat-note">on the amount available today</div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Deadline</div>
                <div className="fx-stat-value is-small">{draft.deadline || 'None set'}</div>
              </div>
            </div>
            <Banner tone="info">
              These figures assume every target is reached. Targets that are never reached leave
              that tranche unconverted.
            </Banner>
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
              disabled={createStrategy.isPending}
            >
              {createStrategy.isPending ? 'Creating…' : 'Create strategy'}
            </button>
          )}
          <button type="button" onClick={() => navigate('/')}>
            Skip setup
          </button>
        </div>
      </Card>
    </>
  );
}
