import { defineConfig } from '@playwright/test';

const demoPort = process.env.MINUSPOD_DEMO_PORT || '4175';

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
    { name: 'phone-320', testIgnore: /demo-(smoke|startup)/, use: { channel: process.env.PLAYWRIGHT_CHANNEL, viewport: { width: 320, height: 844 }, hasTouch: true } },
    { name: 'phone-390', testIgnore: /demo-(smoke|startup)/, use: { channel: process.env.PLAYWRIGHT_CHANNEL, viewport: { width: 390, height: 844 }, hasTouch: true } },
    { name: 'tablet-768', testIgnore: /demo-(smoke|startup)/, use: { channel: process.env.PLAYWRIGHT_CHANNEL, viewport: { width: 768, height: 1000 }, hasTouch: true } },
    { name: 'desktop-1440', testIgnore: /demo-(smoke|startup)/, use: { channel: process.env.PLAYWRIGHT_CHANNEL, viewport: { width: 1440, height: 1000 } } },
    ...[320, 390, 768, 1440].map((width) => ({
      name: `webkit-${width}`,
      testIgnore: /demo-(smoke|startup)/,
      use: { browserName: 'webkit' as const, viewport: { width, height: width < 768 ? 844 : 1000 }, hasTouch: width < 1440 },
    })),
    ...(process.env.MINUSPOD_DEMO_E2E === '1'
      ? [{
          name: 'demo-1440',
          testMatch: /demo-(smoke|startup)/,
          use: { channel: process.env.PLAYWRIGHT_CHANNEL, viewport: { width: 1440, height: 1000 }, baseURL: `http://127.0.0.1:${demoPort}/` },
        }]
      : []),
  ],
  webServer: process.env.MINUSPOD_DEMO_E2E === '1'
    ? {
        command: `npm run dev:demo -- --host 127.0.0.1 --port ${demoPort} --strictPort`,
        url: `http://127.0.0.1:${demoPort}/`,
        reuseExistingServer: false,
      }
    : {
        command: 'npm run dev -- --host 127.0.0.1 --port 4174 --strictPort',
        url: 'http://127.0.0.1:4174/ui/',
        env: { VITE_PROXY_TARGET: 'http://127.0.0.1:9', VITE_PROXY_COOKIE: '' },
        reuseExistingServer: false,
      },
});
