import { test, expect, type Page, type Route } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

// Screenshots are written only when MINUSPOD_SHOTS names a directory.
const SHOTS = process.env.MINUSPOD_SHOTS;

const feed = {
  slug: 'example-podcast',
  title: 'The Daily Tech Show',
  sourceUrl: 'https://example.com/feed.xml',
  feedUrl: 'https://example.com/modified.xml',
  feedType: 'subscribed',
  episodeCount: 3,
  processingMode: 'standard',
  showNotesChapters: true,
};

async function mockApi(page: Page, patches: Record<string, unknown>[]) {
  const state = { ...feed };
  await page.route('**/api/v1/**', async (route: Route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname.replace(/^.*\/api\/v1/, '');
    let body: unknown;
    if (p === '/auth/status') body = { passwordSet: true, authenticated: true };
    else if (p === '/feeds/example-podcast' && req.method() === 'PATCH') {
      const patch = req.postDataJSON();
      patches.push(patch);
      Object.assign(state, patch);
      body = state;
    } else if (p === '/feeds/example-podcast') body = state;
    else if (p === '/feeds') body = { feeds: [state] };
    else if (p.endsWith('/episodes')) body = { episodes: [], total: 0 };
    else if (p === '/settings') body = { llmEnabled: true, defaults: {} };
    else if (p.endsWith('/cue-templates')) body = [];
    else if (p === '/status') body = { currentJob: null, jobs: [], queueLength: 0, queuedEpisodes: [], feedRefreshes: [], hold: { queuePaused: false, holdUntil: null, holdSince: null, offlineHeld: 0, offlineServices: [] }, lastUpdated: Date.now() / 1000 };
    if (body === undefined) return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.addInitScript(() => localStorage.setItem('feed-settings-example-podcast', 'true'));
}

async function shoot(page: Page, state: string, project: string) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `show-notes-toggle-${state}-${project}.png`), fullPage: false });
}

test('chapters from show notes toggle', async ({ page }, info) => {
  const phone = info.project.name.startsWith('phone');
  const patches: Record<string, unknown>[] = [];
  await mockApi(page, patches);
  await page.goto('feeds/example-podcast');
  const toggle = page.getByRole('switch', { name: 'Chapters from show notes' });
  await expect(toggle).toHaveCount(1);
  const place = async () => {
    await toggle.evaluate((el) => window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 300, behavior: 'instant' }));
    await page.waitForTimeout(300);
  };
  const noOverflow = async () => {
    if (!phone) return;
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  };

  await place();
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByText("Use the description's timestamps as chapters when it lists three or more.")).toBeVisible();
  const box = (await toggle.boundingBox())!;
  console.log(`[${info.project.name}] switch box ${box.width} x ${box.height} CSS px`);
  if (phone) {
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);
  }
  await noOverflow();
  await shoot(page, 'on', info.project.name);

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  expect(patches.at(-1)).toMatchObject({ showNotesChapters: false });
  await place();
  await noOverflow();
  await shoot(page, 'off', info.project.name);
});
