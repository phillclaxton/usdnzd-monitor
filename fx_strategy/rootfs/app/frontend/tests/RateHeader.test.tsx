import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import RateHeader from '@/components/RateHeader';
import type { CurrentRate } from '@/types';

function makeRate(overrides: Partial<CurrentRate> = {}): CurrentRate {
  return {
    source_currency: 'USD',
    target_currency: 'NZD',
    rate: '1.76040000',
    status: 'live',
    provider: 'wise',
    quote_type: 'mid_market',
    quote_label: 'Mid-market reference rate',
    provider_timestamp: '2026-08-01T09:00:00Z',
    retrieved_at: '2026-08-01T09:00:30Z',
    age_seconds: 30,
    stale_after_seconds: 900,
    changes: {
      one_hour: '0.00120000',
      twenty_four_hours: '-0.00450000',
      seven_days: null,
      thirty_days: null,
    },
    high_24h: '1.77000000',
    low_24h: '1.75000000',
    high_6m: '1.81000000',
    low_6m: '1.66000000',
    disagreement_warning: null,
    message: null,
    ...overrides,
  };
}

describe('RateHeader', () => {
  it('shows the rate in the product quote convention', () => {
    render(<RateHeader rate={makeRate()} timezone="Pacific/Auckland" ratePlaces={4} />);
    expect(screen.getByText('1 USD = 1.7604 NZD')).toBeInTheDocument();
    expect(screen.getByText('Live')).toBeInTheDocument();
  });

  it('warns clearly when the rate is stale, and says targets will not fire', () => {
    render(
      <RateHeader
        rate={makeRate({ status: 'stale', age_seconds: 7200 })}
        timezone="Pacific/Auckland"
        ratePlaces={4}
      />,
    );
    expect(screen.getByText(/treated as stale/i)).toBeInTheDocument();
    expect(screen.getByText(/Targets will not trigger/i)).toBeInTheDocument();
    // Status is conveyed by a word, not colour alone.
    expect(screen.getByText('Stale')).toBeInTheDocument();
  });

  it('marks a delayed rate distinctly from a live one', () => {
    render(
      <RateHeader rate={makeRate({ status: 'delayed' })} timezone="UTC" ratePlaces={4} />,
    );
    expect(screen.getByText('Delayed')).toBeInTheDocument();
    expect(screen.queryByText('Live')).not.toBeInTheDocument();
  });

  it('renders signed changes and a dash for missing windows', () => {
    render(<RateHeader rate={makeRate()} timezone="UTC" ratePlaces={4} />);
    expect(screen.getByText('+0.0012')).toBeInTheDocument();
    expect(screen.getByText('-0.0045')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('surfaces a provider disagreement warning', () => {
    render(
      <RateHeader
        rate={makeRate({ disagreement_warning: 'Providers differ by 0.0300.' })}
        timezone="UTC"
        ratePlaces={4}
      />,
    );
    expect(screen.getByText('Providers differ by 0.0300.')).toBeInTheDocument();
  });

  it('explains an empty state rather than showing a blank rate', () => {
    render(
      <RateHeader
        rate={makeRate({
          status: 'unavailable',
          rate: null,
          provider: '',
          message: 'No rate has been collected yet.',
          retrieved_at: null,
          age_seconds: null,
        })}
        timezone="UTC"
        ratePlaces={4}
      />,
    );
    expect(screen.getByText('No rate has been collected yet.')).toBeInTheDocument();
    expect(screen.getByText(/Never retrieved/)).toBeInTheDocument();
  });
});
