import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  timeout: 30_000,
  use: {
    baseURL: 'http://127.0.0.1:4174/ui/',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    storageState: process.env.MINUSPOD_STORAGE_STATE || undefined,
  },
  projects: [
    { name: 'phone-320', use: { channel: process.env.PLAYWRIGHT_CHANNEL, viewport: { width: 320, height: 844 }, hasTouch: true } },
    { name: 'phone-390', use: { channel: process.env.PLAYWRIGHT_CHANNEL, viewport: { width: 390, height: 844 }, hasTouch: true } },
    { name: 'tablet-768', use: { channel: process.env.PLAYWRIGHT_CHANNEL, viewport: { width: 768, height: 1000 }, hasTouch: true } },
    { name: 'desktop-1440', use: { channel: process.env.PLAYWRIGHT_CHANNEL, viewport: { width: 1440, height: 1000 } } },
    ...[320, 390, 768, 1440].map((width) => ({
      name: `webkit-${width}`,
      use: { browserName: 'webkit' as const, viewport: { width, height: width < 768 ? 844 : 1000 }, hasTouch: width < 1440 },
    })),
  ],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4174 --strictPort',
    url: 'http://127.0.0.1:4174/ui/',
    env: { VITE_PROXY_TARGET: 'http://127.0.0.1:9', VITE_PROXY_COOKIE: '' },
    reuseExistingServer: false,
  },
});
