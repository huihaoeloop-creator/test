import { defineConfig, devices } from '@playwright/test';

import { proxyFromEnv } from './scripts/lib/env.mjs';

const PORT = Number(process.env.PORT ?? 4173);
const BASE_URL = process.env.BASE_URL ?? `http://127.0.0.1:${PORT}`;

/**
 * Headless-first Playwright config.
 *
 * Browsers are resolved from PLAYWRIGHT_BROWSERS_PATH when it is set (this is
 * how the sandboxed CI/agent images ship a pre-installed Chromium), otherwise
 * Playwright falls back to its own per-user cache.
 */
export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',

  use: {
    headless: true,
    proxy: proxyFromEnv(BASE_URL),
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },

  // Serves ./fixtures so the suite has a real URL to drive without any
  // network access. Point BASE_URL at your own dev server to skip it.
  webServer: process.env.BASE_URL
    ? undefined
    : {
        command: `npx http-server fixtures -p ${PORT} -s -c-1`,
        url: `http://127.0.0.1:${PORT}`,
        reuseExistingServer: !process.env.CI,
        timeout: 30_000,
      },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
