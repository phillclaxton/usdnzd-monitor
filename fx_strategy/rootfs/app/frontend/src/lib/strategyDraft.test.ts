import { describe, expect, it } from 'vitest';

import { blankDraft, draftFromJson, draftFromStrategy, draftToJson } from '@/lib/strategyDraft';
import type { Strategy } from '@/types';

const STRATEGY: Strategy = {
  id: 1,
  name: 'USD to NZD',
  status: 'active',
  source_currency: 'USD',
  target_currency: 'NZD',
  initial_source_amount: '800000.0000',
  funds_available_amount: '800000.0000',
  funds_arrival_date: null,
  strategy_start_date: '2026-08-01T00:00:00+00:00',
  final_deadline: '2026-12-01T00:00:00+00:00',
  minimum_acceptable_rate: '1.70000000',
  walk_away_rate: '1.78000000',
  require_targets_in_order: true,
  fee_model_id: 3,
  rate_provider_id: 'wise',
  timezone: 'Pacific/Auckland',
  notes: 'Staged over four months.',
  created_at: '2026-08-01T00:00:00+00:00',
  updated_at: '2026-08-02T00:00:00+00:00',
  archived_at: null,
  completed_at: null,
  tranches: [
    {
      id: 11,
      strategy_id: 1,
      sequence: 2,
      name: 'Second',
      allocation_type: 'percentage',
      allocation_value: '60.0000',
      calculated_source_amount: '480000.0000',
      target_rate: '1.76000000',
      minimum_rate: '1.74000000',
      deadline: '2026-11-01T00:00:00+00:00',
      status: 'pending',
      intended_for_auto_conversion: false,
      notifications_enabled: true,
      wise_auto_conversion_reference: 'AC-7',
      target_first_reached_at: null,
      notification_sent_at: null,
      acknowledged_at: null,
      completed_at: null,
    },
    {
      id: 10,
      strategy_id: 1,
      sequence: 1,
      name: 'First',
      allocation_type: 'percentage',
      allocation_value: '40.0000',
      calculated_source_amount: '320000.0000',
      target_rate: '1.72000000',
      minimum_rate: null,
      deadline: null,
      status: 'pending',
      intended_for_auto_conversion: true,
      notifications_enabled: true,
      wise_auto_conversion_reference: null,
      target_first_reached_at: null,
      notification_sent_at: null,
      acknowledged_at: null,
      completed_at: null,
    },
  ],
  requirements: [
    {
      id: 5,
      strategy_id: 1,
      due_date: '2026-09-01T00:00:00+00:00',
      required_source_amount: '250000.0000',
      required_percentage: null,
      description: 'School fees',
    },
  ],
};

describe('draftFromStrategy', () => {
  it('carries every field the server accepts', () => {
    const draft = draftFromStrategy(STRATEGY);

    // The fields the field editor has no input for are the ones most easily
    // dropped, and dropping them clears them on the next save.
    expect(draft.strategy_start_date).toBe('2026-08-01T00:00:00+00:00');
    expect(draft.rate_provider_id).toBe('wise');
    expect(draft.requirements).toHaveLength(1);
    expect(draft.requirements?.[0]?.description).toBe('School fees');
    expect(draft.tranches[1]?.minimum_rate).toBe('1.74000000');
    expect(draft.tranches[1]?.deadline).toBe('2026-11-01T00:00:00+00:00');
    expect(draft.tranches[1]?.wise_auto_conversion_reference).toBe('AC-7');
  });

  it('orders tranches by sequence rather than by arrival', () => {
    const draft = draftFromStrategy(STRATEGY);
    expect(draft.tranches.map((tranche) => tranche.sequence)).toEqual([1, 2]);
  });
});

describe('a draft through JSON and back', () => {
  it('survives the round trip unchanged', () => {
    const draft = draftFromStrategy(STRATEGY);
    const result = draftFromJson(draftToJson(draft), draft);

    expect(result.ok).toBe(true);
    if (result.ok) expect(result.draft).toEqual(draft);
  });

  it('survives the round trip for a strategy that does not exist yet', () => {
    const draft = blankDraft('USD', 'NZD', 'Pacific/Auckland');
    const result = draftFromJson(draftToJson(draft), draft);

    expect(result.ok).toBe(true);
    if (result.ok) expect(result.draft).toEqual(draft);
  });
});

describe('draftFromJson', () => {
  const fallback = blankDraft('USD', 'NZD', 'Pacific/Auckland');

  it('refuses text that is not JSON, and says so', () => {
    const result = draftFromJson('{oops', fallback);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain('cannot be read');
  });

  it('refuses a JSON value that is not an object', () => {
    const result = draftFromJson('[1, 2, 3]', fallback);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain('must be a JSON object');
  });

  it('turns a JSON number into a string rather than letting a float through', () => {
    const result = draftFromJson('{"initial_source_amount": 800000}', fallback);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.draft.initial_source_amount).toBe('800000');
  });

  it('applies the server defaults to keys that were left out', () => {
    const result = draftFromJson('{"name": "Minimal"}', fallback);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.draft.name).toBe('Minimal');
    expect(result.draft.tranches).toEqual([]);
    expect(result.draft.requirements).toEqual([]);
    expect(result.draft.require_targets_in_order).toBe(false);
    expect(result.draft.walk_away_rate).toBeNull();
  });

  it('numbers tranches by position when a sequence is missing', () => {
    const result = draftFromJson('{"tranches": [{"target_rate": "1.72"}, {"sequence": 9}]}', fallback);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.draft.tranches.map((t) => t.sequence)).toEqual([1, 9]);
  });

  it('treats an empty string in an optional field as cleared', () => {
    const result = draftFromJson('{"walk_away_rate": ""}', fallback);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.draft.walk_away_rate).toBeNull();
  });
});
