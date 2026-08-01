import { useEffect, useMemo, useState } from 'react';

import { Banner, Card, EmptyState, Field, Loading, Tag } from '@/components/ui';
import {
  useCreateStrategy,
  useFeeModels,
  useStrategies,
  useStrategy,
  useStrategyAction,
  useTemplates,
  useUpdateStrategy,
} from '@/hooks/useStrategy';
import { useCurrentRate } from '@/hooks/useRates';
import { useSettings } from '@/hooks/useSettings';
import { compareDecimal, formatDecimal, formatMoney, formatRate, roundTo } from '@/lib/decimal';
import type { StrategyInput, TrancheInput } from '@/types';

function blankStrategy(source: string, target: string): StrategyInput {
  return {
    name: `${source} to ${target}`,
    source_currency: source,
    target_currency: target,
    initial_source_amount: '800000',
    funds_available_amount: '0',
    final_deadline: null,
    walk_away_rate: null,
    minimum_acceptable_rate: null,
    require_targets_in_order: false,
    fee_model_id: null,
    tranches: [],
  };
}

/**
 * Allocation totals are computed here as well as on the server. The client copy
 * gives instant feedback while typing; the server's answer is authoritative and
 * is what blocks activation.
 */
function allocationSummary(draft: StrategyInput): {
  percentage: string;
  allocated: string;
  unallocated: string;
  errors: string[];
} {
  const total = draft.initial_source_amount || '0';
  let percentage = '0';
  let allocated = '0';
  const errors: string[] = [];

  for (const tranche of draft.tranches) {
    if (tranche.allocation_type === 'percentage') {
      percentage = String(Number(percentage) + Number(tranche.allocation_value || '0'));
      allocated = roundTo(
        String((Number(total) * Number(tranche.allocation_value || '0')) / 100 + Number(allocated)),
        4,
      );
    } else if (tranche.allocation_type === 'fixed_amount') {
      allocated = roundTo(String(Number(allocated) + Number(tranche.allocation_value || '0')), 4);
    }
  }
  if (Number(percentage) > 100) errors.push(`Percentages total ${percentage}%, above 100%.`);
  if (compareDecimal(allocated, total) > 0) {
    errors.push('Tranches allocate more than the strategy total.');
  }
  const remainderCount = draft.tranches.filter((t) => t.allocation_type === 'remainder').length;
  const unallocated = remainderCount
    ? '0'
    : roundTo(String(Number(total) - Number(allocated)), 4);
  return { percentage, allocated, unallocated, errors };
}

