import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import FxAlertSettingsPanel from '@/components/FxAlertSettings';
import type { FxAlertSettings, Settings } from '@/types';

/**
 * The FX alerts panel.
 *
 * What is pinned here is that a rate reaches the API as the string that was
 * typed, that a list is parsed into a list, and that switching the whole thing
 * off says what that costs rather than doing it quietly.
 */

const ALERTS: FxAlertSettings = {
  enabled: true,
  absolute_rate_move: '0.00500000',
  intraday_percent_move: '0.50',
  alert_new_7d_high: true,
  alert_new_30d_high: true,
  alert_new_90d_high: false,
  new_high_cooldown_minutes: 240,
  alert_round_number_breaks: true,
  watch_levels: ['1.70000000', '1.75000000'],
  material_nzd_value_change: '5000',
  secondary_nzd_value_change: '10000',
  offset_shortfall_thresholds: ['100000', '50000', '0'],
  daily_cost_thresholds: ['10', '5'],
  minimum_change_since_last_alert: '0.00500000',
};

function settingsWith(alerts: Partial<FxAlertSettings> = {}): Settings {
  return {
    general: { target_currency: 'NZD', source_currency: 'USD' },
    fx_alerts: { ...ALERTS, ...alerts },
  } as unknown as Settings;
}

let saved: Partial<Settings>[] = [];

function renderPanel(settings: Settings = settingsWith()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <FxAlertSettingsPanel settings={settings} onSave={(patch) => saved.push(patch)} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  saved = [];
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('FxAlertSettingsPanel', () => {
  it('shows what is configured now', () => {
    renderPanel();
    expect(screen.getByLabelText(/Say nothing again until/)).toHaveValue('0.00500000');
    expect(screen.getByLabelText(/Absolute move/)).toHaveValue('0.00500000');
    expect(screen.getByLabelText(/A new 7-day high/)).toBeChecked();
    expect(screen.getByLabelText(/A new 90-day high/)).not.toBeChecked();
    expect(screen.getByLabelText(/Watch levels/)).toHaveValue('1.70000000, 1.75000000');
  });

  it('sends a rate as the string that was typed, never a number', async () => {
    renderPanel();
    const field = screen.getByLabelText(/Absolute move/);
    // type="number" would hand the browser a float, which is the rounding the
    // Decimal pipeline exists to prevent.
    expect(field).toHaveAttribute('type', 'text');
    expect(field).toHaveAttribute('inputMode', 'decimal');

    await userEvent.clear(field);
    await userEvent.type(field, '0.0075');
    // Nothing is sent until the field is left: the settings document lives on
    // the server, and saving per keystroke would be one PUT per character.
    expect(saved).toHaveLength(0);

    await userEvent.tab();
    const last = saved.at(-1)!;
    expect(last.fx_alerts!.absolute_rate_move).toBe('0.0075');
    expect(typeof last.fx_alerts!.absolute_rate_move).toBe('string');
  });

  it('parses a comma separated list into a list', async () => {
    renderPanel();
    const field = screen.getByLabelText(/Watch levels/);
    await userEvent.clear(field);
    await userEvent.type(field, '1.7200, 1.7400 , 1.7600');
    await userEvent.tab();

    expect(saved.at(-1)!.fx_alerts!.watch_levels).toEqual(['1.7200', '1.7400', '1.7600']);
  });

  it('keeps every other setting when one changes', async () => {
    renderPanel();
    await userEvent.click(screen.getByLabelText(/A new 90-day high/));

    const patched = saved.at(-1)!.fx_alerts!;
    expect(patched.alert_new_90d_high).toBe(true);
    // A section is replaced wholesale by the API, so a partial patch here would
    // silently reset everything it left out.
    expect(patched.watch_levels).toEqual(ALERTS.watch_levels);
    expect(patched.daily_cost_thresholds).toEqual(ALERTS.daily_cost_thresholds);
  });

  it('says what switching it off costs', () => {
    renderPanel(settingsWith({ enabled: false }));
    expect(screen.getByText(/you will simply not hear about either/)).toBeInTheDocument();
  });

  it('names the target currency on the money thresholds', () => {
    renderPanel();
    expect(screen.getByLabelText(/Worth mentioning \(NZD\)/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Daily carrying cost levels \(NZD\)/)).toBeInTheDocument();
  });
});
