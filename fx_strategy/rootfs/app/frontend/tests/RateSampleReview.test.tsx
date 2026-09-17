import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import RateSampleReview from '@/components/RateSampleReview';
import { api } from '@/lib/api';
import type { RateSampleList, Settings } from '@/types';

const SETTINGS = {
  general: { timezone: 'UTC' },
  formatting: { rate_decimal_places: 4 },
} as unknown as Settings;

function sample(overrides: Partial<RateSampleList['samples'][number]> = {}) {
  return {
    id: 1,
    timestamp: '2026-09-17T03:00:00Z',
    rate: '1.72000000',
    provider: 'wise',
    quote_type: 'mid_market',
    excluded: false,
    excluded_reason: null,
    deviation: '0.00010000',
    suspicious: false,
    ...overrides,
  };
}

let list: RateSampleList;

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RateSampleReview range="7d" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  list = {
    samples: [
      sample({ id: 7, rate: '1.77500000', deviation: '0.03200000', suspicious: true }),
      sample({ id: 8 }),
    ],
    threshold: '0.02000000',
    total: 2,
    excluded_count: 0,
    suspicious_count: 1,
  };
  vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
    if (path === 'settings') return SETTINGS as never;
    return list as never;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('RateSampleReview', () => {
  it('shows a point that stands out, and how far out it is', async () => {
    renderPanel();
    expect(await screen.findByText('1.7750')).toBeInTheDocument();
    expect(screen.getByText('3.20%')).toBeInTheDocument();
    expect(screen.getByText('Stands out')).toBeInTheDocument();
  });

  it('says plainly that the observation is kept', async () => {
    renderPanel();
    expect(
      await screen.findByText(/The observation itself is kept, and can be restored/),
    ).toBeInTheDocument();
  });

  it('excludes a point with a reason naming the rate and provider', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ message: '1 excluded' } as never);
    renderPanel();

    await userEvent.click((await screen.findAllByRole('button', { name: 'Exclude' }))[0]!);

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('rates/samples/exclude', {
        sample_ids: [7],
        reason: expect.stringContaining('1.7750'),
      }),
    );
  });

  it('can take out every point that stands out at once', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ message: '1 excluded' } as never);
    renderPanel();

    await userEvent.click(await screen.findByRole('button', { name: /Exclude all 1/ }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        'rates/samples/exclude',
        expect.objectContaining({ sample_ids: [7] }),
      ),
    );
  });

  it('offers to restore a point that was already excluded', async () => {
    list = {
      ...list,
      samples: [sample({ id: 7, rate: '1.77500000', excluded: true, excluded_reason: 'spike' })],
      excluded_count: 1,
      suspicious_count: 0,
    };
    const post = vi.spyOn(api, 'post').mockResolvedValue({ message: '1 restored' } as never);
    renderPanel();

    await userEvent.click(await screen.findByRole('button', { name: 'Restore' }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('rates/samples/restore', { sample_ids: [7] }),
    );
  });

  it('says so when nothing stands out rather than showing an empty table', async () => {
    list = { ...list, samples: [], suspicious_count: 0 };
    renderPanel();
    expect(await screen.findByText('Nothing stands out')).toBeInTheDocument();
    expect(screen.getByText(/2.00%/)).toBeInTheDocument();
  });
});
