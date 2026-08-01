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
  for (const path of ['chart', 'strategy', 'scenarios', 'conversions', 'settings', 'diagnostics']) {
    const response = await page.goto(`${INGRESS}/${path}`);
    expect(response?.status()).toBe(200);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `${path} overflows horizontally`).toBeLessThanOrEqual(1);
  }
});
