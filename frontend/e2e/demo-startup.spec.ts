import { test, expect } from '@playwright/test';

test('a missing snapshot stops startup before the app calls an API', async ({ page }) => {
  const apiRequests: string[] = [];
  page.context().on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(request.url());
  });
  await page.route('**/demo/fixtures/index.json', (route) => route.abort());
  await page.goto('/');
  await expect(page.locator('#root')).toHaveText('The demo failed to start. Reload the page to try again.');
  await expect(page.getByRole('navigation')).toHaveCount(0);
  expect(apiRequests).toEqual([]);
});
