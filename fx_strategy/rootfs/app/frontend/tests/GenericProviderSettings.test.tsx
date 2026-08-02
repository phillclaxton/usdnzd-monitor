import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import GenericProviderSettingsPanel from '@/components/GenericProviderSettings';
import { api } from '@/lib/api';

const PRESETS = [
  {
    key: 'frankfurter',
    display_name: 'Frankfurter (ECB reference rates)',
    base_url: 'https://api.frankfurter.app',
    requires_key: false,
    auth_style: 'none',
    notes: 'Free, no key, but publishes one rate per working day.',
  },
  {
    key: 'openexchangerates',
    display_name: 'Open Exchange Rates',
    base_url: 'https://openexchangerates.org/api',
    requires_key: true,
    auth_style: 'query',
    notes: 'The free plan only supports a USD base.',
  },
];

const CONFIG = {
  enabled: false,
  display_name: 'Generic API provider',
  base_url: '',
  rate_path: '/latest',
  history_path: '',
  auth_style: 'header' as const,
  auth_name: 'apikey',
  source_param: 'base',
  target_param: 'symbols',
  rate_json_path: 'rates.{target}',
  timestamp_json_path: 'timestamp',
  convention: 'target_per_source' as const,
  provider_timezone: 'UTC',
  min_seconds_between_calls: 60,
  timeout_seconds: 15,
  preset: '',
};

const STATUS = {
  enabled: false,
  configured: false,
  display_name: 'Generic API provider',
  preset: '',
  base_url: '',
  supports_history: false,
  key_required: true,
  key_hint: '',
  message: 'No base URL yet. Choose a preset or enter one.',
  rate: null,
  latency_ms: null,
};

function renderPanel(): ReactElement {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const element = (
    <QueryClientProvider client={client}>
      <GenericProviderSettingsPanel />
    </QueryClientProvider>
  );
  render(element);
  return element;
}

beforeEach(() => {
  vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
    if (path === 'providers/presets') return PRESETS as never;
    return { config: CONFIG, status: STATUS } as never;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('GenericProviderSettingsPanel', () => {
  it('offers every preset and says which need a key', async () => {
    renderPanel();

    const select = await screen.findByLabelText(/^Preset/);
    expect(select).toBeInTheDocument();
    expect(
      await screen.findByRole('option', { name: /Frankfurter.*no key needed/ }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole('option', { name: /Open Exchange Rates.*key required/ }),
    ).toBeInTheDocument();
    // A provider that is not on the list is still configurable.
    expect(screen.getByRole('option', { name: 'Custom (no preset)' })).toBeInTheDocument();
  });

  it('says what is missing before anything is configured', async () => {
    renderPanel();
    expect(
      await screen.findByText(/No base URL yet. Choose a preset or enter one./),
    ).toBeInTheDocument();
  });

  it('applies a preset through the server rather than filling the form locally', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({} as never);
    renderPanel();

    const select = await screen.findByLabelText(/^Preset/);
    // Wait for the preset list to arrive before choosing from it.
    await screen.findByRole('option', { name: /Frankfurter/ });
    await userEvent.selectOptions(select, 'frankfurter');

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('providers/generic/preset/frankfurter'),
    );
  });

  it('sends the API key only on save, and never renders it back', async () => {
    const put = vi.spyOn(api, 'put').mockResolvedValue({} as never);
    renderPanel();

    const key = await screen.findByLabelText(/API key/);
    await userEvent.type(key, 'secret-key-123');

    // Typing alone must not save.
    expect(put).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        'providers/generic',
        expect.objectContaining({ api_key: 'secret-key-123' }),
      ),
    );
  });

  it('hides the key fields when the provider needs no authentication', async () => {
    vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
      if (path === 'providers/presets') return PRESETS as never;
      return {
        config: { ...CONFIG, auth_style: 'none', base_url: 'https://api.frankfurter.app' },
        status: { ...STATUS, base_url: 'https://api.frankfurter.app', key_required: false },
      } as never;
    });
    renderPanel();

    await screen.findByLabelText(/^Base URL/);
    expect(screen.queryByLabelText(/API key/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^Key name/)).not.toBeInTheDocument();
  });

  it('reports a failed test as a message rather than hiding it', async () => {
    vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
      if (path === 'providers/presets') return PRESETS as never;
      return {
        config: { ...CONFIG, base_url: 'https://api.example.com' },
        status: { ...STATUS, base_url: 'https://api.example.com' },
      } as never;
    });
    vi.spyOn(api, 'post').mockResolvedValue({
      ...STATUS,
      message: 'The provider answered 401. Check the API key.',
      rate: null,
    } as never);

    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: 'Test' }));

    expect(
      await screen.findByText('The provider answered 401. Check the API key.'),
    ).toBeInTheDocument();
  });

  it('shows the rate a successful test returned', async () => {
    vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
      if (path === 'providers/presets') return PRESETS as never;
      return {
        config: { ...CONFIG, base_url: 'https://api.example.com' },
        status: { ...STATUS, base_url: 'https://api.example.com' },
      } as never;
    });
    vi.spyOn(api, 'post').mockResolvedValue({
      ...STATUS,
      configured: true,
      message: 'Success: 1 USD = 1.72310000 NZD',
      rate: '1.72310000',
    } as never);

    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: 'Test' }));

    expect(await screen.findByText('Success: 1 USD = 1.72310000 NZD')).toBeInTheDocument();
  });

  it('explains that enabling is not the same as selecting', async () => {
    renderPanel();
    expect(
      await screen.findByText(/Enabling this provider does not select it/),
    ).toBeInTheDocument();
  });
});
