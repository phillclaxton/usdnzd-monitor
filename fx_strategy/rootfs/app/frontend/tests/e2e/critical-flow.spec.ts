import { expect, test } from '@playwright/test';

/**
 * The critical flow, end to end, through a simulated Home Assistant Ingress
 * prefix:
 *
 * open → enter the position → record a conversion → watch the improvement
 * figures move → export the state → restart → check the data survived.
 *
 * This is one continuous narrative over a single database, so it runs in the
 * `desktop` project only. Running it a second time under another project would
 * start from the state the first run left behind and assert the wrong figures;
 * the phone-sized checks live in `mobile.spec.ts` instead.
 */

const INGRESS = '/api/hassio_ingress/E2ETESTTOKEN';

const api = (path: string) => `${INGRESS}/api/v1/${path}`;

test.describe.configure({ mode: 'serial' });

test('the app opens under an Ingress prefix with relative assets', async ({ page }) => {
  const response = await page.goto(`${INGRESS}/`);
  expect(response?.status()).toBe(200);

  // The injected <base href> is what makes every relative URL resolve.
  const baseHref = await page.locator('base').getAttribute('href');
  expect(baseHref).toBe(`${INGRESS}/`);

  await expect(page.getByRole('heading', { name: 'FX Strategy Manager' })).toBeVisible();

  // Every asset resolved under the prefix rather than at the origin root. API
  // calls are excluded on purpose: on a fresh install `GET /fx/state` answers
  // 200 with a null position, but other routes may legitimately 404.
  const failed: string[] = [];
  page.on('response', (res) => {
    if (res.status() >= 400 && !res.url().includes('/api/v1/')) {
      failed.push(`${res.status()} ${res.url()}`);
    }
  });
  await page.reload();
  expect(failed).toEqual([]);
});

