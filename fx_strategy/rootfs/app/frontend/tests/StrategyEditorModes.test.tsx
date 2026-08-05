/**
 * The field editor and the JSON view must describe the same strategy.
 *
 * Both replace the whole definition when they save, so a mismatch between them
 * is not a display quirk — it is one view overwriting what the other just did.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import StrategyEditor from '@/pages/StrategyEditor';
import { api } from '@/lib/api';
import type { Settings, Strategy, StrategyInput } from '@/types';
import { makeStrategy, makeTranche } from './factories';

const SETTINGS = {
  general: {
    timezone: 'Pacific/Auckland',
    source_currency: 'USD',
    target_currency: 'NZD',
    rate_convention: 'target_per_source',
    setup_complete: true,
    active_strategy_id: 1,
  },
  formatting: {
    currency_decimal_places: 2,
    rate_decimal_places: 4,
    thousands_separator: true,
    locale: 'en-NZ',
  },
} as unknown as Settings;

const REQUIREMENT = {
  id: 5,
  strategy_id: 1,
  due_date: '2026-09-01T00:00:00Z',
  required_source_amount: '250000.0000',
  required_percentage: null,
  description: 'School fees',
};

/** The saved strategy, swapped out when a test simulates a save. */
let strategy: Strategy;

function document(from: Strategy): string {
  return JSON.stringify({ name: from.name, tranches: [] }, null, 2);
}

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <StrategyEditor />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  strategy = makeStrategy({
    updated_at: '2026-08-02T00:00:00Z',
    rate_provider_id: 'wise',
    tranches: [
      makeTranche({ id: 10, sequence: 1, minimum_rate: '1.70000000' }),
      makeTranche({ id: 11, sequence: 2, target_rate: '1.76000000', allocation_value: '20.0000' }),
    ],
    requirements: [REQUIREMENT],
  });

  vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
    if (path === 'settings') return SETTINGS as never;
    if (path === 'strategies') return [strategy] as never;
    if (path === 'strategies/1') return strategy as never;
    if (path === 'strategies/1/document') {
      return { strategy_id: 1, name: strategy.name, text: document(strategy), document: {}, omitted: {} } as never;
    }
    if (path === 'strategy-templates') return [] as never;
    if (path === 'fee-models') return [] as never;
    return null as never;
  });
  vi.spyOn(api, 'post').mockResolvedValue({ valid: true, problems: [], changes: [], warnings: [], tranches_added: [], tranches_removed: [], tranches_retargeted: [], conversions_preserved: 0 } as never);
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function openEditor(): Promise<HTMLElement> {
  renderEditor();
  return screen.findByLabelText('Strategy name');
}

describe('the field editor and the JSON view', () => {
  it('shows the saved strategy in JSON, including what the fields cannot edit', async () => {
    await openEditor();
    await userEvent.click(screen.getByRole('button', { name: 'Edit as JSON' }));

    const box = await screen.findByLabelText('Strategy JSON');
    const parsed = JSON.parse((box as HTMLTextAreaElement).value) as StrategyInput;

    expect(parsed.requirements).toHaveLength(1);
    expect(parsed.rate_provider_id).toBe('wise');
    expect(parsed.tranches[0]?.minimum_rate).toBe('1.70000000');
  });

  it('carries an unsaved field edit into the JSON view', async () => {
    const name = await openEditor();
    await userEvent.clear(name);
    await userEvent.type(name, 'Renamed in fields');

    await userEvent.click(screen.getByRole('button', { name: 'Edit as JSON' }));
    const box = await screen.findByLabelText('Strategy JSON');
    expect((box as HTMLTextAreaElement).value).toContain('Renamed in fields');
  });

  it('carries an unsaved JSON edit back into the fields', async () => {
    await openEditor();
    await userEvent.click(screen.getByRole('button', { name: 'Edit as JSON' }));

    // fireEvent rather than userEvent.type: `[` and `{` are keyboard
    // descriptors there, and this is a paste, not typing.
    const box = await screen.findByLabelText('Strategy JSON');
    fireEvent.change(box, {
      target: {
        value: JSON.stringify({
          name: 'Renamed in JSON',
          initial_source_amount: '900000',
          tranches: [],
        }),
      },
    });

    await userEvent.click(screen.getByRole('button', { name: 'Edit fields' }));
    expect(await screen.findByLabelText('Strategy name')).toHaveValue('Renamed in JSON');
  });

  it('refuses to switch to the fields when the JSON cannot be read', async () => {
    await openEditor();
    await userEvent.click(screen.getByRole('button', { name: 'Edit as JSON' }));

    const box = await screen.findByLabelText('Strategy JSON');
    await userEvent.clear(box);
    await userEvent.type(box, '{{not json');

    await userEvent.click(screen.getByRole('button', { name: 'Edit fields' }));

    // Still in the JSON view, with the reason on screen — showing stale fields
    // would be the very mismatch this prevents.
    expect(await screen.findByText(/The JSON cannot be read/)).toBeInTheDocument();
    expect(screen.getByLabelText('Strategy JSON')).toBeInTheDocument();
  });

  it('updates the fields after the JSON view saves', async () => {
    const saved = makeStrategy({
      name: 'Saved from JSON',
      initial_source_amount: '400000.0000',
      updated_at: '2026-08-03T00:00:00Z',
      tranches: [makeTranche({ id: 10, sequence: 1 })],
      requirements: [REQUIREMENT],
    });
    vi.spyOn(api, 'put').mockImplementation(async () => {
      strategy = saved;
      return saved as never;
    });

    await openEditor();
    await userEvent.click(screen.getByRole('button', { name: 'Edit as JSON' }));
    await screen.findByLabelText('Strategy JSON');
    await userEvent.click(screen.getByRole('button', { name: 'Save JSON' }));

    await waitFor(() => expect(screen.getByText('Strategy saved from the document.')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Edit fields' }));
    expect(await screen.findByLabelText('Strategy name')).toHaveValue('Saved from JSON');
    expect(screen.getByLabelText(/Total USD to convert/)).toHaveValue('400000.0000');
  });
});

describe('saving from the field editor', () => {
  it('keeps the parts of the strategy the form has no input for', async () => {
    const put = vi.spyOn(api, 'put').mockResolvedValue(strategy as never);

    const name = await openEditor();
    await userEvent.type(name, ' v2');
    await userEvent.click(screen.getByRole('button', { name: 'Save strategy' }));

    await waitFor(() => expect(put).toHaveBeenCalled());
    const [path, body] = put.mock.calls[0] as [string, StrategyInput];
    expect(path).toBe('strategies/1');

    // Every one of these is replaced wholesale by the update, so omitting it
    // from the form's payload silently deletes it.
    expect(body.requirements).toHaveLength(1);
    expect(body.rate_provider_id).toBe('wise');
    expect(body.strategy_start_date).toBe(strategy.strategy_start_date);
    expect(body.tranches[0]?.minimum_rate).toBe('1.70000000');
  });
});
