import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ObligationDetail from '@/components/ObligationDetail';
import ObligationForm from '@/components/ObligationForm';
import ObligationsPage from '@/pages/ObligationsPage';
import { api } from '@/lib/api';
import type { Obligation, ObligationPortfolio } from '@/types';

const MORTGAGE: Obligation = {
  id: 1,
  name: 'Mortgage offset',
  obligation_type: 'offset_loan',
  priority: 'normal',
  relationship_importance: 'none',
  interest_basis: 'simple_annual',
  partial_allowed: true,
  active: true,
  completed: false,
  notes: '',
  total_nzd: '256000.0000',
  amount_funded_nzd: '0.0000',
  remaining_nzd: '256000.0000',
  annual_rate: '0.06040000',
  minimum_payment_nzd: null,
  due_date: null,
  earliest_payment_date: null,
  target_rate: '1.8000',
  max_wait_days: null,
  daily_cost_nzd: '42.3627',
  weekly_cost_nzd: '296.5389',
  monthly_cost_nzd: '1288.5333',
  annual_cost_nzd: '15462.4000',
  has_interest_cost: true,
  usd_required_now: '148837.2093',
  rate_used: '1.72000000',
  rate_stale: false,
  rate_quality: 'market',
  gain_at_improvement: { '0.005': '744.1860', '0.01': '1488.3721' },
  gain_at_target_nzd: '11906.9767',
  waiting: [
    { days: 7, waiting_cost_nzd: '296.5389', fx_gain_nzd: '11906.9767', net_benefit_nzd: '11610.4378' },
    { days: 30, waiting_cost_nzd: '1270.8810', fx_gain_nzd: '11906.9767', net_benefit_nzd: '10636.0957' },
    { days: 60, waiting_cost_nzd: '2541.7620', fx_gain_nzd: '11906.9767', net_benefit_nzd: '9365.2147' },
  ],
  break_even_days_at_improvement: { '0.005': '17.5686', '0.01': '35.1372' },
  break_even_days_at_target: '281.0976',
  break_even_rate_after: { '7': '1.72199231', '30': '1.72853846' },
  days_until_due: null,
  overdue: false,
  priority_components: {
    due_urgency: '0',
    user_priority: '10',
    relationship: '0',
    interest_cost: '22',
    size: '7.85',
    max_wait: '0',
    partial_flexibility: '0',
  },
  financial_score: '29.85',
  overall_score: '39.85',
  financial_rank: 1,
  overall_rank: 2,
  action: 'WAIT_FOR_TARGET',
  reason: 'No urgent date, and reaching 1.8000 would be worth NZ$10,636.10 net.',
  warnings: [],
  disclaimer: 'These figures are estimates. Decision support, not financial advice.',
};

const MEIKA: Obligation = {
  ...MORTGAGE,
  id: 2,
  name: 'Meika repayment',
  obligation_type: 'interest_free_loan',
  priority: 'high',
  relationship_importance: 'high',
  interest_basis: 'none',
  partial_allowed: false,
  total_nzd: '70000.0000',
  remaining_nzd: '70000.0000',
  annual_rate: '0.00000000',
  daily_cost_nzd: '0.0000',
  weekly_cost_nzd: '0.0000',
  monthly_cost_nzd: '0.0000',
  annual_cost_nzd: '0.0000',
  has_interest_cost: false,
  usd_required_now: '40697.6744',
  target_rate: null,
  gain_at_improvement: { '0.005': '203.4884', '0.01': '406.9767' },
  gain_at_target_nzd: null,
  waiting: [],
  // No interest means no break-even period at all.
  break_even_days_at_improvement: { '0.005': null, '0.01': null },
  break_even_days_at_target: null,
  break_even_rate_after: { '7': '1.72000000', '30': '1.72000000' },
  priority_components: {
    due_urgency: '0',
    user_priority: '30',
    relationship: '35',
    interest_cost: '0',
    size: '2.15',
    max_wait: '0',
    partial_flexibility: '8',
  },
  financial_score: '2.15',
  overall_score: '75.15',
  financial_rank: 2,
  overall_rank: 1,
  action: 'WAIT_FOR_TARGET',
  reason: 'This interest-free loan accrues no interest, so waiting has no financial cost.',
  warnings: ['No interest-based cost of waiting: this obligation accrues nothing.'],
};