export default function StrategyEditor() {
  const settings = useSettings();
  const strategies = useStrategies();
  const templates = useTemplates();
  const feeModels = useFeeModels();
  const rate = useCurrentRate();

  const existingId = strategies.data?.[0]?.id ?? null;
  const existing = useStrategy(existingId);
  const create = useCreateStrategy();
  const update = useUpdateStrategy(existingId ?? 0);
  const action = useStrategyAction(existingId ?? 0);

  const general = settings.data?.general;
  const ratePlaces = settings.data?.formatting.rate_decimal_places ?? 4;
  const [draft, setDraft] = useState<StrategyInput | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (draft !== null || !general) return;
    if (existing.data) {
      setDraft({
        name: existing.data.name,
        source_currency: existing.data.source_currency,
        target_currency: existing.data.target_currency,
        initial_source_amount: existing.data.initial_source_amount,
        funds_available_amount: existing.data.funds_available_amount,
        funds_arrival_date: existing.data.funds_arrival_date,
        final_deadline: existing.data.final_deadline,
        minimum_acceptable_rate: existing.data.minimum_acceptable_rate,
        walk_away_rate: existing.data.walk_away_rate,
        require_targets_in_order: existing.data.require_targets_in_order,
        fee_model_id: existing.data.fee_model_id,
        timezone: existing.data.timezone,
        notes: existing.data.notes,
        tranches: existing.data.tranches.map((tranche) => ({
          sequence: tranche.sequence,
          name: tranche.name,
          allocation_type: tranche.allocation_type,
          allocation_value: tranche.allocation_value,
          target_rate: tranche.target_rate,
          intended_for_auto_conversion: tranche.intended_for_auto_conversion,
          notifications_enabled: tranche.notifications_enabled,
          wise_auto_conversion_reference: tranche.wise_auto_conversion_reference,
        })),
      });
    } else if (!existingId && !strategies.isLoading) {
      setDraft(blankStrategy(general.source_currency, general.target_currency));
    }
  }, [draft, existing.data, existingId, general, strategies.isLoading]);

  const allocation = useMemo(
    () => (draft ? allocationSummary(draft) : null),
    [draft],
  );

  if (settings.isLoading || strategies.isLoading || !draft || !allocation) {
    return <Loading label="Loading strategy editor…" />;
  }

  const patch = (changes: Partial<StrategyInput>) => {
    setSaved(false);
    setDraft({ ...draft, ...changes });
  };

  const patchTranche = (index: number, changes: Partial<TrancheInput>) => {
    const tranches = draft.tranches.map((tranche, position) =>
      position === index ? { ...tranche, ...changes } : tranche,
    );
    patch({ tranches });
  };

  const addTranche = () => {
    const next = draft.tranches.length + 1;
    const lastRate = draft.tranches.at(-1)?.target_rate ?? rate.data?.rate ?? '1.7200';
    patch({
      tranches: [
        ...draft.tranches,
        {
          sequence: next,
          name: `Tranche ${next}`,
          allocation_type: 'percentage',
          allocation_value: '10',
          target_rate: roundTo(String(Number(lastRate) + 0.02), 4),
        },
      ],
    });
  };

  const removeTranche = (index: number) => {
    patch({
      tranches: draft.tranches
        .filter((_tranche, position) => position !== index)
        .map((tranche, position) => ({ ...tranche, sequence: position + 1 })),
    });
  };

  const move = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= draft.tranches.length) return;
    const tranches = [...draft.tranches];
    const a = tranches[index];
    const b = tranches[target];
    if (!a || !b) return;
    tranches[index] = b;
    tranches[target] = a;
    patch({ tranches: tranches.map((tranche, position) => ({ ...tranche, sequence: position + 1 })) });
  };

  const applyTemplate = (key: string) => {
    const template = templates.data?.find((item) => item.key === key);
    if (!template) return;
    patch({ tranches: template.tranches.map((tranche) => ({ ...tranche })) });
  };

  const save = () => {
    setSaved(false);
    const onSuccess = () => setSaved(true);
    if (existingId) update.mutate(draft, { onSuccess });
    else create.mutate(draft, { onSuccess });
  };

  const mutation = existingId ? update : create;
  const projectedGross = draft.tranches.reduce((accumulator, tranche) => {
    const amount =
      tranche.allocation_type === 'percentage'
        ? (Number(draft.initial_source_amount) * Number(tranche.allocation_value || 0)) / 100
        : Number(tranche.allocation_value || 0);
    return accumulator + amount * Number(tranche.target_rate || 0);
  }, 0);

  return (
    <>
      <Banner tone="info">
        Targets are your own decision. This app does not predict rates and will not convert
        anything for you — it watches, calculates and tells you.
      </Banner>

      {mutation.isError && <Banner tone="error">{(mutation.error as Error).message}</Banner>}
      {saved && <Banner tone="info">Strategy saved.</Banner>}
      {allocation.errors.map((error) => (
        <Banner key={error} tone="warning">
          {error}
        </Banner>
      ))}

      <Card title="Amount and timing">
        <Field label="Strategy name" htmlFor="name">
          <input id="name" value={draft.name} onChange={(e) => patch({ name: e.target.value })} />
        </Field>
        <Field
          label={`Total ${draft.source_currency} to convert`}
          hint="the full amount this strategy plans for"
          htmlFor="total"
        >
          <input
            id="total"
            type="text"
            inputMode="decimal"
            value={draft.initial_source_amount}
            onChange={(e) => patch({ initial_source_amount: e.target.value })}
          />
        </Field>
        <Field
          label={`${draft.source_currency} available now`}
          hint="exposure figures use this, not the total"
          htmlFor="available"
        >
          <input
            id="available"
            type="text"
            inputMode="decimal"
            value={draft.funds_available_amount}
            onChange={(e) => patch({ funds_available_amount: e.target.value })}
          />
        </Field>
        <Field label="Expected funds arrival" htmlFor="arrival">
          <input
            id="arrival"
            type="date"
            value={draft.funds_arrival_date?.slice(0, 10) ?? ''}
            onChange={(e) =>
              patch({
                funds_arrival_date: e.target.value ? `${e.target.value}T00:00:00Z` : null,
              })
            }
          />
        </Field>
        <Field label="Final conversion deadline" htmlFor="deadline">
          <input
            id="deadline"
            type="date"
            value={draft.final_deadline?.slice(0, 10) ?? ''}
            onChange={(e) =>
              patch({ final_deadline: e.target.value ? `${e.target.value}T00:00:00Z` : null })
            }
          />
        </Field>
        <Field
          label="Walk-away rate"
          hint="the level at which finishing is good enough"
          htmlFor="walkaway"
        >
          <input
            id="walkaway"
            type="text"
            inputMode="decimal"
            placeholder="1.7800"
            value={draft.walk_away_rate ?? ''}
            onChange={(e) => patch({ walk_away_rate: e.target.value || null })}
          />
        </Field>
        <Field label="Minimum acceptable rate" htmlFor="minimum">
          <input
            id="minimum"
            type="text"
            inputMode="decimal"
            placeholder="1.7000"
            value={draft.minimum_acceptable_rate ?? ''}
            onChange={(e) => patch({ minimum_acceptable_rate: e.target.value || null })}
          />
        </Field>
        <Field label="Fee assumption" hint="without one, only gross figures can be shown" htmlFor="fee">
          <select
            id="fee"
            value={draft.fee_model_id ?? ''}
            onChange={(e) => patch({ fee_model_id: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">No fee model — figures shown gross</option>
            {(feeModels.data ?? []).map((model) => (
              <option key={model.id} value={model.id}>
                {model.name}
              </option>
            ))}
          </select>
        </Field>
        <div className="fx-inline">
          <input
            id="ordered"
            type="checkbox"
            checked={draft.require_targets_in_order ?? false}
            onChange={(e) => patch({ require_targets_in_order: e.target.checked })}
          />
          <label htmlFor="ordered">Targets must be reached in order</label>
        </div>
      </Card>

      <Card
        title="Target ladder"
        subtitle={`Allocated ${formatDecimal(allocation.allocated)} of ${formatDecimal(
          draft.initial_source_amount,
        )} ${draft.source_currency} · percentages total ${allocation.percentage}%`}
        actions={
          <div className="fx-inline">
            {(templates.data ?? []).map((template) => (
              <button key={template.key} type="button" onClick={() => applyTemplate(template.key)}>
                {template.name}
              </button>
            ))}
          </div>
        }
      >
        {draft.tranches.length === 0 ? (
          <EmptyState glyph="🪜" title="No tranches yet">
            <p>Load the recommended ladder above, or add tranches one at a time.</p>
          </EmptyState>
        ) : (
          <div className="fx-table-wrap">
            <table className="fx-table">
              <thead>
                <tr>
                  <th className="fx-left">#</th>
                  <th className="fx-left">Type</th>
                  <th className="fx-left">Value</th>
                  <th className="fx-left">Target rate</th>
                  <th>{draft.source_currency}</th>
                  <th>Gross {draft.target_currency}</th>
                  <th className="fx-left">Order</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {draft.tranches.map((tranche, index) => {
                  const amount =
                    tranche.allocation_type === 'percentage'
                      ? (Number(draft.initial_source_amount) *
                          Number(tranche.allocation_value || 0)) /
                        100
                      : Number(tranche.allocation_value || 0);
                  return (
                    <tr key={index}>
                      <td className="fx-left">{tranche.sequence}</td>
                      <td className="fx-left">
                        <select
                          aria-label={`Allocation type for tranche ${tranche.sequence}`}
                          value={tranche.allocation_type}
                          onChange={(e) =>
                            patchTranche(index, {
                              allocation_type: e.target.value as TrancheInput['allocation_type'],
                            })
                          }
                        >
                          <option value="percentage">Percentage</option>
                          <option value="fixed_amount">Fixed amount</option>
                          <option value="remainder">Remainder</option>
                        </select>
                      </td>
                      <td className="fx-left">
                        <input
                          aria-label={`Allocation for tranche ${tranche.sequence}`}
                          type="text"
                          inputMode="decimal"
                          disabled={tranche.allocation_type === 'remainder'}
                          value={tranche.allocation_value}
                          onChange={(e) =>
                            patchTranche(index, { allocation_value: e.target.value })
                          }
                        />
                      </td>
                      <td className="fx-left">
                        <input
                          aria-label={`Target rate for tranche ${tranche.sequence}`}
                          type="text"
                          inputMode="decimal"
                          value={tranche.target_rate}
                          onChange={(e) => patchTranche(index, { target_rate: e.target.value })}
                        />
                      </td>
                      <td>
                        {tranche.allocation_type === 'remainder'
                          ? 'remainder'
                          : formatDecimal(String(amount))}
                      </td>
                      <td>
                        {tranche.allocation_type === 'remainder'
                          ? '—'
                          : formatDecimal(String(amount * Number(tranche.target_rate || 0)))}
                      </td>
                      <td className="fx-left">
                        <div className="fx-inline">
                          <button
                            type="button"
                            aria-label={`Move tranche ${tranche.sequence} up`}
                            onClick={() => move(index, -1)}
                          >
                            ↑
                          </button>
                          <button
                            type="button"
                            aria-label={`Move tranche ${tranche.sequence} down`}
                            onClick={() => move(index, 1)}
                          >
                            ↓
                          </button>
                        </div>
                      </td>
                      <td>
                        <button
                          type="button"
                          className="is-danger"
                          onClick={() => removeTranche(index)}
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="fx-toolbar" style={{ marginTop: 'var(--fx-gap)' }}>
          <button type="button" onClick={addTranche}>
            Add tranche
          </button>
        </div>

        <div className="fx-grid">
          <div className="fx-stat">
            <div className="fx-stat-label">
              <span>Gross at every target</span>
              <Tag quality="gross" />
            </div>
            <div className="fx-stat-value is-small">
              {formatMoney(String(projectedGross), draft.target_currency)}
            </div>
            <div className="fx-stat-note">
              Blended{' '}
              {formatRate(
                allocation.allocated === '0'
                  ? null
                  : String(projectedGross / Number(allocation.allocated)),
                ratePlaces,
              )}{' '}
              · assumes every target is reached.
            </div>
          </div>
          <div className="fx-stat">
            <div className="fx-stat-label">Unallocated</div>
            <div className="fx-stat-value is-small">
              {formatMoney(allocation.unallocated, draft.source_currency)}
            </div>
            <div className="fx-stat-note">
              A reserve is allowed; it simply stays unconverted.
            </div>
          </div>
          <div className="fx-stat">
            <div className="fx-stat-label">One-cent exposure</div>
            <div className="fx-stat-value is-small">
              {formatMoney(
                String(Number(draft.funds_available_amount || 0) * 0.01),
                draft.target_currency,
              )}
            </div>
            <div className="fx-stat-note">On the amount available today.</div>
          </div>
        </div>
      </Card>

      <div className="fx-toolbar">
        <button type="button" className="is-primary" onClick={save} disabled={mutation.isPending}>
          {mutation.isPending ? 'Saving…' : existingId ? 'Save strategy' : 'Create strategy'}
        </button>
        {existingId && (
          <>
            <button type="button" onClick={() => action.mutate('activate')}>
              Activate
            </button>
            <button type="button" onClick={() => action.mutate('pause')}>
              Pause monitoring
            </button>
            <button type="button" onClick={() => action.mutate('duplicate')}>
              Duplicate
            </button>
          </>
        )}
      </div>
      {action.isError && <Banner tone="error">{(action.error as Error).message}</Banner>}
    </>
  );
}
