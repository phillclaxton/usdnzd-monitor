import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Dashboard from '@/pages/Dashboard';
import { api } from '@/lib/api';
import type { CurrentRate, FxAlert, FxState, Settings } from '@/types';

/**
 * The dashboard.
 *
 * What is pinned here is what the screen must not do: claim a gain it cannot
 * compute, present an estimate as a fact, hide an alert that failed to send, or
 * meet a fresh install with nothing to act on.
 */

const SETTINGS = {
  general: { timezone: 'UTC', source_currency: 'USD', target_currency: 'NZD' },
  formatting: { rate_decimal_places: 4 },
} as unknown as Settings;

const RATE = {
  source_currency: 'USD',
  target_currency: 'NZD',
  rate: '1.70000000',
  status: 'live',
  provider: 'manual',
  quote_type: 'mid_market',
  quote_label: 'Mid-market reference rate',
  provider_timestamp: '2026-09-17T00:00:00Z',
  retrieved_at: '2026-09-17T00:00:30Z',
  age_seconds: 30,
  stale_after_seconds: 900,
  changes: {
    one_hour: '0.00120000',
    twenty_four_hours: '0.00500000',
    seven_days: null,
    thirty_days: null,
  },
  high_24h: '1.71000000',
  low_24h: '1.69000000',
  high_6m: '1.81000000',
  low_6m: '1.66000000',
  disagreement_warning: null,
  message: null,
} satisfies CurrentRate;

function fxState(overrides: Partial<FxState> = {}): FxState {
  return {
    position: {
      source_currency: 'USD',
      target_currency: 'NZD',
      current_source_balance: '500000.0000',
      baseline_rate: '1.60000000',
      baseline_date: '2026-01-31',
      floating_loan_rate: '0.06000000',
      current_offset_shortfall_nzd: '36500.0000',
      monthly_nzd_burn: '4000.0000',
      notes: '',
      updated_at: '2026-09-17T00:00:00Z',
    },
    metrics: {
      current_rate: '1.70000000',
      rate_status: 'live',
      current_target_value: '850000.0000',
      realised: {
        confirmed: '10732.5000',
        estimated: '6000.0000',
        total: '16732.5000',
        includes_estimates: true,
      },
      unrealised_improvement: '50000.0000',
      total_improvement_confirmed: '60732.5000',
      total_source_converted: '125000.0000',
      total_target_received: '216492.5000',
      total_fees_target: '257.5000',
      daily_carrying_cost: '6.0000',
      monthly_carrying_cost: '182.5000',
      months_of_burn: '212.5000',
    },
    latest_conversion: {
      id: 4,
      executed_at: '2026-08-11T00:00:00Z',
      source_amount: '30000.0000',
      target_amount: '54000.0000',
      gross_rate: '1.80000000',
      amounts_estimated: true,
    },
    conversion_count: 4,
    ...overrides,
  };
}

const ALERT: FxAlert = {
  id: 7,
  rule_type: 'fx_absolute_move',
  severity: 'info',
  title: 'USD/NZD is up half a cent',
  message: 'The rate moved from 1.6950 to 1.7000 — NZD 2,500.00 on what you hold.',
  entity_id: 'absolute_move',
  created_at: '2026-09-17T00:00:00Z',
  delivered: true,
  rate: '1.70000000',
  reference_value: '1.69500000',
  money_value: null,
};

let state: FxState;
let alerts: FxAlert[];

function renderDashboard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  state = fxState();
  alerts = [ALERT];
  vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
    if (path === 'settings') return SETTINGS as never;
    if (path === 'fx/state') return state as never;
    if (path.startsWith('fx/alerts')) return alerts as never;
    if (path === 'rates/current') return RATE as never;
    if (path === 'health') return { version: '2.0.0', database: 'ok' } as never;
    return {} as never;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Dashboard', () => {
  it('shows what the position is worth and what waiting for it costs', async () => {
    renderDashboard();
    expect(await screen.findByText('NZD 850,000.00')).toBeInTheDocument();
    expect(screen.getByText('NZD 50,000.00')).toBeInTheDocument();
    expect(screen.getByText('NZD 60,732.50')).toBeInTheDocument();
    expect(screen.getByText('NZD 6.00')).toBeInTheDocument();
    expect(screen.getByText('NZD 182.50 a month')).toBeInTheDocument();
  });

  it('marks a realised total that includes an estimate', async () => {
    renderDashboard();
    expect(await screen.findByText('NZD 10,732.50')).toBeInTheDocument();
    expect(
      screen.getByText(/plus about NZD 6,000.00 from rows whose amounts are estimated/),
    ).toBeInTheDocument();
  });

  it('says what is missing rather than showing a zero', async () => {
    state = fxState({
      metrics: {
        ...fxState().metrics!,
        current_rate: null,
        current_target_value: null,
        unrealised_improvement: null,
        total_improvement_confirmed: null,
        total_fees_target: null,
        realised: { confirmed: null, estimated: null, total: null, includes_estimates: false },
        daily_carrying_cost: null,
        monthly_carrying_cost: null,
        months_of_burn: null,
      },
    });
    renderDashboard();
    expect(await screen.findByText('No rate yet')).toBeInTheDocument();
    expect(screen.getAllByText('Set a baseline rate').length).toBeGreaterThan(0);
    expect(screen.getByText('Set a shortfall and loan rate')).toBeInTheDocument();
    // "0.00" would be a different and wrong claim.
    expect(screen.queryByText('NZD 0.00')).not.toBeInTheDocument();
  });

  it('offers the position form when nothing is saved', async () => {
    state = fxState({
      position: null,
      metrics: null,
      latest_conversion: null,
      conversion_count: 0,
    });
    renderDashboard();
    expect(await screen.findByText(/Nothing is saved yet/)).toBeInTheDocument();
    // The form itself, not a link to it: a fresh install has nothing else to do.
    expect(screen.getByRole('button', { name: 'Save position' })).toBeInTheDocument();
  });

  it('shows an alert that failed to send rather than hiding it', async () => {
    alerts = [{ ...ALERT, delivered: false }];
    renderDashboard();
    expect(await screen.findByText('USD/NZD is up half a cent')).toBeInTheDocument();
    expect(screen.getByText('Not delivered')).toBeInTheDocument();
  });

  it('never tells you what to convert', async () => {
    renderDashboard();
    await screen.findByText('NZD 850,000.00');
    const words = /convert now|you should|recommend|next target|tranche/i;
    expect(document.body.textContent).not.toMatch(words);
  });
});
