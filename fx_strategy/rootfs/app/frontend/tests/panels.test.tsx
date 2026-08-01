import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import type { ReactElement } from 'react';
import { describe, expect, it } from 'vitest';

import PortfolioPanel from '@/components/PortfolioPanel';
import RiskPanel from '@/components/RiskPanel';
import TranchePanel from '@/components/TranchePanel';
import { makeProgress, makeSummary, makeTranche } from './factories';

function renderWithClient(element: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

describe('PortfolioPanel', () => {
  it('shows exact amounts without float rounding', () => {
    renderWithClient(<PortfolioPanel summary={makeSummary()} ratePlaces={4} />);
    expect(screen.getByText('NZD 1,404,000.00')).toBeInTheDocument();
    expect(screen.getAllByText('USD 800,000.00').length).toBeGreaterThan(0);
  });

  it('says "Fee not included" rather than showing a zero fee', () => {
    renderWithClient(<PortfolioPanel summary={makeSummary()} ratePlaces={4} />);
    expect(screen.getAllByText('Fee not included').length).toBeGreaterThan(0);
    expect(screen.getByText('Not calculable')).toBeInTheDocument();
    expect(
      screen.getByText('Configure a fee model to see net proceeds.'),
    ).toBeInTheDocument();
  });

  it('shows net proceeds once a fee model is configured', () => {
    const summary = makeSummary({
      convert_all_now: {
        source_amount: '800000.0000',
        rate: '1.75500000',
        gross_target_amount: '1404000.0000',
        fee: {
          available: true,
          label: '0.41% of the converted amount',
          amount_source_currency: '3280.0000',
          amount_target_currency: '5756.4000',
          basis: '0.41% of the converted amount',
        },
        net_target_amount: '1398243.6000',
        effective_rate: '1.74780450',
        quality: 'estimate',
      },
    });
    renderWithClient(<PortfolioPanel summary={summary} ratePlaces={4} />);
    expect(screen.getByText('NZD 1,398,243.60')).toBeInTheDocument();
    expect(screen.getByText('NZD 5,756.40')).toBeInTheDocument();
  });

  it('puts the one-cent consequence next to the rate', () => {
    renderWithClient(<PortfolioPanel summary={makeSummary()} ratePlaces={4} />);
    expect(screen.getByText('Cost of a 1-cent reversal')).toBeInTheDocument();
    expect(screen.getByText('NZD 8,000.00')).toBeInTheDocument();
  });

  it('reports a missing rate instead of showing a blank figure', () => {
    renderWithClient(
      <PortfolioPanel summary={makeSummary({ convert_all_now: null })} ratePlaces={4} />,
    );
    expect(screen.getByText(/No rate is available/)).toBeInTheDocument();
  });
});

describe('TranchePanel', () => {
  it('states that a reached target has not converted anything', () => {
    renderWithClient(<TranchePanel summary={makeSummary()} ratePlaces={4} />);
    expect(
      screen.getByText(/A target being reached does not convert anything/),
    ).toBeInTheDocument();
  });

  it('marks a tranche whose target the rate has passed', () => {
    const summary = makeSummary({
      tranche_progress: [
        makeProgress({ target_reached_now: true, distance_to_target: '-0.03500000' }),
      ],
    });
    renderWithClient(<TranchePanel summary={summary} ratePlaces={4} />);
    const row = screen.getAllByRole('row')[1];
    expect(row).toBeDefined();
    expect(within(row!).getByText('At or above')).toBeInTheDocument();
    expect(within(row!).getByText('-0.0350')).toBeInTheDocument();
  });

  it('shows a useful empty state for a monitor-only strategy', () => {
    renderWithClient(
      <TranchePanel summary={makeSummary({ tranche_progress: [] })} ratePlaces={4} />,
    );
    expect(screen.getByText('No tranches in this strategy')).toBeInTheDocument();
  });

  it('offers acknowledge without implying a conversion', () => {
    const summary = makeSummary({
      tranche_progress: [makeProgress({ tranche: makeTranche({ status: 'target_reached' }) })],
    });
    renderWithClient(<TranchePanel summary={summary} ratePlaces={4} />);
    const button = screen.getByRole('button', { name: 'Acknowledge' });
    expect(button).toHaveAttribute(
      'title',
      'Stop repeat alerts for this target without marking it converted',
    );
  });
});

describe('RiskPanel', () => {
  it('lists the documented sensitivity figures', () => {
    renderWithClient(<RiskPanel summary={makeSummary()} ratePlaces={4} />);
    expect(screen.getByText('-4,000.00')).toBeInTheDocument();
    expect(screen.getByText('-24,000.00')).toBeInTheDocument();
    expect(screen.getByText('-80,000.00')).toBeInTheDocument();
  });

  it('shows the walk-away trade-off from both directions', () => {
    renderWithClient(<RiskPanel summary={makeSummary()} ratePlaces={4} />);
    expect(screen.getByText('Waiting for the highest target adds')).toBeInTheDocument();
    expect(screen.getByText(/leaves USD 800,000.00 exposed meanwhile/)).toBeInTheDocument();
  });

  it('warns near a deadline without claiming anything was changed', () => {
    const summary = makeSummary({
      days_to_deadline: 5,
      deadline_severity: 'critical',
      deadline_message: 'Deadline is very close.',
    });
    renderWithClient(<RiskPanel summary={summary} ratePlaces={4} />);
    expect(screen.getByText(/no target has been changed/)).toBeInTheDocument();
  });

  it('does not show a deadline banner when there is plenty of time', () => {
    renderWithClient(<RiskPanel summary={makeSummary()} ratePlaces={4} />);
    expect(screen.queryByText(/day.? remain/)).not.toBeInTheDocument();
  });
});
