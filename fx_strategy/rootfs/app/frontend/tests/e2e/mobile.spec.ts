import { expect, test } from '@playwright/test';

/**
 * The phone-sized checks, run under the `mobile` project so they use a real
 * mobile device profile rather than a resized desktop window.
 *
 * These deliberately assert nothing about the stored figures: they run against
 * whatever `critical-flow.spec.ts` left behind, and layout is what is under
 * test. The narrative itself runs once, in the `desktop` project only, because
 * it depends on starting from an empty database.
 */

const INGRESS = '/api/hassio_ingress/E2ETESTTOKEN';

test('the dashboard is usable on a phone', async ({ page }) => {
  await page.goto(`${INGRESS}/`);

  await expect(page.getByRole('heading', { name: 'FX Strategy Manager' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Sections' })).toBeVisible();

  // Nothing overflows horizontally: the body must never scroll sideways.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test('every section is reachable on a phone without sideways scrolling', async ({ page }) => {
  const paths = ['position', 'chart', 'conversions', 'settings', 'diagnostics'];
  for (const path of paths) {
    const response = await page.goto(`${INGRESS}/${path}`);
    expect(response?.status()).toBe(200);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `${path} overflows horizontally`).toBeLessThanOrEqual(1);
  }
});

test('editing a conversion opens a dialog that is actually on screen', async ({
  page,
  request,
}) => {
  const api = (path: string) => `${INGRESS}/api/v1/${path}`;

  await request.post(api('rates/manual'), { data: { rate: '1.7200' } });
  await request.post(api('fx/state'), {
    data: { current_source_balance: '500000', baseline_rate: '1.6000' },
  });
  await request.post(api('fx/conversions'), {
    data: { source_amount: '10000', target_amount: '17200', notes: 'Phone edit check' },
  });

  await page.goto(`${INGRESS}/conversions`);

  const row = page.locator('tbody tr').first();
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: 'Edit', exact: true }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toHaveAttribute('aria-label', /^Edit conversion /);

  // The whole point: the form must be inside the viewport, not scrolled off
  // above it. A dialog the user cannot see is the bug this replaced.
  const box = await dialog.boundingBox();
  const viewport = page.viewportSize();
  expect(box).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeLessThan(viewport!.height);

  // The warning that an edit does not move the balance is in the dialog, which
  // is where the question actually occurs to someone.
  await expect(dialog.getByText(/does not change your current balance/)).toBeVisible();

  // And Escape closes it.
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});
