import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Banner, Card, EmptyState, Field, Loading, Tag } from '@/components/ui';
import { useSettings } from '@/hooks/useSettings';
import { useStrategies } from '@/hooks/useStrategy';
import { ApiError, api } from '@/lib/api';
import { formatDateTime } from '@/lib/datetime';
import { formatDecimal, formatRate, roundTo } from '@/lib/decimal';
import type { Conversion, ConversionList, ConversionImportPreview } from '@/types';

interface FormState {
  executed_at: string;
  source_amount: string;
  target_amount: string;
  fee_target_currency: string;
  gross_rate: string;
  provider_transaction_id: string;
  tranche_id: string;
  notes: string;
  simulated: boolean;
  correcting_earlier_record: boolean;
}

const EMPTY: FormState = {
  executed_at: new Date().toISOString().slice(0, 16),
  source_amount: '',
  target_amount: '',
  fee_target_currency: '',
  gross_rate: '',
  provider_transaction_id: '',
  tranche_id: '',
  notes: '',
  simulated: false,
  correcting_earlier_record: false,
};

export default function ConversionsPage() {
  const settings = useSettings();
  const strategies = useStrategies();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [preview, setPreview] = useState<ConversionImportPreview | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const strategy = strategies.data?.[0] ?? null;
  const strategyId = strategy?.id ?? null;
  const timezone = settings.data?.general.timezone ?? 'Pacific/Auckland';
  const ratePlaces = settings.data?.formatting.rate_decimal_places ?? 4;

  const list = useQuery({
    queryKey: ['conversions', strategyId],
    queryFn: () => api.get<ConversionList>(`conversions?strategy_id=${strategyId}`),
    enabled: strategyId !== null,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['conversions'] });
    void queryClient.invalidateQueries({ queryKey: ['strategy'] });
  };

  const record = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api.post<Conversion[]>('conversions', payload),
    onSuccess: () => {
      setForm(EMPTY);
      invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) =>
      api.del(`conversions/${id}?reason=${encodeURIComponent(reason)}`),
    onSuccess: invalidate,
  });

  const importCsv = useMutation({
    mutationFn: async ({ file, commit }: { file: File; commit: boolean }) => {
      const body = new FormData();
      body.append('file', file);
      const response = await fetch(
        api.url(`conversions/import?strategy_id=${strategyId}&commit=${commit}`),
        { method: 'POST', body, credentials: 'same-origin' },
      );
      if (!response.ok) {
        const detail = (await response.json()) as { error?: { message?: string } };
        throw new Error(detail.error?.message ?? 'Import failed.');
      }
      return (await response.json()) as ConversionImportPreview;
    },
    onSuccess: (result) => {
      setPreview(result);
      if (result.committed) invalidate();
    },
  });

  if (strategies.isLoading) return <Loading label="Loading…" />;
  if (!strategy) {
    return (
      <Card title="Conversions">
        <EmptyState glyph="💱" title="No strategy yet">
          <p>Create a strategy before recording conversions against it.</p>
        </EmptyState>
      </Card>
    );
  }

  const source = strategy.source_currency;
  const target = strategy.target_currency;
  const impliedRate =
    form.source_amount && form.target_amount && Number(form.source_amount) > 0
      ? roundTo(String(Number(form.target_amount) / Number(form.source_amount)), ratePlaces)
      : null;

  const submit = () => {
    record.mutate({
      strategy_id: strategy.id,
      executed_at: new Date(form.executed_at).toISOString(),
      source_amount: form.source_amount,
      target_amount: form.target_amount,
      fee_target_currency: form.fee_target_currency || null,
      gross_rate: form.gross_rate || null,
      provider_transaction_id: form.provider_transaction_id || null,
      tranche_id: form.tranche_id ? Number(form.tranche_id) : null,
      notes: form.notes,
      simulated: form.simulated,
      correcting_earlier_record: form.correcting_earlier_record,
      provider: 'wise',
      record_source: 'manual',
    });
  };

  return (
    <>
      <Card
        title="Record a conversion"
        subtitle="Enter what actually happened, from your Wise statement. Nothing here initiates a conversion."
      >
        {record.isError && (
          <Banner tone="error">{(record.error as ApiError).message}</Banner>
        )}
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
              placeholder="120000"
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
              placeholder="207840"
              value={form.target_amount}
              onChange={(event) => setForm({ ...form, target_amount: event.target.value })}
            />
          </Field>
          <Field label={`Wise fee (${target})`} hint="optional" htmlFor="fee">
            <input
              id="fee"
              type="text"
              inputMode="decimal"
              placeholder="520"
              value={form.fee_target_currency}
              onChange={(event) =>
                setForm({ ...form, fee_target_currency: event.target.value })
              }
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
              placeholder="1.7320"
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
          <Field label="Assign to tranche" htmlFor="tranche">
            <select
              id="tranche"
              value={form.tranche_id}
              onChange={(event) => setForm({ ...form, tranche_id: event.target.value })}
            >
              <option value="">Unassigned</option>
              {strategy.tranches.map((tranche) => (
                <option key={tranche.id} value={tranche.id}>
                  Tranche {tranche.sequence} — target{' '}
                  {formatRate(tranche.target_rate, ratePlaces)}
                </option>
              ))}
            </select>
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
              id="simulated"
              type="checkbox"
              checked={form.simulated}
              onChange={(event) => setForm({ ...form, simulated: event.target.checked })}
            />
            <label htmlFor="simulated">
              Mark as simulated — excluded from your real position
            </label>
          </div>
          <div className="fx-inline" style={{ marginTop: 8 }}>
            <input
              id="correcting"
              type="checkbox"
              checked={form.correcting_earlier_record}
              onChange={(event) =>
                setForm({ ...form, correcting_earlier_record: event.target.checked })
              }
            />
            <label htmlFor="correcting">
              I am correcting an earlier record (allows exceeding the remaining balance)
            </label>
          </div>

          {impliedRate && (
            <p className="fx-stat-note">
              Effective rate: <strong>{impliedRate}</strong> {target} per 1 {source}
            </p>
          )}

          <div className="fx-toolbar" style={{ marginTop: 'var(--fx-gap)' }}>
            <button type="submit" className="is-primary" disabled={record.isPending}>
              {record.isPending ? 'Recording…' : 'Record conversion'}
            </button>
          </div>
        </form>
      </Card>

      <Card
        title="Import from CSV"
        subtitle="Required columns: executed_at, source_amount, target_amount."
        actions={
          <a
            href={api.url(`conversions/export?strategy_id=${strategy.id}`)}
            download
            className="fx-tag"
            style={{ textDecoration: 'none' }}
          >
            Export CSV
          </a>
        }
      >
        {importCsv.isError && (
          <Banner tone="error">{(importCsv.error as Error).message}</Banner>
        )}
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
              {preview.total_rows} row(s) read · {preview.accepted} importable ·{' '}
              {preview.rejected} rejected · {preview.duplicates} already recorded
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
        {list.isLoading && <Loading />}
        {list.data && list.data.conversions.length === 0 && (
          <EmptyState glyph="💱" title="Nothing recorded yet">
            <p>
              When Wise completes a conversion, record it here so your remaining balance and
              blended rate stay accurate.
            </p>
          </EmptyState>
        )}
        {list.data && list.data.conversions.length > 0 && (
          <>
            <div className="fx-grid" style={{ marginBottom: 'var(--fx-gap)' }}>
              <div className="fx-stat">
                <div className="fx-stat-label">Total converted</div>
                <div className="fx-stat-value is-small">
                  {source} {formatDecimal(list.data.total_source_amount)}
                </div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Total received</div>
                <div className="fx-stat-value is-small">
                  {target} {formatDecimal(list.data.total_target_amount)}
                </div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">
                  <span>Blended effective rate</span>
                  <Tag quality="actual" />
                </div>
                <div className="fx-stat-value is-small">
                  {formatRate(list.data.blended_effective_rate, ratePlaces)}
                </div>
              </div>
              <div className="fx-stat">
                <div className="fx-stat-label">Fees recorded</div>
                <div className="fx-stat-value is-small">
                  {list.data.total_fees === null
                    ? 'None recorded'
                    : formatDecimal(list.data.total_fees)}
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
                    <th className="fx-left">Tranche</th>
                    <th className="fx-left">Reference</th>
                    <th className="fx-left">Source</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.data.conversions.map((row) => (
                    <tr key={row.id}>
                      <td className="fx-left">{formatDateTime(row.executed_at, timezone)}</td>
                      <td>{formatDecimal(row.source_amount)}</td>
                      <td>{formatDecimal(row.target_amount)}</td>
                      <td>{formatRate(row.gross_rate, ratePlaces)}</td>
                      <td>{formatRate(row.effective_rate, ratePlaces)}</td>
                      <td>
                        {row.fee_total_target_equivalent === null
                          ? 'Not recorded'
                          : formatDecimal(row.fee_total_target_equivalent)}
                      </td>
                      <td className="fx-left">
                        {strategy.tranches.find((t) => t.id === row.tranche_id)?.sequence ?? '—'}
                      </td>
                      <td className="fx-left">{row.provider_transaction_id ?? '—'}</td>
                      <td className="fx-left">
                        {row.simulated ? (
                          <Tag quality="warning">Simulated</Tag>
                        ) : (
                          <Tag quality="actual">{row.record_source}</Tag>
                        )}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="is-danger"
                          onClick={() => {
                            const reason = window.prompt(
                              'Deleting a financial record. The audit trail keeps its values.\n\nWhy are you deleting it?',
                              '',
                            );
                            if (reason !== null) remove.mutate({ id: row.id, reason });
                          }}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
