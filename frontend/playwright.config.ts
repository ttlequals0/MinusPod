import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  timeout: 30_000,
  use: {
    baseURL: 'http://127.0.0.1:4174/ui/',
    channel: process.env.PLAYWRIGHT_CHANNEL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'phone-320', use: { viewport: { width: 320, height: 844 }, hasTouch: true } },
    { name: 'phone-390', use: { viewport: { width: 390, height: 844 }, hasTouch: true } },
    { name: 'tablet-768', use: { viewport: { width: 768, height: 1000 }, hasTouch: true } },
    { name: 'desktop-1440', use: { viewport: { width: 1440, height: 1000 } } },
  ],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4174 --strictPort',
    url: 'http://127.0.0.1:4174/ui/',
    env: { VITE_PROXY_TARGET: 'http://127.0.0.1:9', VITE_PROXY_COOKIE: '' },
    reuseExistingServer: false,
  },
});
