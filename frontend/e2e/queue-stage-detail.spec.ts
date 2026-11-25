import { test, expect, type Page, type Route } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

// Screenshots are written only when MINUSPOD_SHOTS names a directory; one project is enough, widths are set per describe.
const SHOTS = process.env.MINUSPOD_SHOTS;
const WIDTHS = [320, 390, 466, 626, 768, 890, 1024, 1280, 1440];
const DARK_WIDTHS = [320, 390, 1440];

const jobs = [
  {
    slug: 'example-podcast',
    episodeId: 'a1b2c3d4e5f6',
    title: 'The Daily Tech Show: an exceptionally long episode title that keeps going so it has to truncate or wrap cleanly beside the stage meta line',
    podcastName: 'The Daily Tech Show',
    stage: 'pass1:transcribing',
    progress: 20,
    startedAt: Date.now() / 1000 - 192,
    elapsed: 192,
    stageDetail: { processed: 894, total: 2706, unit: 'seconds' },
  },
  {
    slug: 'example-podcast',
    episodeId: 'b2c3d4e5f6a1',
    title: 'A second episode without stage detail',
    podcastName: 'The Daily Tech Show',
    stage: 'pass1:detecting',
    progress: 45,
    startedAt: Date.now() / 1000 - 600,
    elapsed: 600,
    stageDetail: null,
  },
];

const status = {
  currentJob: jobs[0],
  jobs,
  queueLength: 0,
  queuedEpisodes: [],
  feedRefreshes: [],
  hold: { queuePaused: false, holdUntil: null, holdSince: null, offlineHeld: 0, offlineServices: [] },
  lastUpdated: Date.now() / 1000,
};

async function mockApi(page: Page) {
  await page.route('**/api/v1/**', async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, '');
    let body: unknown;
    if (p === '/auth/status') body = { passwordSet: true, authenticated: true };
    else if (p === '/status') body = status;
    else if (p === '/settings') body = { defaults: {} };
    else if (p === '/settings/processing-schedule') body = { enabled: false, open: true, nextChangeAt: null };
    else if (p === '/settings/offline-queue') body = { enabled: false, ttlHours: 48, deferredCount: 0 };
    else if (p === '/settings/rate-limit-hold') body = { enabled: false, holdUntil: null, llmUsageUrl: '', rateLimitProbeMinutes: 5 };
    else if (p === '/episodes/processing') body = jobs.map((j) => ({ episodeId: j.episodeId, slug: j.slug, title: j.title, podcast: j.podcastName, startedAt: j.startedAt, stage: j.stage }));
    else if (p.endsWith('/admission')) body = { paused: false, activeRuns: 2, queuedEpisodes: 0 };
    if (body === undefined) return route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not mocked"}' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.addInitScript(() => localStorage.setItem('queue-page-control', 'true'));
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
}

async function shoot(page: Page, view: string, width: number, suffix = '') {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${view}-${width}${suffix}.png`), fullPage: false });
}

for (const width of WIDTHS) {
  for (const scheme of DARK_WIDTHS.includes(width) ? (['light', 'dark'] as const) : (['light'] as const)) {
    test.describe(`stage detail width ${width} ${scheme}`, () => {
      const phone = width < 768;
      test.use({ viewport: { width, height: 900 }, hasTouch: phone, isMobile: phone, colorScheme: scheme });

      test.beforeEach(async ({ page }, info) => {
        test.skip(info.project.name !== 'desktop-1440', 'width is set by the describe, one project is enough');
        await mockApi(page);
        await page.addInitScript((t) => localStorage.setItem('theme', t), scheme);
        await page.goto('queue');
        await page.getByRole('heading', { name: 'In progress' }).waitFor();
      });

      test('queue job meta line', async ({ page }) => {
        const meta = page.getByText('14.9 / 45.1 min - 20% - 3m 12s');
        await expect(meta).toBeVisible();
        const metaBox = (await meta.boundingBox())!;
        const titleBox = (await page.getByRole('link', { name: /exceptionally long/ }).boundingBox())!;
        // Meta line stays inside the viewport and never overlaps the title horizontally.
        expect(metaBox.x + metaBox.width).toBeLessThanOrEqual(width);
        expect(metaBox.x >= titleBox.x + titleBox.width - 1 || metaBox.y >= titleBox.y + titleBox.height - 1).toBe(true);
        await expect(page.getByText('45% - 10m')).toBeVisible();
        await noOverflow(page);
        await page.getByRole('heading', { name: 'In progress' }).evaluate((el) => window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 170, behavior: 'instant' }));
        await page.waitForTimeout(300);
        await shoot(page, 'queue-stage-detail', width, scheme === 'dark' ? '-dark' : '');
      });

      test('status bar meta line', async ({ page }) => {
        await page.getByRole('button', { name: 'Expand status bar' }).click();
        const meta = page.getByTestId('status-job').getByText(/14\.9 \/ 45\.1 min - 20%/);
        await expect(meta).toBeVisible();
        const box = (await meta.boundingBox())!;
        expect(box.x + box.width).toBeLessThanOrEqual(width);
        await noOverflow(page);
        await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
        await page.waitForTimeout(300);
        await shoot(page, 'status-bar-stage-detail', width, scheme === 'dark' ? '-dark' : '');
      });
    });
  }
}
