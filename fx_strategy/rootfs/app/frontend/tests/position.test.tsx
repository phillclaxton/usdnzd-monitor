import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ConversionsPage from '@/pages/ConversionsPage';
import PositionPage from '@/pages/PositionPage';
import { api } from '@/lib/api';
import type { ConversionHistory, FxState, Settings } from '@/types';

/**
 * The position screens.
 *
 * What is pinned here is the handful of rules that would otherwise quietly
 * mislead someone: a `null` metric says what is missing rather than reading
 * `0.00`, an estimated total is never shown as a confirmed one, and editing a
 * historical record says on screen that it does not move the balance.
 */

const SETTINGS = {
  general: { timezone: 'UTC', source_currency: 'USD', target_currency: 'NZD' },
  formatting: { rate_decimal_places: 4 },
} as unknown as Settings;

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
      total_fees_target: '255.0000',
      daily_carrying_cost: '6.0000',
      monthly_carrying_cost: '182.5000',
      months_of_burn: '212.5000',
    },
    latest_conversion: null,
    conversion_count: 4,
    ...overrides,
  };
}

function history(overrides: Partial<ConversionHistory> = {}): ConversionHistory {
  return {
    baseline_rate: '1.60000000',
    realised: {
      confirmed: '10732.5000',
      estimated: '6000.0000',
      total: '16732.5000',
      includes_estimates: true,
    },
    conversions: [
      {
        id: 4,
        executed_at: '2026-08-11T00:00:00Z',
        source_amount: '30000.0000',
        target_amount: '54000.0000',
        gross_rate: '1.80000000',
        effective_rate: '1.80000000',
        fee_source_currency: null,
        fee_total_target_equivalent: null,
        provider: 'wise',
        record_source: 'manual',
        notes: '',
        amounts_estimated: true,
        simulated: false,
        improvement: '6000.0000',
        cumulative_improvement: '16732.5000',
        fee_unrecorded: true,
      },
      {
        id: 1,
        executed_at: '2026-02-14T00:00:00Z',
        source_amount: '50000.0000',
        target_amount: '84830.0000',
        gross_rate: '1.70000000',
        effective_rate: '1.69660000',
        fee_source_currency: '100.0000',
        fee_total_target_equivalent: '170.0000',
        provider: 'wise',
        record_source: 'manual',
        notes: '',
        amounts_estimated: false,
        simulated: false,
        improvement: '4990.0000',
        cumulative_improvement: '4990.0000',
        fee_unrecorded: false,
      },
    ],
    ...overrides,
  };
}

let state: FxState;
let conversions: ConversionHistory;

