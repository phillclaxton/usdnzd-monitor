import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import StrategyJsonEditor from '@/components/StrategyJsonEditor';
import { ApiError, api } from '@/lib/api';
import type { DocumentPreview } from '@/types';

const TEXT = JSON.stringify(
  {
    name: 'USD to NZD',
    initial_source_amount: '800000.0000',
    tranches: [{ sequence: 1, allocation_type: 'percentage', allocation_value: '100' }],
  },
  null,
  2,
);

const DOCUMENT = {
  strategy_id: 1,
  name: 'USD to NZD',
  text: TEXT,
  document: { name: 'USD to NZD' },
  omitted: {
    conversions: 'records of what happened, not settings — never altered by an edit',
    status: 'changed with the activate, pause and complete actions',
  },
};

const NO_CHANGES: DocumentPreview = {
  valid: true,
  problems: [],
  changes: [],
  warnings: [],
  tranches_added: [],
  tranches_removed: [],
  tranches_retargeted: [],
  conversions_preserved: 0,
};

let preview: DocumentPreview = NO_CHANGES;

/**
 * The text belongs to the page, not the component, so the harness holds it —
 * that is what lets the field editor and the JSON view stay the same value.
 */
function Harness({ strategyId, initial }: { strategyId: number | null; initial: string }) {
  const [text, setText] = useState(initial);
  return (
    <StrategyJsonEditor
      strategyId={strategyId}
      value={text}
      onChange={setText}
      onSaved={() => {}}
    />
  );
}

function renderEditor(strategyId: number | null = 1, initial: string = TEXT) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <Harness strategyId={strategyId} initial={initial} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  preview = NO_CHANGES;
  vi.spyOn(api, 'get').mockImplementation(async () => DOCUMENT as never);
  vi.spyOn(api, 'post').mockImplementation(async () => preview as never);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('StrategyJsonEditor', () => {
  it('shows the document text the page gives it', async () => {
    renderEditor();
    const box = await screen.findByLabelText('Strategy JSON');
    expect(box).toHaveValue(TEXT);
  });

  it('says plainly that an unchanged document would change nothing', async () => {
    renderEditor();
    await screen.findByLabelText('Strategy JSON');
    expect(
      await screen.findByText(/Saving it would change nothing/, undefined, { timeout: 3000 }),
    ).toBeInTheDocument();
  });

  it('lists what would change, with the value before and after', async () => {
    preview = {
      ...NO_CHANGES,
      changes: [{ path: 'name', before: 'USD to NZD', after: 'Staged conversion' }],
    };
    renderEditor();
    await screen.findByLabelText('Strategy JSON');

    expect(await screen.findByText('1 change', undefined, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText('Staged conversion')).toBeInTheDocument();
  });

  it('warns about consequences before the document is saved', async () => {
    preview = {
      ...NO_CHANGES,
      tranches_retargeted: [1],
      warnings: [
        'Target rates changed on tranche(s) 1. Their reached-and-notified state resets, so a ' +
          'target already passed will alert again once it is reached at the new level.',
      ],
      conversions_preserved: 2,
    };
    renderEditor();
    await screen.findByLabelText('Strategy JSON');

    expect(
      await screen.findByText(/reached-and-notified state resets/, undefined, { timeout: 3000 }),
    ).toBeInTheDocument();
  });

  it('locates a syntax error by line and refuses to save', async () => {
    preview = {
      ...NO_CHANGES,
      valid: false,
      problems: [{ path: '', message: 'Expecting value', line: 3, column: 12 }],
    };
    renderEditor();

    const box = await screen.findByLabelText('Strategy JSON');
    await userEvent.type(box, '{{');

    expect(
      await screen.findByText('Line 3, column 12: Expecting value', undefined, { timeout: 3000 }),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save JSON' })).toBeDisabled());
  });

  it('names the field a validation problem belongs to', async () => {
    preview = {
      ...NO_CHANGES,
      valid: false,
      problems: [
        { path: 'tranches[2].target_rate', message: 'A target rate must be greater than zero.', line: null, column: null },
      ],
    };
    renderEditor();
    await screen.findByLabelText('Strategy JSON');

    expect(
      await screen.findByText(
        'tranches[2].target_rate — A target rate must be greater than zero.',
        undefined,
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();
  });

  it('saves the text as typed rather than a re-serialised copy', async () => {
    const put = vi.spyOn(api, 'put').mockResolvedValue({} as never);
    renderEditor();

    const box = await screen.findByLabelText('Strategy JSON');
    await userEvent.clear(box);
    await userEvent.type(box, '{{"name": "Pasted"}');

    await userEvent.click(screen.getByRole('button', { name: 'Save JSON' }));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith('strategies/1/document', { text: '{"name": "Pasted"}' }),
    );
  });

  it('shows the located problems when the server rejects a save', async () => {
    vi.spyOn(api, 'put').mockRejectedValue(
      new ApiError(422, 'validation_error', 'The document has 1 problem.', [
        { path: 'initial_source_amount', message: 'This field is required.', line: null, column: null },
      ]),
    );
    renderEditor();
    await screen.findByLabelText('Strategy JSON');

    await userEvent.click(screen.getByRole('button', { name: 'Save JSON' }));
    expect(
      await screen.findByText('initial_source_amount — This field is required.'),
    ).toBeInTheDocument();
  });

  it('creates rather than updates when there is no strategy yet', async () => {
    const post = vi.spyOn(api, 'post').mockImplementation(async (path: string) => {
      if (path.endsWith('/preview')) return preview as never;
      return {} as never;
    });
    renderEditor(null, '{}');

    await screen.findByLabelText('Strategy JSON');
    await userEvent.click(screen.getByRole('button', { name: 'Create from JSON' }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('strategies/document', { text: '{}' }),
    );
  });

  it('explains what the document deliberately leaves out', async () => {
    renderEditor();
    expect(await screen.findByText('What the document leaves out')).toBeInTheDocument();
    expect(screen.getByText('conversions')).toBeInTheDocument();
  });

  it('restores the saved document when changes are discarded', async () => {
    renderEditor();
    const box = await screen.findByLabelText('Strategy JSON');

    await userEvent.type(box, 'rubbish');
    expect(box).not.toHaveValue(TEXT);

    await userEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(box).toHaveValue(TEXT);
  });
});