test('a fresh install offers the position form and has nothing to value', async ({
  page,
  request,
}) => {
  const state = await (await request.get(api('fx/state'))).json();
  // Not a 404: having entered nothing is a normal state to describe.
  expect(state.position).toBeNull();
  expect(state.metrics).toBeNull();

  await page.goto(`${INGRESS}/`);
  await expect(page.getByText(/Nothing is saved yet/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save position' })).toBeVisible();
});

test('the position, what it is worth, and what waiting for it costs', async ({ page, request }) => {
  await request.post(api('rates/manual'), { data: { rate: '1.7500' } });

  // 800,000 USD against a 1.7000 baseline, with a 36,500 offset shortfall at
  // 6%. Every figure below divides exactly, so a wrong answer is obvious.
  const saved = await request.post(api('fx/state'), {
    data: {
      current_source_balance: '800000',
      baseline_rate: '1.7000',
      floating_loan_rate: '0.0600',
      current_offset_shortfall_nzd: '36500',
      monthly_nzd_burn: '4000',
    },
  });
  expect(saved.ok()).toBeTruthy();

  const state = await (await request.get(api('fx/state'))).json();
  expect(state.metrics.current_target_value).toBe('1400000.0000');
  // 800,000 x (1.7500 - 1.7000).
  expect(state.metrics.unrealised_improvement).toBe('40000.0000');
  // 36,500 x 0.06 / 365 is 6.00 a day, and / 12 is 182.50 a month.
  expect(state.metrics.daily_carrying_cost).toBe('6.0000');
  expect(state.metrics.monthly_carrying_cost).toBe('182.5000');

  await page.goto(`${INGRESS}/`);
  await expect(page.getByText('NZD 1,400,000.00')).toBeVisible();
  // Twice over, in fact: with nothing converted yet, the unrealised figure and
  // the total improvement are the same number.
  await expect(page.getByText('NZD 40,000.00').first()).toBeVisible();
  await expect(page.getByText('NZD 6.00')).toBeVisible();
});

test('recording a conversion reduces the balance and realises the improvement', async ({
  page,
  request,
}) => {
  // 120,000 at 1.7600 against the 1.7000 baseline: 7,200 realised.
  const recorded = await request.post(api('fx/conversions'), {
    data: {
      executed_at: new Date().toISOString(),
      source_amount: '120000',
      target_amount: '211200',
      provider_transaction_id: 'E2E-1',
    },
  });
  expect(recorded.status()).toBe(201);

  const state = await (await request.get(api('fx/state'))).json();
  // This is the endpoint that owns the balance, and the whole reason it is
  // separate from POST /conversions.
  expect(state.position.current_source_balance).toBe('680000.0000');
  expect(state.metrics.realised.confirmed).toBe('7200.0000');
  expect(state.metrics.realised.includes_estimates).toBe(false);
  expect(state.metrics.total_source_converted).toBe('120000.0000');

  await page.goto(`${INGRESS}/conversions`);
  await expect(page.getByText('USD 120,000.00').first()).toBeVisible();
  await expect(page.getByText('7,200.00').first()).toBeVisible();

  // A repeated transaction reference is refused.
  const duplicate = await request.post(api('conversions'), {
    data: {
      executed_at: new Date().toISOString(),
      source_amount: '120000',
      target_amount: '211200',
      provider_transaction_id: 'E2E-1',
    },
  });
  expect(duplicate.status()).toBe(409);
});

test('an estimated conversion is never presented as a confirmed figure', async ({
  page,
  request,
}) => {
  const recorded = await request.post(api('fx/conversions'), {
    data: {
      executed_at: new Date().toISOString(),
      source_amount: '30000',
      target_amount: '54000',
      provider_transaction_id: 'E2E-EST',
      amounts_estimated: true,
    },
  });
  expect(recorded.status()).toBe(201);

  const state = await (await request.get(api('fx/state'))).json();
  // 30,000 at 1.8000 against a 1.7000 baseline is 3,000 — and it stays out of
  // the confirmed figure, which is what a consumer reads.
  expect(state.metrics.realised.confirmed).toBe('7200.0000');
  expect(state.metrics.realised.estimated).toBe('3000.0000');
  expect(state.metrics.realised.total).toBe('10200.0000');
  expect(state.metrics.realised.includes_estimates).toBe(true);

  await page.goto(`${INGRESS}/`);
  await expect(page.getByText('NZD 7,200.00').first()).toBeVisible();
  await expect(page.getByText(/plus about NZD 3,000.00/)).toBeVisible();
  await expect(page.getByText('Estimated').first()).toBeVisible();
});

test('the state can be exported and the data survives a reload', async ({ page, request }) => {
  const exported = await request.get(api('fx/state/export'));
  expect(exported.ok()).toBeTruthy();
  const document = await exported.json();
  // 800,000 less both recorded conversions: recording one is what moves it.
  expect(document.source_balance).toBe('650000.0000');
  expect(document.conversions).toHaveLength(2);
  // Three fields, not one: a consumer reading only the confirmed figure cannot
  // pick up an estimate by accident.
  expect(document.realised_confirmed).toBe('7200.0000');
  expect(document.realised_estimated).toBe('3000.0000');
  expect(document.includes_estimates).toBe(true);

  const backup = await request.post(api('backup'));
  expect(backup.ok()).toBeTruthy();
  const dump = await backup.json();
  expect(dump.counts.fx_position).toBe(1);
  expect(dump.counts.conversions).toBe(2);
  expect(dump.contains_secrets).toBe(false);

  await page.goto(`${INGRESS}/`);
  await expect(page.getByText('USD 650,000.00').first()).toBeVisible();
  await page.reload();
  await expect(page.getByText('USD 650,000.00').first()).toBeVisible();

  const csv = await request.get(api('conversions/export'));
  expect(await csv.text()).toContain('120000.0000');
});

test('deep links work under the Ingress prefix', async ({ page }) => {
  const paths = ['position', 'chart', 'conversions', 'settings', 'diagnostics'];
  for (const path of paths) {
    const response = await page.goto(`${INGRESS}/${path}`);
    expect(response?.status()).toBe(200);
    await expect(page.locator('base')).toHaveAttribute('href', `${INGRESS}/`);
  }
});

test('simulation mode shows its banner and can be reset', async ({ page, request }) => {
  await request.put(api('simulation'), { data: { enabled: true } });
  await page.goto(`${INGRESS}/`);
  await expect(
    page.getByText('SIMULATION MODE: No live financial decisions should be based on this screen.'),
  ).toBeVisible();

  const replay = await request.post(api('simulation/replay'), {
    data: { rates: ['1.7400', '1.7610', '1.7720'], seconds_between: 60 },
  });
  expect(replay.ok()).toBeTruthy();

  const reset = await request.post(api('simulation/reset'));
  expect((await reset.json()).message).toContain('Real records were not touched');

  await request.put(api('simulation'), { data: { enabled: false } });

  // The real conversions recorded earlier are untouched by the reset.
  const conversions = await (await request.get(api('conversions'))).json();
  expect(conversions.total_source_amount).toBe('150000.0000');
});

test('no credential appears anywhere in the diagnostics bundle', async ({ request }) => {
  await request.put(api('wise/credentials'), {
    data: { api_token: 'e2e-secret-token-123456', profile_id: '12345678' },
  });
  const bundle = await request.get(api('diagnostics/bundle'));
  const text = await bundle.text();

  expect(text).not.toContain('e2e-secret-token-123456');
  // The profile ID is masked rather than printed.
  expect(text).not.toContain('12345678');
  expect(text).toContain('12****78');
});