function renderPage(element: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

beforeEach(() => {
  state = fxState();
  conversions = history();
  vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
    if (path === 'settings') return SETTINGS as never;
    if (path === 'fx/state') return state as never;
    if (path === 'fx/conversions') return conversions as never;
    if (path === 'rates/current') return { rate: '1.70000000' } as never;
    return {} as never;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// A missing figure says what is missing
// ---------------------------------------------------------------------------

describe('PositionPage', () => {
  it('shows what the position comes to', async () => {
    renderPage(<PositionPage />);
    expect(await screen.findByText('NZD 850,000.00')).toBeInTheDocument();
    expect(screen.getByText('NZD 50,000.00')).toBeInTheDocument();
    expect(screen.getByText('NZD 6.00')).toBeInTheDocument();
    expect(screen.getByText('NZD 182.50 a month')).toBeInTheDocument();
  });

  it('says what is missing rather than showing a zero', async () => {
    state = fxState({
      metrics: {
        ...fxState().metrics!,
        current_rate: null,
        current_target_value: null,
        unrealised_improvement: null,
        realised: {
          confirmed: null,
          estimated: null,
          total: null,
          includes_estimates: false,
        },
        daily_carrying_cost: null,
        monthly_carrying_cost: null,
      },
    });
    renderPage(<PositionPage />);
    expect(await screen.findByText('No rate yet')).toBeInTheDocument();
    expect(screen.getAllByText('Set a baseline rate').length).toBeGreaterThan(0);
    expect(screen.getByText('Set a shortfall and loan rate')).toBeInTheDocument();
    // "0.00" would be a different and wrong claim.
    expect(screen.queryByText('NZD 0.00')).not.toBeInTheDocument();
  });

  it('marks a realised total that includes an estimate', async () => {
    renderPage(<PositionPage />);
    expect(await screen.findByText('NZD 10,732.50')).toBeInTheDocument();
    expect(
      screen.getByText(/plus about NZD 6,000.00 from rows whose amounts are estimated/),
    ).toBeInTheDocument();
  });

  it('refuses a loan rate typed as a percentage', async () => {
    renderPage(<PositionPage />);
    const field = await screen.findByLabelText(/Floating loan rate/);
    await userEvent.clear(field);
    await userEvent.type(field, '6.04');
    expect(
      screen.getByText('The loan rate is a fraction, not a percentage: enter 6.04% as 0.0604.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save position' })).toBeDisabled();
  });

  it('invites you to fill it in when nothing is saved', async () => {
    state = fxState({ position: null, metrics: null, conversion_count: 0 });
    renderPage(<PositionPage />);
    expect(await screen.findByText(/Nothing is saved yet/)).toBeInTheDocument();
  });

  it('every money input takes text, never a number', async () => {
    renderPage(<PositionPage />);
    for (const label of [/still held/, /Baseline rate/, /Floating loan rate/, /Offset shortfall/]) {
      const field = await screen.findByLabelText(label);
      // type="number" hands the browser a float, which is the whole thing the
      // Decimal pipeline exists to prevent.
      expect(field).toHaveAttribute('type', 'text');
      expect(field).toHaveAttribute('inputMode', 'decimal');
    }
  });
});

// ---------------------------------------------------------------------------
// The rate what-if
// ---------------------------------------------------------------------------

describe('the rate what-if', () => {
  it('multiplies exactly, without going through a float', async () => {
    renderPage(<PositionPage />);
    const field = await screen.findByLabelText(/A rate to try/);
    await userEvent.type(field, '1.7504');
    // 500,000 x 1.7504 = 875,200 exactly.
    expect(await screen.findByText('NZD 875,200.00')).toBeInTheDocument();
    // Against a 1.6000 baseline that is 75,200 more.
    expect(screen.getByText('NZD +75,200.00')).toBeInTheDocument();
  });

  it('says so rather than guessing when there is no baseline', async () => {
    state = fxState({
      position: { ...fxState().position!, baseline_rate: null },
    });
    renderPage(<PositionPage />);
    const field = await screen.findByLabelText(/A rate to try/);
    await userEvent.type(field, '1.7500');
    expect(await screen.findByText('NZD 875,000.00')).toBeInTheDocument();
    expect(screen.getAllByText('Set a baseline rate').length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Conversion history and editing
// ---------------------------------------------------------------------------

describe('ConversionsPage', () => {
  it('shows what each conversion gained and the running total', async () => {
    renderPage(<ConversionsPage />);
    const estimated = (await screen.findByText('30,000.00')).closest('tr');
    expect(within(estimated!).getByText('6,000.00')).toBeInTheDocument();
    expect(within(estimated!).getByText('16,732.50')).toBeInTheDocument();

    // The oldest row's improvement and its cumulative are the same figure,
    // because it is the first one counted.
    const oldest = screen.getByText('50,000.00').closest('tr');
    expect(within(oldest!).getAllByText('4,990.00')).toHaveLength(2);
  });

  it('tags a row whose amounts are estimated', async () => {
    renderPage(<ConversionsPage />);
    expect(await screen.findAllByText('Estimated')).not.toHaveLength(0);
  });

  it('says when a fee was never recorded rather than showing a zero', async () => {
    renderPage(<ConversionsPage />);
    expect(await screen.findByText('Not recorded')).toBeInTheDocument();
    expect(screen.getByText('No fee')).toBeInTheDocument();
  });

  it('warns that an edit does not move the balance', async () => {
    renderPage(<ConversionsPage />);
    const row = (await screen.findByText('30,000.00')).closest('tr');
    expect(row).not.toBeNull();
    await userEvent.click(within(row!).getByRole('button', { name: 'Edit' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/does not change your current balance/)).toBeInTheDocument();
    expect(within(dialog).getByText(/restate it on the Position page/)).toBeInTheDocument();
  });

  it('opens the edit dialog on the row that was clicked', async () => {
    renderPage(<ConversionsPage />);
    const row = (await screen.findByText('50,000.00')).closest('tr');
    await userEvent.click(within(row!).getByRole('button', { name: 'Edit' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveAttribute('aria-label', 'Edit conversion 1');
    expect(within(dialog).getByLabelText(/USD converted/)).toHaveValue('50000.0000');
  });

  it('says a realised total is unknown rather than zero without a baseline', async () => {
    conversions = history({
      baseline_rate: null,
      realised: {
        confirmed: null,
        estimated: null,
        total: null,
        includes_estimates: false,
      },
      conversions: history().conversions.map((row) => ({
        ...row,
        improvement: null,
        cumulative_improvement: null,
      })),
    });
    renderPage(<ConversionsPage />);
    expect(await screen.findByText('Set a baseline rate')).toBeInTheDocument();
  });
});