const PORTFOLIO: ObligationPortfolio = {
  total_obligations: 2,
  total_nzd: '326000.0000',
  total_usd_required: '189534.8837',
  total_daily_cost_nzd: '42.3627',
  total_monthly_cost_nzd: '1288.5333',
  due_within_7_days_nzd: '0.0000',
  due_within_30_days_nzd: '0.0000',
  highest_priority_obligation_id: 2,
  highest_priority_obligation_name: 'Meika repayment',
  next_obligation_id: 2,
  next_obligation_name: 'Meika repayment',
  next_conversion_usd: '40697.6744',
  next_conversion_nzd: '70000.0000',
  usd_after_critical: null,
  usd_after_high_priority: null,
  weighted_break_even_rate: '1.72670807',
  max_rational_wait_days: 281,
  strategy_status: 'waiting',
  rate_used: '1.72000000',
  rate_stale: false,
  rate_quality: 'market',
  warnings: [],
  disclaimer: 'These figures are estimates. Decision support, not financial advice.',
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ObligationsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
    if (path === 'obligations') return [MORTGAGE, MEIKA] as never;
    if (path === 'obligations/portfolio') return PORTFOLIO as never;
    return [] as never;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ObligationsPage', () => {
  it('shows the totals exactly, without float rounding', async () => {
    renderPage();
    expect(await screen.findByText('NZD 326,000.00')).toBeInTheDocument();
    expect(screen.getByText('USD 189,534.88')).toBeInTheDocument();
  });

  it('orders by overall priority, so the interest-free loan comes first', async () => {
    renderPage();
    await screen.findByText('NZD 326,000.00');

    const rows = screen.getAllByRole('row');
    // Header row, then Meika (overall rank 1), then the mortgage.
    expect(rows[1]).toHaveTextContent('Meika repayment');
    expect(rows[2]).toHaveTextContent('Mortgage offset');
  });

  it('shows a dash rather than a zero where there is no interest', async () => {
    renderPage();
    await screen.findByText('NZD 326,000.00');

    const meikaRow = screen.getAllByRole('row')[1];
    // No interest rate, no daily cost, and no break-even period.
    expect(meikaRow).toHaveTextContent('None');
  });

  it('names the next obligation and the amount to convert', async () => {
    renderPage();
    expect(await screen.findByText('USD 40,697.67')).toBeInTheDocument();
    expect(screen.getAllByText(/Meika repayment/).length).toBeGreaterThan(0);
  });

  it('carries the disclaimer', async () => {
    renderPage();
    expect(await screen.findByText(/not financial advice/)).toBeInTheDocument();
  });
});

describe('ObligationDetail', () => {
  function renderDetail(obligation: Obligation) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ObligationDetail obligation={obligation} onChanged={() => {}} onClose={() => {}} />
      </QueryClientProvider>,
    );
  }

  it('shows both priority scores and their components', () => {
    renderDetail(MEIKA);

    expect(screen.getAllByText(/Financial priority/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Overall priority/).length).toBeGreaterThan(0);
    // The component that explains the gap.
    expect(screen.getByText('relationship')).toBeInTheDocument();
  });

  it('says there is no break-even period rather than showing zero days', () => {
    renderDetail(MEIKA);
    expect(screen.getByText('No interest cost')).toBeInTheDocument();
    expect(
      screen.getByText('there is no financial break-even period'),
    ).toBeInTheDocument();
  });

  it('explains that a missing target means nothing to weigh against', () => {
    renderDetail(MEIKA);
    expect(screen.getByText(/No target rate is set/)).toBeInTheDocument();
  });

  it('shows the net benefit at each horizon when a target exists', () => {
    renderDetail(MORTGAGE);
    expect(screen.getAllByText('After 7 days').length).toBeGreaterThan(0);
    expect(screen.getByText('NZD 10,636.10')).toBeInTheDocument();
  });

  it('shows the rate that would justify waiting', () => {
    renderDetail(MORTGAGE);
    expect(screen.getByText('1.7285')).toBeInTheDocument();
  });
});

