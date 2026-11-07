import { test, expect } from '@playwright/test';
import { DEMO_BANNER, DEMO_WRITE_ERROR, DEMO_WRITE_TOOLTIP } from '../src/demo/copy';

test.skip(process.env.MINUSPOD_DEMO_E2E !== '1', 'demo smoke runs against the demo build on demand');

test('demo build serves fixtures, blocks writes, and explains itself', async ({ page }) => {
  const requested: string[] = [];
  page.context().on('request', (req) => requested.push(req.url()));

  await page.goto('/', { waitUntil: 'load' });
  await expect(page.getByText(DEMO_BANNER)).toBeVisible();
  const scripts = await page.evaluate(() => navigator.serviceWorker.getRegistrations().then((rs) => rs.map((r) => (r.active ?? r.installing ?? r.waiting)?.scriptURL ?? '')));
  expect(scripts.some((url) => url.endsWith('/mockServiceWorker.js'))).toBe(true);
  expect(scripts.filter((url) => url.endsWith('/sw.js'))).toEqual([]);
  expect(requested.filter((url) => url.endsWith('/sw.js'))).toEqual([]);

  await page.goto('/settings');
  await page.getByRole('button', { name: 'Podcast search' }).click();
  await page.locator('#podcastSearchProvider').selectOption('podcastindex');
  const wrapper = page.locator('span.cursor-not-allowed').filter({ has: page.getByRole('button', { name: 'Save changes' }) });
  await expect(wrapper).toHaveAttribute('title', DEMO_WRITE_TOOLTIP);
  await expect(wrapper.getByRole('button', { name: 'Save changes' })).toBeDisabled();

  await page.goto('/api-docs');
  await expect(page.locator('#swagger-ui .opblock').first()).toBeVisible();
  expect(await page.locator('#swagger-ui .opblock').count()).toBeGreaterThan(0);

  await page.goto('/add');
  await page.getByLabel(/RSS or YouTube URL|Search podcasts/).fill('https://example.com/feed.xml');
  // The submit button is disabled in demo, so submit the form directly to exercise the 403 path.
  await page.locator('form').filter({ has: page.locator('#podcastInput') }).evaluate((form: HTMLFormElement) => form.requestSubmit());
  await expect(page.getByRole('alert').filter({ hasText: DEMO_WRITE_ERROR })).toContainText(DEMO_WRITE_ERROR);
});
