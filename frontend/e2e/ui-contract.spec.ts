import AxeBuilder from '@axe-core/playwright';
import { test, expect } from '@playwright/test';
import path from 'node:path';

// The app calls an unavailable backend; no API responses are fabricated.
test('failed settings stop loading and offer retry', async ({ page }) => {
  await page.goto('settings');
  await expect(page.getByRole('heading', { name: 'Settings', level: 1 })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Settings are unavailable', { timeout: 15_000 });
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeVisible();
  await expect(page.getByTestId('skeleton-rows')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Save Changes' })).toHaveCount(0);
});

test('search filters fit and failed requests are not empty results', async ({ page }) => {
  await page.goto('search?q=technology');
  await expect(page.getByRole('alert')).toContainText('Search', { timeout: 15_000 });
  const filters = page.locator('main [role="tab"]');
  await expect(filters).toHaveCount(6);
  const coarse = Boolean(test.info().project.use.hasTouch);
  for (const filter of await filters.all()) {
    const box = await filter.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    if (coarse) expect(box!.height).toBeGreaterThanOrEqual(44);
  }
  await expect(page.getByText('No results found', { exact: true })).toHaveCount(0);
});

test('tabs support arrows, Home and End and name their panel', async ({ page }) => {
  await page.goto('patterns');
  const tabs = page.getByRole('tab');
  await tabs.first().focus();
  await page.keyboard.press('End');
  await expect(tabs.last()).toBeFocused();
  await expect(tabs.last()).toHaveAttribute('aria-selected', 'true');
  const panelId = await tabs.last().getAttribute('aria-controls');
  await expect(page.locator(`[id="${panelId}"]`)).toHaveAttribute('role', 'tabpanel');
  await page.keyboard.press('Home');
  await expect(tabs.first()).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(tabs.nth(1)).toBeFocused();
  expect(await page.locator('[role="tab"][tabindex="0"]').count()).toBe(1);
});

test('nested import confirmation owns focus and Escape, then restores the trigger', async ({ page }) => {
  await page.goto('patterns');
  const trigger = page.getByRole('button', { name: 'Import', exact: true });
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: 'Import patterns', exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('Pattern JSON file').setInputFiles(path.resolve('package.json'));
  await dialog.getByLabel('Mode', { exact: true }).selectOption('replace');
  await dialog.getByRole('button', { name: 'Import', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: 'Replace all patterns?' });
  await expect(confirmation).toBeVisible();
  for (let step = 0; step < 5; step++) {
    await page.keyboard.press('Tab');
    expect(await confirmation.evaluate(el => el.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press('Escape');
  await expect(confirmation).toHaveCount(0);
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test('date range stays paired on phones and tablet controls retain touch height', async ({ page }) => {
  await page.goto('history?tab=downloads');
  const since = page.getByLabel('Since', { exact: true });
  const until = page.getByLabel('Until', { exact: true });
  await expect(since).toBeVisible();
  const a = (await since.boundingBox())!;
  const b = (await until.boundingBox())!;
  expect(Math.abs(a.y - b.y)).toBeLessThanOrEqual(1);
  expect(a.x + a.width).toBeLessThanOrEqual(b.x);
  expect(b.x + b.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  if (test.info().project.use.hasTouch) expect(Math.min(a.height, b.height)).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('plain typing does not open search and Ctrl+K opens a named combobox', async ({ page }) => {
  await page.goto('add');
  await expect(page.locator('h1')).toBeVisible();
  await page.keyboard.press('b');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.keyboard.press('Control+k');
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Search' })).toBeFocused();
});

test('discarding a nested editor restores the page and its trigger', async ({ page, request }) => {
  test.skip(!process.env.MINUSPOD_POPULATED_URL, 'Requires a populated, disposable audit instance.');
  const base = process.env.MINUSPOD_POPULATED_URL!;
  const response = await request.get(new URL('/api/v1/sponsors', base).href);
  const sponsors = await response.json();
  const name = (Array.isArray(sponsors) ? sponsors : sponsors.sponsors)[0].name;
  await page.goto(base + '/sponsors');
  const trigger = page.getByRole('button', { name: 'Add sponsor', exact: true });
  await trigger.click();
  const editor = page.getByRole('dialog', { name: 'Add sponsor', exact: true });
  await editor.locator('input[type="text"]').first().fill(name);
  await editor.getByRole('button', { name: 'Close', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: 'Discard unsaved changes?' });
  await confirmation.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(editor.locator('input[type="text"]').first()).toHaveValue(name);
  await editor.getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('[inert]')).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test('feed URL validation associates its visible error with the field', async ({ page }) => {
  await page.goto('add');
  const input = page.locator('#podcastInput');
  await input.fill('https://');
  await input.blur();
  await expect(input).toHaveAttribute('aria-invalid', 'true');
  await expect(input).toHaveAttribute('aria-describedby', 'podcast-url-feedback');
  await expect(page.locator('#podcast-url-feedback')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add feed', exact: true })).toBeDisabled();
});


test('add-feed controls have accessible names and valid ARIA relationships', async ({ page }) => {
  await page.goto('add');
  await page.getByText('Advanced options', { exact: false }).click();
  const scan = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(scan.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) }))).toEqual([]);
});

test('icon-only controls center their icon', async ({ page }) => {
  test.slow(); // four routes, two of them wait out the backend retry before their alert shows
  const failures: string[] = [];
  for (const route of ['', 'settings', 'search?q=technology', 'patterns']) {
    await page.goto(route);
    await expect(page.locator('main')).toBeVisible();
    if (route === 'settings' || route.startsWith('search')) {
      await expect(page.getByRole('alert').first()).toBeVisible({ timeout: 15_000 });
    }
    const found = await page.evaluate(() => {
      const hasText = (el: Element) => {
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          if (!node.textContent?.trim()) continue;
          const parent = node.parentElement;
          if (parent?.closest('.sr-only, [aria-hidden="true"]')) continue;
          const range = document.createRange();
          range.selectNodeContents(node);
          const r = range.getBoundingClientRect();
          if (r.width > 0 && r.height > 0) return true;
        }
        return false;
      };
      const out: string[] = [];
      for (const el of document.querySelectorAll('main :is(button, [role="button"], a[href], summary), header :is(button, [role="button"], a[href], summary)')) {
        const svg = el.querySelector('svg');
        const box = el.getBoundingClientRect();
        if (!svg || box.width === 0 || box.height === 0 || hasText(el)) continue;
        const s = svg.getBoundingClientRect();
        const dx = s.x + s.width / 2 - (box.x + box.width / 2);
        const dy = s.y + s.height / 2 - (box.y + box.height / 2);
        if (Math.abs(dx) > 1.25 || Math.abs(dy) > 1.25) {
          out.push(`aria-label="${el.getAttribute('aria-label')}" class="${el.getAttribute('class')}" dx=${dx.toFixed(2)} dy=${dy.toFixed(2)}`);
        }
      }
      return out;
    });
    failures.push(...found.map(f => `${route || '/'}: ${f}`));
  }
  expect(failures.join('\n')).toBe('');
});
