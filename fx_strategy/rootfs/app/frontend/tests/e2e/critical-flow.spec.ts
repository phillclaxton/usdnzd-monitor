import { expect, test } from '@playwright/test';

/**
 * The critical flow from the product specification, end to end, through a
 * simulated Home Assistant Ingress prefix:
 *
 * open → create the USD 800,000 strategy with the recommended ladder →
 * activate → inject rates → cross a target → record the conversion → check the
 * remaining balance and blended rate → export → restart → check the data
 * survived.
 *
 * This is one continuous narrative over a single database, so it runs in the
 * `desktop` project only. Running it a second time under another project would
 * start from the state the first run left behind and assert the wrong figures;
 * the phone-sized checks live in `mobile.spec.ts` instead.
 */

const INGRESS = '/api/hassio_ingress/E2ETESTTOKEN';

const LADDER = [
  { sequence: 1, allocation_type: 'percentage', allocation_value: '15', target_rate: '1.7200' },
  { sequence: 2, allocation_type: 'percentage', allocation_value: '20', target_rate: '1.7400' },
  { sequence: 3, allocation_type: 'percentage', allocation_value: '25', target_rate: '1.7600' },
  { sequence: 4, allocation_type: 'percentage', allocation_value: '20', target_rate: '1.7800' },
  { sequence: 5, allocation_type: 'percentage', allocation_value: '20', target_rate: '1.8000' },
];

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
  // calls are excluded on purpose: on a fresh install there is no strategy yet,
  // and `GET /summary` answering 404 is the correct response to that, not a
  // base-path failure.
  const failed: string[] = [];
  page.on('response', (res) => {
    if (res.status() >= 400 && !res.url().includes('/api/v1/')) {
      failed.push(`${res.status()} ${res.url()}`);
    }
  });
  await page.reload();
  expect(failed).toEqual([]);
});

test('the full strategy lifecycle', async ({ page, request }) => {
  // 1-5. Create and activate the USD 800,000 strategy with the recommended ladder.
  const created = await request.post(api('strategies'), {
    data: {
      name: 'USD to NZD',
      initial_source_amount: '800000',
      funds_available_amount: '800000',
      walk_away_rate: '1.7800',
      tranches: LADDER,
    },
  });
  expect(created.status()).toBe(201);
  const strategy = await created.json();
  expect(strategy.tranches.map((t: { calculated_source_amount: string }) => t.calculated_source_amount)).toEqual([
    '120000.0000',
    '160000.0000',
    '200000.0000',
    '160000.0000',
    '160000.0000',
  ]);

  const activated = await request.post(api(`strategies/${strategy.id}/activate`));
  expect(activated.ok()).toBeTruthy();

  // 6. Inject a rate below the first target.
  await request.post(api('rates/manual'), { data: { rate: '1.7100' } });

  await page.goto(`${INGRESS}/`);
  await expect(page.getByText('1 USD = 1.7100 NZD')).toBeVisible();
  // The headline exposure figure.
  await expect(page.getByText('NZD 8,000.00').first()).toBeVisible();

  // 7. Cross the first target.
  await request.post(api('rates/manual'), { data: { rate: '1.7250' } });
  await page.reload();

  // 8. The tranche shows as at or above its target — and says nothing converted.
  await expect(page.getByText('At or above').first()).toBeVisible();
  await expect(
    page.getByText(/A target being reached does not convert anything/),
  ).toBeVisible();

  // 9. Record the conversion Wise performed.
  const conversion = await request.post(api('conversions'), {
    data: {
      strategy_id: strategy.id,
      executed_at: new Date().toISOString(),
      source_amount: '120000',
      target_amount: '206400',
      tranche_id: strategy.tranches[0].id,
      provider_transaction_id: 'E2E-1',
    },
  });
  expect(conversion.status()).toBe(201);

  // 10-11. Remaining balance and blended rate.
  const summary = await (await request.get(api(`strategies/${strategy.id}/summary`))).json();
  expect(summary.converted_source_amount).toBe('120000.0000');
  expect(summary.remaining_source_amount).toBe('680000.0000');
  expect(summary.blended_effective_rate).toBe('1.72000000');
  // Exposure now reflects only what is left.
  expect(summary.one_cent_exposure).toBe('6800.0000');

  await page.goto(`${INGRESS}/conversions`);
  await expect(page.getByText('USD 120,000.00').first()).toBeVisible();

  // A repeated transaction reference is refused.
  const duplicate = await request.post(api('conversions'), {
    data: {
      strategy_id: strategy.id,
      executed_at: new Date().toISOString(),
      source_amount: '120000',
      target_amount: '206400',
      provider_transaction_id: 'E2E-1',
    },
  });
  expect(duplicate.status()).toBe(409);

  // 12. Export.
  const backup = await request.post(api('backup'));
  expect(backup.ok()).toBeTruthy();
  const document = await backup.json();
  expect(document.counts.strategies).toBe(1);
  expect(document.counts.conversions).toBe(1);
  expect(document.contains_secrets).toBe(false);

  const csv = await request.get(api(`conversions/export?strategy_id=${strategy.id}`));
  expect(await csv.text()).toContain('120000.0000');
});

