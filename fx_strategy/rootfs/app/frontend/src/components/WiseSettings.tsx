import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Banner, Card, Field, Loading } from '@/components/ui';
import { api } from '@/lib/api';
import { formatDecimal } from '@/lib/decimal';
import type { WiseStatus, ReconcileResult, WiseBalance } from '@/types';

export default function WiseSettingsPanel() {
  const queryClient = useQueryClient();
  const [token, setToken] = useState('');
  const [profileId, setProfileId] = useState('');
  const [balanceId, setBalanceId] = useState('');
  const [reconcile, setReconcile] = useState<ReconcileResult | null>(null);

  const status = useQuery({
    queryKey: ['wise', 'status'],
    queryFn: () => api.get<WiseStatus>('wise/status'),
  });

  const balances = useQuery({
    queryKey: ['wise', 'balances'],
    queryFn: () => api.get<WiseBalance[]>('wise/balances'),
    enabled: status.data?.connected === true,
    retry: false,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['wise'] });
    void queryClient.invalidateQueries({ queryKey: ['strategy'] });
    void queryClient.invalidateQueries({ queryKey: ['conversions'] });
  };

  const save = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api.put<WiseStatus>('wise/credentials', payload),
    onSuccess: () => {
      setToken('');
      invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: () => api.del<WiseStatus>('wise/credentials'),
    onSuccess: invalidate,
  });

  const test = useMutation({
    mutationFn: () => api.post<WiseStatus>('wise/test'),
    onSuccess: invalidate,
  });

  const runReconcile = useMutation({
    mutationFn: (commit: boolean) =>
      api.post<ReconcileResult>(`wise/reconcile?commit=${commit}`),
    onSuccess: (result) => {
      setReconcile(result);
      if (!result.dry_run) invalidate();
    },
  });

  const current = status.data;

  return (
    <>
      <Card
        title="Wise"
        subtitle="Read-only. This app reads rates, balances and completed conversions; it never converts or transfers anything."
      >
        {status.isLoading && <Loading />}
        {current && (
          <Banner tone={current.connected ? 'info' : current.configured ? 'warning' : 'info'}>
            {current.configured ? (
              <>
                Token stored ({current.token_hint}) ·{' '}
                {current.connected ? 'connected' : 'not connected'} · {current.message}
              </>
            ) : (
              'No Wise API token is stored. Wise features are unavailable until one is added.'
            )}
          </Banner>
        )}

        {save.isError && <Banner tone="error">{(save.error as Error).message}</Banner>}

        <Field
          label="Wise API token"
          hint="stored encrypted outside the database, never shown again"
          htmlFor="wise-token"
        >
          <input
            id="wise-token"
            type="password"
            autoComplete="off"
            placeholder={current?.configured ? current.token_hint : 'Paste your API token'}
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
        </Field>
        <Field label="Profile ID" htmlFor="wise-profile">
          <input
            id="wise-profile"
            type="text"
            placeholder={current?.profile_id || 'e.g. 12345678'}
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
          />
          {current && current.profiles.length > 0 && (
            <span className="fx-hint">
              Available: {current.profiles.map((p) => `${p.id} (${p.type})`).join(', ')}
            </span>
          )}
        </Field>
        <Field
          label="Source balance ID"
          hint="the balance conversions are read from"
          htmlFor="wise-balance"
        >
          <input
            id="wise-balance"
            type="text"
            value={balanceId}
            onChange={(event) => setBalanceId(event.target.value)}
          />
        </Field>

        <div className="fx-toolbar">
          <button
            type="button"
            className="is-primary"
            disabled={save.isPending}
            onClick={() =>
              save.mutate({
                api_token: token || null,
                profile_id: profileId || null,
                source_balance_id: balanceId || null,
                enabled: true,
              })
            }
          >
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
          <button type="button" onClick={() => test.mutate()} disabled={test.isPending}>
            {test.isPending ? 'Testing…' : 'Test connection'}
          </button>
          {current?.configured && (
            <button
              type="button"
              className="is-danger"
              onClick={() => {
                if (window.confirm('Remove the stored Wise token?')) remove.mutate();
              }}
            >
              Remove token
            </button>
          )}
        </div>

        <p className="fx-stat-note" style={{ marginTop: 'var(--fx-gap)' }}>
          {current?.notice ??
            'This application does not automatically convert or transfer money.'}
        </p>
      </Card>

      {current?.connected && (
        <Card title="Balances">
          {balances.isLoading && <Loading />}
          {balances.isError && (
            <Banner tone="warning">{(balances.error as Error).message}</Banner>
          )}
          {balances.data && (
            <div className="fx-table-wrap">
              <table className="fx-table">
                <thead>
                  <tr>
                    <th className="fx-left">Balance</th>
                    <th className="fx-left">Currency</th>
                    <th>Amount</th>
                    <th>Reserved</th>
                  </tr>
                </thead>
                <tbody>
                  {balances.data.map((balance) => (
                    <tr key={balance.balance_id}>
                      <td className="fx-left">{balance.balance_id}</td>
                      <td className="fx-left">{balance.currency}</td>
                      <td>{formatDecimal(balance.amount)}</td>
                      <td>{formatDecimal(balance.reserved)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      <Card
        title="Reconcile with Wise"
        subtitle="Compares completed Wise conversions with what is recorded here. Matching is on the Wise reference, so running it twice imports nothing twice."
      >
        {runReconcile.isError && (
          <Banner tone="error">{(runReconcile.error as Error).message}</Banner>
        )}
        <div className="fx-toolbar">
          <button
            type="button"
            onClick={() => runReconcile.mutate(false)}
            disabled={runReconcile.isPending || !current?.connected}
          >
            Preview
          </button>
          <button
            type="button"
            className="is-primary"
            onClick={() => runReconcile.mutate(true)}
            disabled={
              runReconcile.isPending ||
              !current?.connected ||
              !reconcile ||
              reconcile.imported_references.length === 0
            }
          >
            Import {reconcile ? `${reconcile.imported_references.length}` : ''}
          </button>
        </div>

        {reconcile && (
          <div style={{ marginTop: 'var(--fx-gap)' }}>
            <p className="fx-stat-note">
              {reconcile.fetched} conversion(s) read · {reconcile.matched} already recorded ·{' '}
              {reconcile.dry_run
                ? `${reconcile.imported_references.length} would be imported`
                : `${reconcile.imported} imported`}
              {reconcile.skipped_other_pair > 0 &&
                ` · ${reconcile.skipped_other_pair} for a different currency pair, skipped`}
            </p>
            {reconcile.imported_references.length > 0 && (
              <p className="fx-stat-note">
                References: {reconcile.imported_references.join(', ')}
              </p>
            )}
            {reconcile.errors.length > 0 && (
              <ul className="fx-stat-note" style={{ paddingLeft: 18 }}>
                {reconcile.errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>

      <Card title="Automatic conversion">
        <Banner tone="info">
          This application does not execute conversions. There is no endpoint that would, and
          the only shipped executor refuses. Create Auto Conversions in Wise; this app tells you
          when a target is reached and records what happened.
        </Banner>
        <p className="fx-stat-note">
          The rate this app reads from Wise is the mid-market reference rate, not the rate a
          transfer settles at. Request a quote to see the fee and the amount you would actually
          receive — it is still only an estimate, and this app will not act on it.
        </p>
      </Card>
    </>
  );
}
