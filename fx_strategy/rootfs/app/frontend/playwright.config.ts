import { defineConfig, devices } from '@playwright/test';

/**
 * The end-to-end suite runs the real backend behind a simulated Ingress prefix,
 * so the tests exercise the same base-path resolution a Home Assistant install
 * does rather than a convenient localhost root.
 */
const PORT = Number(process.env.FX_E2E_PORT ?? 8199);

/**
 * Some environments ship a pre-installed Chromium whose build number does not
 * match the pinned @playwright/test version. Setting FX_CHROMIUM_PATH points
 * the tests at it instead of downloading a second copy.
 */
const executablePath = process.env.FX_CHROMIUM_PATH || undefined;

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['html', { open: 'never' }], ['list']] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      // The critical flow is one narrative over a single database, so it runs
      // here and only here. Replaying it under a second project would start
      // from the first project's leftovers.
      name: 'desktop',
      testIgnore: /mobile\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], launchOptions: { executablePath } },
    },
    {
      name: 'mobile',
      testMatch: /mobile\.spec\.ts/,
      use: {
        ...devices['iPhone 13'],
        // iPhone 13 defaults to WebKit; the layout matters more than the
        // engine, and Chromium is what this environment provides.
        browserName: 'chromium',
        launchOptions: { executablePath },
      },
    },
  ],
  webServer: {
    command: 'node tests/e2e/server.mjs',
    port: PORT,
    // Never reused, even locally: the server creates a fresh temporary database
    // on start, and the critical flow asserts figures that only hold from an
    // empty one. Reusing a server from an earlier run makes the suite fail on
    // stale data rather than on a regression.
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