describe('ObligationForm', () => {
  function renderForm(obligation?: Obligation) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ObligationForm obligation={obligation} onDone={() => {}} onCancel={() => {}} />
      </QueryClientProvider>,
    );
  }

  it('opens pre-filled when editing an existing obligation', () => {
    renderForm(MORTGAGE);

    expect(screen.getByText(/Edit Mortgage offset/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Name/)).toHaveValue('Mortgage offset');
    expect(screen.getByLabelText(/^Total NZD/)).toHaveValue('256000.0000');
    // The stored fraction is shown as the percentage the user typed.
    expect(screen.getByLabelText(/^Annual interest rate/)).toHaveValue('6.04');
  });

  it('is empty when adding', () => {
    renderForm();
    expect(screen.getByText('New obligation')).toBeInTheDocument();
    expect(screen.getByLabelText(/^Name/)).toHaveValue('');
  });

  it('sends a PATCH when editing, not a POST', async () => {
    const patch = vi.spyOn(api, 'patch').mockResolvedValue({} as never);
    const post = vi.spyOn(api, 'post').mockResolvedValue({} as never);
    renderForm(MORTGAGE);

    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(patch).toHaveBeenCalledWith('obligations/1', expect.any(Object));
    expect(post).not.toHaveBeenCalled();
  });

  it('clears a due date by sending an explicit null', async () => {
    const patch = vi.spyOn(api, 'patch').mockResolvedValue({} as never);
    renderForm({ ...MORTGAGE, due_date: '2026-09-01' });

    expect(screen.getByLabelText(/^Due date/)).toHaveValue('2026-09-01');
    const [clearDate] = screen.getAllByRole('button', { name: 'Clear' });
    await userEvent.click(clearDate!);
    expect(screen.getByLabelText(/^Due date/)).toHaveValue('');

    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    // null, not undefined and not an empty string: only null clears it.
    expect(patch).toHaveBeenCalledWith(
      'obligations/1',
      expect.objectContaining({ due_date: null }),
    );
  });

  it('clears a target rate and a waiting limit the same way', async () => {
    const patch = vi.spyOn(api, 'patch').mockResolvedValue({} as never);
    renderForm({ ...MORTGAGE, target_rate: '1.8000', max_wait_days: 45 });

    const clears = screen.getAllByRole('button', { name: 'Clear' });
    // Due date has none set, so its Clear is disabled; the other two are live.
    for (const button of clears) {
      if (!button.hasAttribute('disabled')) await userEvent.click(button);
    }

    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(patch).toHaveBeenCalledWith(
      'obligations/1',
      expect.objectContaining({ target_rate: null, max_wait_days: null }),
    );
  });

  it('disables Clear when there is nothing to clear', () => {
    renderForm({ ...MORTGAGE, due_date: null });
    // The due-date Clear is the first one and has nothing to remove.
    const [clearDate] = screen.getAllByRole('button', { name: 'Clear' });
    expect(clearDate).toBeDisabled();
  });

  it('converts the percentage back to a fraction on save', async () => {
    const patch = vi.spyOn(api, 'patch').mockResolvedValue({} as never);
    renderForm(MORTGAGE);

    await userEvent.clear(screen.getByLabelText(/^Annual interest rate/));
    await userEvent.type(screen.getByLabelText(/^Annual interest rate/), '7.5');
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(patch).toHaveBeenCalledWith(
      'obligations/1',
      expect.objectContaining({ annual_rate: '0.075' }),
    );
  });
});