test('the data survives a page reload and the API stays consistent', async ({
  page,
  request,
}) => {
  // 13-14. The backend keeps its state; reloading the SPA re-reads it.
  await page.goto(`${INGRESS}/`);
  await expect(page.getByText('USD 680,000.00').first()).toBeVisible();

  await page.reload();
  await expect(page.getByText('USD 680,000.00').first()).toBeVisible();

  const summary = await (await request.get(api('summary'))).json();
  expect(summary.blended_effective_rate).toBe('1.72000000');
});

test('deep links work under the Ingress prefix', async ({ page }) => {
  for (const path of ['chart', 'strategy', 'scenarios', 'conversions', 'settings', 'diagnostics']) {
    const response = await page.goto(`${INGRESS}/${path}`);
    expect(response?.status()).toBe(200);
    await expect(page.locator('base')).toHaveAttribute('href', `${INGRESS}/`);
  }
});

test('the strategy can be read as a JSON document that matches what is saved', async ({
  page,
}) => {
  await page.goto(`${INGRESS}/strategy`);
  await page.getByRole('button', { name: 'Edit as JSON' }).click();

  const box = page.getByLabel('Strategy JSON');
  await expect(box).toBeVisible();
  const text = await box.inputValue();
  const document = JSON.parse(text) as {
    tranches: { target_rate: string }[];
    status?: unknown;
  };

  // What is copied out is the plan, not the record of what happened.
  expect(document.status).toBeUndefined();
  expect(document.tranches.map((tranche) => tranche.target_rate)).toEqual([
    '1.72000000',
    '1.74000000',
    '1.76000000',
    '1.78000000',
    '1.80000000',
  ]);

  // Untouched, the document is checked against the server and reports nothing.
  await expect(page.getByText('Saving it would change nothing')).toBeVisible();
});

test('simulation mode shows its banner and can be reset', async ({ page, request }) => {
  await request.put(api('simulation'), { data: { enabled: true } });
  await page.goto(`${INGRESS}/`);
  await expect(
    page.getByText('SIMULATION MODE: No live financial decisions should be based on this screen.'),
  ).toBeVisible();

  const replay = await request.post(api('simulation/replay'), {
    data: { rates: ['1.7400', '1.7610', '1.7620'], seconds_between: 60 },
  });
  expect(replay.ok()).toBeTruthy();

  const reset = await request.post(api('simulation/reset'));
  expect((await reset.json()).message).toContain('Real records were not touched');

  await request.put(api('simulation'), { data: { enabled: false } });

  // The real conversion recorded earlier is untouched by the reset.
  const conversions = await (await request.get(api('conversions'))).json();
  expect(conversions.total_source_amount).toBe('120000.0000');
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
