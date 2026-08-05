/**
 * The one place the field editor and the JSON view agree on what a strategy is.
 *
 * Both views edit the same value — a `StrategyInput`, which mirrors the
 * server's `StrategyIn` field for field. The JSON view is that value rendered
 * as text; the field editor is that value rendered as inputs. Switching modes
 * converts between them rather than reloading from somewhere else, so unsaved
 * work survives the switch and the two can never disagree.
 *
 * This matters more than it looks. `PUT /strategies/{id}` and the document
 * endpoint both replace the whole definition, so any field this module forgets
 * to carry is a field silently cleared the next time either view saves.
 */
import type {
  AllocationType,
  RequirementInput,
  Strategy,
  StrategyInput,
  TrancheInput,
} from '@/types';

const ALLOCATION_TYPES: AllocationType[] = ['percentage', 'fixed_amount', 'remainder'];

/**
 * Money and rates are strings everywhere in this app. A pasted document may
 * still carry a JSON number, which the server accepts; it is turned into a
 * string here at the boundary rather than being allowed any further in.
 */
function text(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return fallback;
}

function optionalText(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  return text(value, '');
}

function flag(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** An empty strategy for an installation that has none yet. */
export function blankDraft(source: string, target: string, timezone: string): StrategyInput {
  return {
    name: `${source} to ${target}`,
    source_currency: source,
    target_currency: target,
    initial_source_amount: '800000',
    funds_available_amount: '0',
    funds_arrival_date: null,
    strategy_start_date: null,
    final_deadline: null,
    minimum_acceptable_rate: null,
    walk_away_rate: null,
    require_targets_in_order: false,
    fee_model_id: null,
    rate_provider_id: null,
    timezone,
    notes: '',
    tranches: [],
    requirements: [],
  };
}

/**
 * A saved strategy as an editable draft.
 *
 * Every field the server accepts is copied. Keys are written in the order the
 * server's document uses, so the JSON view reads the same way whether the text
 * came from here or straight from `GET /strategies/{id}/document`.
 */
export function draftFromStrategy(strategy: Strategy): StrategyInput {
  return {
    name: strategy.name,
    source_currency: strategy.source_currency,
    target_currency: strategy.target_currency,
    initial_source_amount: strategy.initial_source_amount,
    funds_available_amount: strategy.funds_available_amount,
    funds_arrival_date: strategy.funds_arrival_date,
    strategy_start_date: strategy.strategy_start_date,
    final_deadline: strategy.final_deadline,
    minimum_acceptable_rate: strategy.minimum_acceptable_rate,
    walk_away_rate: strategy.walk_away_rate,
    require_targets_in_order: strategy.require_targets_in_order,
    fee_model_id: strategy.fee_model_id,
    rate_provider_id: strategy.rate_provider_id,
    timezone: strategy.timezone,
    notes: strategy.notes,
    tranches: [...strategy.tranches]
      .sort((a, b) => a.sequence - b.sequence)
      .map((tranche) => ({
        sequence: tranche.sequence,
        name: tranche.name,
        allocation_type: tranche.allocation_type,
        allocation_value: tranche.allocation_value,
        target_rate: tranche.target_rate,
        minimum_rate: tranche.minimum_rate,
        deadline: tranche.deadline,
        intended_for_auto_conversion: tranche.intended_for_auto_conversion,
        notifications_enabled: tranche.notifications_enabled,
        wise_auto_conversion_reference: tranche.wise_auto_conversion_reference,
      })),
    requirements: strategy.requirements.map((requirement) => ({
      due_date: requirement.due_date,
      required_source_amount: requirement.required_source_amount,
      required_percentage: requirement.required_percentage,
      description: requirement.description,
    })),
  };
}

/** The draft as the JSON document, indented for an editor. */
export function draftToJson(draft: StrategyInput): string {
  return JSON.stringify(draft, null, 2);
}

function trancheFrom(raw: unknown, index: number): TrancheInput {
  const item = record(raw);
  const type = text(item.allocation_type) as AllocationType;
  return {
    sequence: typeof item.sequence === 'number' ? item.sequence : index + 1,
    name: text(item.name),
    allocation_type: ALLOCATION_TYPES.includes(type) ? type : 'percentage',
    allocation_value: text(item.allocation_value, '0'),
    target_rate: text(item.target_rate, '0'),
    minimum_rate: optionalText(item.minimum_rate),
    deadline: optionalText(item.deadline),
    intended_for_auto_conversion: flag(item.intended_for_auto_conversion, true),
    notifications_enabled: flag(item.notifications_enabled, true),
    wise_auto_conversion_reference: optionalText(item.wise_auto_conversion_reference),
  };
}

function requirementFrom(raw: unknown): RequirementInput {
  const item = record(raw);
  return {
    due_date: text(item.due_date),
    required_source_amount: optionalText(item.required_source_amount),
    required_percentage: optionalText(item.required_percentage),
    description: text(item.description),
  };
}

export type ParseResult =
  | { ok: true; draft: StrategyInput }
  | { ok: false; message: string };

/**
 * Read edited JSON back into a draft.
 *
 * Deliberately forgiving about shape and silent about nothing that matters:
 * missing keys take the same defaults the server would apply, and anything the
 * server would actually reject is left for the server to reject, with its own
 * message and field path. This only has to produce something the field inputs
 * can render.
 */
export function draftFromJson(text_: string, fallback: StrategyInput): ParseResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text_) as unknown;
  } catch (error) {
    return { ok: false, message: `The JSON cannot be read: ${(error as Error).message}` };
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ok: false, message: 'A strategy document must be a JSON object.' };
  }

  const raw = parsed as Record<string, unknown>;
  return {
    ok: true,
    draft: {
      name: text(raw.name, fallback.name),
      source_currency: text(raw.source_currency, fallback.source_currency),
      target_currency: text(raw.target_currency, fallback.target_currency),
      initial_source_amount: text(raw.initial_source_amount, '0'),
      funds_available_amount: text(raw.funds_available_amount, '0'),
      funds_arrival_date: optionalText(raw.funds_arrival_date),
      strategy_start_date: optionalText(raw.strategy_start_date),
      final_deadline: optionalText(raw.final_deadline),
      minimum_acceptable_rate: optionalText(raw.minimum_acceptable_rate),
      walk_away_rate: optionalText(raw.walk_away_rate),
      require_targets_in_order: flag(raw.require_targets_in_order, false),
      fee_model_id: typeof raw.fee_model_id === 'number' ? raw.fee_model_id : null,
      rate_provider_id: optionalText(raw.rate_provider_id),
      timezone: text(raw.timezone, fallback.timezone ?? 'Pacific/Auckland'),
      notes: text(raw.notes),
      tranches: list(raw.tranches).map(trancheFrom),
      requirements: list(raw.requirements).map(requirementFrom),
    },
  };
}
