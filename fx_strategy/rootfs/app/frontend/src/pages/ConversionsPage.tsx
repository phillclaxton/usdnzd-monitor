import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import { Banner, Card, EmptyState, Field, Loading, Modal, Tag } from '@/components/ui';
import {
  useConversionHistory,
  useCorrectConversion,
  useDeleteConversion,
  useFxState,
  useRecordConversion,
} from '@/hooks/usePosition';
import { useSettings } from '@/hooks/useSettings';
import { ApiError, api } from '@/lib/api';
import { formatDateTime } from '@/lib/datetime';
import { formatDecimal, formatRate } from '@/lib/decimal';
import type { ConversionHistoryRow, ConversionImportPreview } from '@/types';

/**
 * The sentence that has to be on screen rather than in a tooltip.
 *
 * Correcting a historical record does not credit the balance back: the balance
 * is yours to state, and a correction is restated on the position form. The
 * moment someone edits an amount is the moment they would otherwise assume
 * otherwise, so it is said right there.
 */
const BALANCE_WARNING =
  'Editing this historical conversion does not change your current balance. ' +
  'If the balance is wrong, restate it on the Position page.';

interface FormState {
  executed_at: string;
  source_amount: string;
  target_amount: string;
  fee_source_currency: string;
  gross_rate: string;
  provider_transaction_id: string;
  notes: string;
  amounts_estimated: boolean;
}

function emptyForm(): FormState {
  return {
    executed_at: new Date().toISOString().slice(0, 16),
    source_amount: '',
    target_amount: '',
    fee_source_currency: '',
    gross_rate: '',
    provider_transaction_id: '',
    notes: '',
    amounts_estimated: false,
  };
}

function formFrom(row: ConversionHistoryRow): FormState {
  return {
    executed_at: row.executed_at.slice(0, 16),
    source_amount: row.source_amount,
    target_amount: row.target_amount,
    fee_source_currency: row.fee_source_currency ?? '',
    gross_rate: row.gross_rate,
    provider_transaction_id: '',
    notes: row.notes,
    amounts_estimated: row.amounts_estimated,
  };
}

