import { test, expect, type Page, type Route } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

// Screenshots are written only when MINUSPOD_SHOTS names a directory; one project is enough, widths are set per describe.
const SHOTS = process.env.MINUSPOD_SHOTS;
const WIDTHS = [320, 390, 466, 626, 768, 890, 1024, 1280, 1440];
const DARK_WIDTHS = [390, 1440];

const feed = {
  slug: 'example-podcast',
  title: 'The Daily Tech Show',
  sourceUrl: 'https://example.com/feed.xml',
  feedUrl: 'https://example.com/modified.xml',
  feedType: 'subscribed',
  episodeCount: 3,
  processingMode: 'standard',
  descriptionTrimPattern: '*Donations*',
  descriptionTrimMode: 'to_next',
  skipTranscription: false,
};

async function mockApi(page: Page) {
  const state = { ...feed };
  await page.route('**/api/v1/**', async (route: Route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname.replace(/^.*\/api\/v1/, '');
    let body: unknown;
    if (p === '/auth/status') body = { passwordSet: true, authenticated: true };
    else if (p === '/feeds/example-podcast' && req.method() === 'PATCH') {
      Object.assign(state, req.postDataJSON());
      body = state;
    } else if (p === '/feeds/example-podcast') body = state;
    else if (p === '/feeds') body = { feeds: [state] };
    else if (p.endsWith('/episodes')) body = { episodes: [], total: 0 };
    else if (p === '/settings') body = { defaults: {} };
    else if (p.endsWith('/cue-templates')) body = [];
    else if (p === '/status') body = { currentJob: null, jobs: [], queueLength: 0, queuedEpisodes: [], feedRefreshes: [], hold: { queuePaused: false, holdUntil: null, holdSince: null, offlineHeld: 0, offlineServices: [] }, lastUpdated: Date.now() / 1000 };
    if (body === undefined) return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
}

async function shoot(page: Page, name: string) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false });
}

for (const width of WIDTHS) {
  for (const scheme of DARK_WIDTHS.includes(width) ? (['light', 'dark'] as const) : (['light'] as const)) {
    test.describe(`chapter trim skip transcription width ${width} ${scheme}`, () => {
      const phone = width < 768;
      test.use({ viewport: { width, height: 900 }, hasTouch: phone, isMobile: phone, colorScheme: scheme });

      test('toggle off and on', async ({ page }, info) => {
        test.skip(info.project.name !== 'desktop-1440', 'width is set by the describe, one project is enough');
        await mockApi(page);
        await page.addInitScript((t) => localStorage.setItem('theme', t), scheme);
        await page.goto('feeds/example-podcast?section=settings');
        const toggle = page.getByRole('switch', { name: 'Skip transcription' });
        await expect(toggle).toHaveCount(1);
        const place = () => toggle.evaluate((el) => window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 360, behavior: 'instant' }));
        const suffix = scheme === 'dark' ? '-dark' : '';

        await place();
        await page.waitForTimeout(300);
        await expect(toggle).toHaveAttribute('aria-checked', 'false');
        const box = (await toggle.boundingBox())!;
        expect(box.height).toBeGreaterThanOrEqual(phone ? 44 : 20);
        if (phone) expect(box.width).toBeGreaterThanOrEqual(44);
        await expect(page.getByText(/matching chapter skip transcription and ad detection/)).toBeVisible();
        await noOverflow(page);
        await shoot(page, `chapter-trim-off-${width}${suffix}`);

        await toggle.click();
        await expect(toggle).toHaveAttribute('aria-checked', 'true');
        await place();
        await page.waitForTimeout(300);
        await noOverflow(page);
        await shoot(page, `chapter-trim-on-${width}${suffix}`);
      });
    });
  }
}
