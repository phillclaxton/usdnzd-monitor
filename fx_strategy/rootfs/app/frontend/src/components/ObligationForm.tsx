import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import { Banner, Card, Field } from '@/components/ui';
import { api } from '@/lib/api';
import type {
  InterestBasis,
  Obligation,
  ObligationPriority,
  ObligationType,
  RelationshipImportance,
} from '@/types';

const TYPES: { value: ObligationType; label: string }[] = [
  { value: 'mortgage', label: 'Mortgage' },
  { value: 'revolving_credit', label: 'Revolving credit' },
  { value: 'offset_loan', label: 'Offset loan' },
  { value: 'personal_loan', label: 'Personal loan' },
  { value: 'credit_card', label: 'Credit card' },
  { value: 'tax_payment', label: 'Tax payment' },
  { value: 'interest_free_loan', label: 'Interest-free personal loan' },
  { value: 'planned_purchase', label: 'Planned purchase' },
  { value: 'other', label: 'Other NZD obligation' },
];

/**
 * Adding or editing an obligation.
 *
 * One form for both: an edit that could not reach every field would leave the
 * user unable to undo a mistake. Every optional field can be emptied, and an
 * emptied field is sent as an explicit null so the server clears it rather than
 * leaving the old value in place.
 */
export default function ObligationForm({
  obligation,
  onDone,
  onCancel,
}: {
  obligation?: Obligation;
  onDone: () => void;
  onCancel: () => void;
}) {
  const editing = obligation !== undefined;

  const [name, setName] = useState(obligation?.name ?? '');
  const [type, setType] = useState<ObligationType>(obligation?.obligation_type ?? 'other');
  const [total, setTotal] = useState(obligation?.total_nzd ?? '');
  const [funded, setFunded] = useState(obligation?.amount_funded_nzd ?? '');
  // Stored as a fraction, shown as a percentage.
  const [ratePercent, setRatePercent] = useState(
    obligation && obligation.annual_rate !== '0.00000000'
      ? String(Number(obligation.annual_rate) * 100)
      : '',
  );
  const [basis, setBasis] = useState<InterestBasis>(
    obligation?.interest_basis ?? 'simple_annual',
  );
  const [dueDate, setDueDate] = useState(obligation?.due_date ?? '');
  const [priority, setPriority] = useState<ObligationPriority>(obligation?.priority ?? 'normal');
  const [relationship, setRelationship] = useState<RelationshipImportance>(
    obligation?.relationship_importance ?? 'none',
  );
  const [partial, setPartial] = useState(obligation?.partial_allowed ?? true);
  const [targetRate, setTargetRate] = useState(obligation?.target_rate ?? '');
  const [maxWait, setMaxWait] = useState(
    obligation?.max_wait_days === null || obligation?.max_wait_days === undefined
      ? ''
      : String(obligation.max_wait_days),
  );
  const [notes, setNotes] = useState(obligation?.notes ?? '');

  /** An empty optional field becomes null, which is what clears it server-side. */
  const payload = () => ({
    name,
    obligation_type: type,
    total_nzd: total,
    amount_funded_nzd: funded || '0',
    // Entered as a percentage for readability; stored as the fraction the
    // engine works in, so 6.04 becomes 0.0604.
    annual_rate: basis === 'none' || !ratePercent ? '0' : String(Number(ratePercent) / 100),
    interest_basis: basis,
    due_date: dueDate || null,
    priority,
    relationship_importance: relationship,
    partial_allowed: partial,
    target_rate: targetRate || null,
    max_wait_days: maxWait ? Number(maxWait) : null,
    notes,
  });

  const save = useMutation({
    mutationFn: () =>
      editing
        ? api.patch(`obligations/${obligation.id}`, payload())
        : api.post('obligations', payload()),
    onSuccess: onDone,
  });

  const create = save;

  return (
    <Card
      title={editing ? `Edit ${obligation.name}` : 'New obligation'}
      subtitle={
        editing
          ? 'Clearing an optional field removes it. An emptied date, target rate or waiting limit is deleted rather than left as it was.'
          : 'Everything except the name and amount is optional.'
      }
    >
      {create.isError && <Banner tone="error">{(create.error as Error).message}</Banner>}

      <Field label="Name" htmlFor="ob-name">
        <input
          id="ob-name"
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </Field>

      <Field label="Type" hint="affects wording only" htmlFor="ob-type">
        <select
          id="ob-type"
          value={type}
          onChange={(event) => setType(event.target.value as ObligationType)}
        >
          {TYPES.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Total NZD" htmlFor="ob-total">
        <input
          id="ob-total"
          type="text"
          inputMode="decimal"
          value={total}
          onChange={(event) => setTotal(event.target.value)}
        />
      </Field>

      <Field label="Already funded" hint="NZD applied so far" htmlFor="ob-funded">
        <input
          id="ob-funded"
          type="text"
          inputMode="decimal"
          value={funded}
          onChange={(event) => setFunded(event.target.value)}
        />
      </Field>

      <Field label="Interest basis" htmlFor="ob-basis">
        <select
          id="ob-basis"
          value={basis}
          onChange={(event) => setBasis(event.target.value as InterestBasis)}
        >
          <option value="simple_annual">Simple annual rate</option>
          <option value="daily_manual">Daily rate entered manually</option>
          <option value="none">No interest</option>
        </select>
      </Field>

      {basis !== 'none' && (
        <Field
          label="Annual interest rate"
          hint="as a percentage, e.g. 6.04"
          htmlFor="ob-rate"
        >
          <input
            id="ob-rate"
            type="text"
            inputMode="decimal"
            value={ratePercent}
            onChange={(event) => setRatePercent(event.target.value)}
          />
        </Field>
      )}

      <Field label="Due date" hint="optional" htmlFor="ob-due">
        <div className="fx-input-row">
          <input
            id="ob-due"
            type="date"
            value={dueDate}
            onChange={(event) => setDueDate(event.target.value)}
          />
          {/* Some browsers hide the native clear control on a date input, so
              there is an explicit one. A date added by accident has to be
              removable. */}
          <button type="button" disabled={!dueDate} onClick={() => setDueDate('')}>
            Clear
          </button>
        </div>
      </Field>

      <Field label="Priority" htmlFor="ob-priority">
        <select
          id="ob-priority"
          value={priority}
          onChange={(event) => setPriority(event.target.value as ObligationPriority)}
        >
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="normal">Normal</option>
          <option value="low">Low</option>
        </select>
      </Field>

      <Field
        label="Relationship importance"
        hint="non-financial urgency, counted separately from cost"
        htmlFor="ob-relationship"
      >
        <select
          id="ob-relationship"
          value={relationship}
          onChange={(event) => setRelationship(event.target.value as RelationshipImportance)}
        >
          <option value="none">None</option>
          <option value="moderate">Moderate</option>
          <option value="high">High</option>
        </select>
      </Field>

      <Field label="Partial payments" htmlFor="ob-partial">
        <select
          id="ob-partial"
          value={partial ? 'yes' : 'no'}
          onChange={(event) => setPartial(event.target.value === 'yes')}
        >
          <option value="yes">Allowed</option>
          <option value="no">Not allowed</option>
        </select>
      </Field>

      <Field label="Target rate" hint="optional" htmlFor="ob-target">
        <div className="fx-input-row">
          <input
            id="ob-target"
            type="text"
            inputMode="decimal"
            placeholder="e.g. 1.7800"
            value={targetRate}
            onChange={(event) => setTargetRate(event.target.value)}
          />
          <button type="button" disabled={!targetRate} onClick={() => setTargetRate('')}>
            Clear
          </button>
        </div>
      </Field>

      <Field label="Maximum acceptable wait" hint="in days; optional" htmlFor="ob-max-wait">
        <div className="fx-input-row">
          <input
            id="ob-max-wait"
            type="number"
            min={0}
            value={maxWait}
            onChange={(event) => setMaxWait(event.target.value)}
          />
          <button type="button" disabled={!maxWait} onClick={() => setMaxWait('')}>
            Clear
          </button>
        </div>
      </Field>

      <Field label="Notes" htmlFor="ob-notes">
        <textarea
          id="ob-notes"
          rows={2}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
      </Field>

      <div className="fx-toolbar">
        <button
          type="button"
          className="is-primary"
          disabled={!name || !total || create.isPending}
          onClick={() => create.mutate()}
        >
          {create.isPending ? 'Saving…' : editing ? 'Save changes' : 'Add obligation'}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </Card>
  );
}