export default function ConversionsPage() {
  const settings = useSettings();
  const state = useFxState();
  const history = useConversionHistory();
  const record = useRecordConversion();
  const correct = useCorrectConversion();
  const remove = useDeleteConversion();

  const [form, setForm] = useState<FormState>(emptyForm);
  const [editing, setEditing] = useState<ConversionHistoryRow | null>(null);
  const [editForm, setEditForm] = useState<FormState>(emptyForm);
  const [preview, setPreview] = useState<ConversionImportPreview | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const timezone = settings.data?.general.timezone ?? 'Pacific/Auckland';
  const ratePlaces = settings.data?.formatting.rate_decimal_places ?? 4;
  const source =
    state.data?.position?.source_currency ?? settings.data?.general.source_currency ?? 'USD';
  const target =
    state.data?.position?.target_currency ?? settings.data?.general.target_currency ?? 'NZD';
  const metrics = state.data?.metrics ?? null;
  const realised = history.data?.realised ?? null;
  const hasPosition = state.data?.position != null;

  const importCsv = useMutation({
    mutationFn: async ({ file, commit }: { file: File; commit: boolean }) => {
      const body = new FormData();
      body.append('file', file);
      const response = await fetch(api.url(`conversions/import?commit=${commit}`), {
        method: 'POST',
        body,
        credentials: 'same-origin',
      });
      if (!response.ok) {
        const detail = (await response.json()) as { error?: { message?: string } };
        throw new Error(detail.error?.message ?? 'Import failed.');
      }
      return (await response.json()) as ConversionImportPreview;
    },
    onSuccess: (result) => {
      setPreview(result);
      if (result.committed) void history.refetch();
    },
  });

  const submit = () => {
    record.mutate(
      {
        executed_at: new Date(form.executed_at).toISOString(),
        source_amount: form.source_amount,
        target_amount: form.target_amount,
        fee_source_currency: form.fee_source_currency || null,
        gross_rate: form.gross_rate || null,
        provider_transaction_id: form.provider_transaction_id || null,
        notes: form.notes,
        amounts_estimated: form.amounts_estimated,
      },
      { onSuccess: () => setForm(emptyForm()) },
    );
  };

  const saveEdit = () => {
    if (!editing) return;
    correct.mutate(
      {
        id: editing.id,
        body: {
          executed_at: new Date(editForm.executed_at).toISOString(),
          source_amount: editForm.source_amount,
          target_amount: editForm.target_amount,
          fee_source_currency: editForm.fee_source_currency || null,
          gross_rate: editForm.gross_rate || null,
          notes: editForm.notes,
          amounts_estimated: editForm.amounts_estimated,
          correction_reason: 'Corrected from the conversions page.',
        },
      },
      { onSuccess: () => setEditing(null) },
    );
  };

  return (
    <>
      <Card
        title="Record a conversion"
        subtitle={
          hasPosition
            ? `Enter what actually happened, from your Wise statement. This reduces your ${source} balance. Nothing here initiates a conversion.`
            : 'Enter what actually happened, from your Wise statement. Nothing here initiates a conversion.'
        }
      >
        {!hasPosition && (
          <Banner tone="warning">
            There is no position saved yet, so there is no balance to reduce. Save your position
            first.
          </Banner>
        )}
        {record.isError && <Banner tone="error">{(record.error as ApiError).message}</Banner>}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <Field label="Date and time" htmlFor="executed-at">
            <input
              id="executed-at"
              type="datetime-local"
              required
              value={form.executed_at}
              onChange={(event) => setForm({ ...form, executed_at: event.target.value })}
            />
          </Field>
          <Field label={`${source} converted`} htmlFor="source-amount">
            <input
              id="source-amount"
              type="text"
              inputMode="decimal"
              required
              placeholder="30000"
              value={form.source_amount}
              onChange={(event) => setForm({ ...form, source_amount: event.target.value })}
            />
          </Field>
          <Field
            label={`${target} received`}
            hint="the amount that reached your account"
            htmlFor="target-amount"
          >
            <input
              id="target-amount"
              type="text"
              inputMode="decimal"
              required
              placeholder="52500"
              value={form.target_amount}
              onChange={(event) => setForm({ ...form, target_amount: event.target.value })}
            />
          </Field>
          <Field label={`Fee (${source})`} hint="optional" htmlFor="fee">
            <input
              id="fee"
              type="text"
              inputMode="decimal"
              placeholder="115.43"
              value={form.fee_source_currency}
              onChange={(event) => setForm({ ...form, fee_source_currency: event.target.value })}
            />
          </Field>
          <Field
            label="Rate Wise displayed"
            hint="optional — the effective rate is calculated from the amounts either way"
            htmlFor="gross-rate"
          >
            <input
              id="gross-rate"
              type="text"
              inputMode="decimal"
              placeholder="1.7500"
              value={form.gross_rate}
              onChange={(event) => setForm({ ...form, gross_rate: event.target.value })}
            />
          </Field>
          <Field label="Transaction ID" hint="optional, prevents double entry" htmlFor="txn">
            <input
              id="txn"
              type="text"
              value={form.provider_transaction_id}
              onChange={(event) =>
                setForm({ ...form, provider_transaction_id: event.target.value })
              }
            />
          </Field>
          <Field label="Notes" htmlFor="notes">
            <textarea
              id="notes"
              rows={2}
              value={form.notes}
              onChange={(event) => setForm({ ...form, notes: event.target.value })}
            />
          </Field>
          <div className="fx-inline">
            <input
              id="estimated"
              type="checkbox"
              checked={form.amounts_estimated}
              onChange={(event) => setForm({ ...form, amounts_estimated: event.target.checked })}
            />
            <label htmlFor="estimated">
              One of these amounts is my reconstruction, not a figure off a receipt
            </label>
          </div>

          <div className="fx-toolbar" style={{ marginTop: 'var(--fx-gap)' }}>
            <button type="submit" className="is-primary" disabled={record.isPending}>
              {record.isPending ? 'Recording…' : 'Record conversion'}
            </button>
          </div>
        </form>
      </Card>

      <Card
        title="Import from CSV"
        subtitle="Required columns: executed_at, source_amount, target_amount. Imported rows are history and do not change your balance."
        actions={
          <a
            href={api.url('conversions/export')}
            download
            className="fx-tag"
            style={{ textDecoration: 'none' }}
          >
            Export CSV
          </a>
        }
      >
        {importCsv.isError && <Banner tone="error">{(importCsv.error as Error).message}</Banner>}
        <input ref={fileInput} type="file" accept=".csv,text/csv" aria-label="CSV file" />
        <div className="fx-toolbar" style={{ marginTop: 8 }}>
          <button
            type="button"
            onClick={() => {
              const file = fileInput.current?.files?.[0];
              if (file) importCsv.mutate({ file, commit: false });
            }}
            disabled={importCsv.isPending}
          >
            Preview
          </button>
          <button
            type="button"
            className="is-primary"
            disabled={!preview || preview.accepted === 0 || importCsv.isPending}
            onClick={() => {
              const file = fileInput.current?.files?.[0];
              if (file) importCsv.mutate({ file, commit: true });
            }}
          >
            Import {preview ? `${preview.accepted} row(s)` : ''}
          </button>
        </div>

        {preview && (
          <div style={{ marginTop: 'var(--fx-gap)' }}>
            <p className="fx-stat-note">
              {preview.total_rows} row(s) read · {preview.accepted} importable · {preview.rejected}{' '}
              rejected · {preview.duplicates} already recorded
              {preview.committed ? ` · ${preview.imported} imported` : ''}
            </p>
            {preview.errors.length > 0 && (
              <ul className="fx-stat-note" style={{ paddingLeft: 18 }}>
                {preview.errors.map((error, index) => (
                  <li key={index}>
                    {error.row ? `Row ${error.row}: ` : ''}
                    {error.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>

      <Card title="Recorded conversions">
        {history.isLoading && <Loading />}
        {history.data && history.data.conversions.length === 0 && (
          <EmptyState glyph="💱" title="Nothing recorded yet">
            <p>
              When Wise completes a conversion, record it here so your balance and realised
              improvement stay accurate.
            </p>
          </EmptyState>
        )}
        {history.data && history.data.conversions.length > 0 && (
          <>
            <div className="fx-grid" style={{ marginBottom: 'var(--fx-gap)' }}>
              <div className="fx-stat">
                <div className="fx-stat-label">Total converted</div>
                <div className="fx-stat-value is-small">
                  {source} {formatDecimal(metrics?.total_source_converted)}
                </div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Total received</div>
                <div className="fx-stat-value is-small">
                  {target} {formatDecimal(metrics?.total_target_received)}
                </div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">
                  <span>Realised improvement</span>
                  {realised?.includes_estimates && <Tag quality="estimate" />}
                </div>
                <div className="fx-stat-value is-small">
                  {history.data.baseline_rate === null
                    ? 'Set a baseline rate'
                    : `${target} ${formatDecimal(realised?.confirmed)}`}
                </div>
                {realised?.includes_estimates && (
                  <div className="fx-stat-note">
                    plus about {target} {formatDecimal(realised.estimated)} from rows whose amounts
                    are estimated
                  </div>
                )}
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Fees recorded</div>
                <div className="fx-stat-value is-small">
                  {metrics?.total_fees_target == null
                    ? 'None recorded'
                    : formatDecimal(metrics.total_fees_target)}
                </div>
              </div>
            </div>

            <div className="fx-table-wrap">
              <table className="fx-table">
                <thead>
                  <tr>
                    <th className="fx-left">Executed</th>
                    <th>{source}</th>
                    <th>{target}</th>
                    <th>Rate</th>
                    <th>Effective</th>
                    <th>Fee</th>
                    <th>Improvement</th>
                    <th>Cumulative</th>
                    <th className="fx-left">Source</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {history.data.conversions.map((row) => (
                    <tr key={row.id}>
                      <td className="fx-left">{formatDateTime(row.executed_at, timezone)}</td>
                      <td>{formatDecimal(row.source_amount)}</td>
                      <td>{formatDecimal(row.target_amount)}</td>
                      <td>{formatRate(row.gross_rate, ratePlaces)}</td>
                      <td>{formatRate(row.effective_rate, ratePlaces)}</td>
                      <td>
                        {row.fee_source_currency === null
                          ? 'Not recorded'
                          : formatDecimal(row.fee_source_currency)}
                      </td>
                      <td>
                        {row.improvement === null ? '—' : formatDecimal(row.improvement)}
                        {row.fee_unrecorded && row.improvement !== null && (
                          <>
                            {' '}
                            <Tag quality="warning">No fee</Tag>
                          </>
                        )}
                      </td>
                      <td>
                        {row.cumulative_improvement === null
                          ? '—'
                          : formatDecimal(row.cumulative_improvement)}
                      </td>
                      <td className="fx-left">
                        {row.amounts_estimated && <Tag quality="estimate" />}
                        {row.simulated ? (
                          <Tag quality="warning">Simulated</Tag>
                        ) : (
                          !row.amounts_estimated && <Tag quality="actual">{row.record_source}</Tag>
                        )}
                      </td>
                      <td>
                        <div className="fx-toolbar">
                          <button
                            type="button"
                            onClick={() => {
                              setEditing(row);
                              setEditForm(formFrom(row));
                            }}
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            className="is-danger"
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Deleting a financial record. The audit trail keeps its values.\n\n${BALANCE_WARNING}`,
                                )
                              ) {
                                remove.mutate(row.id);
                              }
                            }}
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>

      {editing && (
        <Modal
          title={`Edit conversion ${editing.id}`}
          onClose={() => setEditing(null)}
          footer={
            <>
              <button type="button" onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="is-primary"
                disabled={correct.isPending}
                onClick={saveEdit}
              >
                {correct.isPending ? 'Saving…' : 'Save correction'}
              </button>
            </>
          }
        >
          <Banner tone="warning">{BALANCE_WARNING}</Banner>
          {correct.isError && <Banner tone="error">{(correct.error as ApiError).message}</Banner>}
          <Field label="Date and time" htmlFor="edit-executed-at">
            <input
              id="edit-executed-at"
              type="datetime-local"
              value={editForm.executed_at}
              onChange={(event) => setEditForm({ ...editForm, executed_at: event.target.value })}
            />
          </Field>
          <Field label={`${source} converted`} htmlFor="edit-source-amount">
            <input
              id="edit-source-amount"
              type="text"
              inputMode="decimal"
              value={editForm.source_amount}
              onChange={(event) => setEditForm({ ...editForm, source_amount: event.target.value })}
            />
          </Field>
          <Field label={`${target} received`} htmlFor="edit-target-amount">
            <input
              id="edit-target-amount"
              type="text"
              inputMode="decimal"
              value={editForm.target_amount}
              onChange={(event) => setEditForm({ ...editForm, target_amount: event.target.value })}
            />
          </Field>
          <Field
            label={`Fee (${source})`}
            hint="leave empty if it was never recorded"
            htmlFor="edit-fee"
          >
            <input
              id="edit-fee"
              type="text"
              inputMode="decimal"
              value={editForm.fee_source_currency}
              onChange={(event) =>
                setEditForm({ ...editForm, fee_source_currency: event.target.value })
              }
            />
          </Field>
          <Field label="Rate Wise displayed" htmlFor="edit-gross-rate">
            <input
              id="edit-gross-rate"
              type="text"
              inputMode="decimal"
              value={editForm.gross_rate}
              onChange={(event) => setEditForm({ ...editForm, gross_rate: event.target.value })}
            />
          </Field>
          <Field label="Notes" htmlFor="edit-notes">
            <textarea
              id="edit-notes"
              rows={2}
              value={editForm.notes}
              onChange={(event) => setEditForm({ ...editForm, notes: event.target.value })}
            />
          </Field>
          <div className="fx-inline">
            <input
              id="edit-estimated"
              type="checkbox"
              checked={editForm.amounts_estimated}
              onChange={(event) =>
                setEditForm({ ...editForm, amounts_estimated: event.target.checked })
              }
            />
            <label htmlFor="edit-estimated">
              One of these amounts is my reconstruction, not a figure off a receipt
            </label>
          </div>
          <p className="fx-stat-note">
            Clearing that box once you have the real receipt removes this row from the estimated
            total on its own.
          </p>
        </Modal>
      )}
    </>
  );
}
