import { test, expect, type Page, type Route } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

// Runs once (the widths are set per describe); screenshots are written only when MINUSPOD_SHOTS names a directory.
const SHOTS = process.env.MINUSPOD_SHOTS;
const WIDTHS = [320, 390, 466, 626, 768, 890, 1024, 1280, 1440];
const DARK_WIDTHS = [320, 390, 1440];
const iso = (offsetMs: number) => new Date(Date.now() + offsetMs).toISOString();

const schedule = {
  enabled: true,
  start: '22:00',
  end: '06:00',
  days: ['mon', 'tue', 'wed', 'thu', 'fri'],
  timezone: 'America/New_York',
  open: false,
  nextChangeAt: iso(5 * 3_600_000),
};

const queuedEpisode = {
  episodeId: 'a1b2c3d4e5f6',
  slug: 'example-podcast',
  title: 'The Daily Tech Show, episode one',
  podcast: 'The Daily Tech Show',
  startedAt: null,
  stage: 'queued',
  queuePosition: 1,
  queueTotal: 1,
  priority: 0,
  admission: { blocked: true, phase: null, slot: null, reason: 'outside_schedule', resumesAt: schedule.nextChangeAt },
};

const status = {
  currentJob: null,
  jobs: [],
  queueLength: 1,
  queuedEpisodes: [],
  feedRefreshes: [],
  hold: { queuePaused: false, holdUntil: null, holdSince: null, offlineHeld: 0, offlineServices: [] },
  processingSchedule: { enabled: true, open: false, nextChangeAt: schedule.nextChangeAt },
  lastUpdated: Date.now() / 1000,
};

async function mockApi(page: Page) {
  await page.route('**/api/v1/**', async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, '');
    let body: unknown;
    if (p === '/auth/status') body = { passwordSet: true, authenticated: true };
    else if (p === '/status') body = status;
    else if (p === '/settings') body = { defaults: {} };
    else if (p === '/settings/processing-schedule') body = schedule;
    else if (p === '/settings/offline-queue') body = { enabled: false, ttlHours: 48, deferredCount: 0 };
    else if (p === '/settings/rate-limit-hold') body = { enabled: false, holdUntil: null, llmUsageUrl: '', rateLimitProbeMinutes: 5 };
    else if (p === '/episodes/processing') body = [queuedEpisode];
    else if (p === '/episodes/processing/admission' || p.endsWith('/admission')) body = { paused: false, activeRuns: 0, queuedEpisodes: 1 };
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
    test.describe(`schedule width ${width} ${scheme}`, () => {
      const phone = width < 768;
      test.use({ viewport: { width, height: 900 }, hasTouch: phone, isMobile: phone, colorScheme: scheme });

      test.beforeEach(async ({ page }, info) => {
        test.skip(info.project.name !== 'desktop-1440', 'width is set by the describe, one project is enough');
        await mockApi(page);
        await page.addInitScript((t) => localStorage.setItem('theme', t), scheme);
        await page.goto('queue');
        await page.getByRole('heading', { name: 'Processing schedule' }).waitFor();
      });

      test('schedule block and guard', async ({ page }) => {
        const heading = page.getByRole('heading', { name: 'Processing schedule' });
        const from = page.getByLabel('From');
        const until = page.getByLabel('Until');
        // Heading near the top, clear of the sticky nav and status bar.
        await heading.evaluate((el) => window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 170, behavior: 'instant' }));
        await page.waitForTimeout(300);

        const [fb, ub] = [(await from.boundingBox())!, (await until.boundingBox())!];
        expect(fb.height).toBeGreaterThanOrEqual(phone ? 44 : 40);
        expect(ub.height).toBeGreaterThanOrEqual(phone ? 44 : 40);
        expect(Math.abs(fb.y - ub.y)).toBeLessThan(2);
        expect(ub.x).toBeGreaterThan(fb.x + fb.width - 1);

        const group = page.getByRole('group', { name: 'Days the window starts on' });
        for (const btn of await group.getByRole('button').all()) {
          const box = (await btn.boundingBox())!;
          expect(box.width).toBeGreaterThanOrEqual(44);
          expect(box.height).toBeGreaterThanOrEqual(phone ? 44 : 40);
        }
        if (process.env.MINUSPOD_LOG_BOXES) {
          const dayBoxes = await Promise.all((await group.getByRole('button').all()).map((b) => b.boundingBox()));
          const min = (k: 'width' | 'height') => Math.min(...dayBoxes.map((b) => b![k]));
          console.log(`BOX ${width} ${scheme} from=${fb.width.toFixed(1)}x${fb.height.toFixed(1)}@${fb.x.toFixed(1)},${fb.y.toFixed(1)} until=${ub.width.toFixed(1)}x${ub.height.toFixed(1)}@${ub.x.toFixed(1)},${ub.y.toFixed(1)} dayMin=${min('width').toFixed(1)}x${min('height').toFixed(1)}`);
        }
        await noOverflow(page);
        await shoot(page, 'schedule-block', width, scheme === 'dark' ? '-dark' : '');

        for (const name of ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']) {
          await group.getByRole('button', { name }).click();
        }
        await expect(page.getByRole('alert').filter({ hasText: 'Select at least one day.' })).toBeVisible();
        await expect(group.locator('xpath=..').getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
        await page.getByRole('alert').filter({ hasText: 'Select at least one day.' }).scrollIntoViewIfNeeded();
        await page.waitForTimeout(300);
        await noOverflow(page);
        await shoot(page, 'schedule-guard', width, scheme === 'dark' ? '-dark' : '');
      });

      test('queue hold box', async ({ page }) => {
        const hold = page.getByText('Queue hold');
        await expect(hold).toBeVisible();
        await expect(page.getByText(/Processing is outside its schedule/)).toBeVisible();
        await hold.scrollIntoViewIfNeeded();
        await page.waitForTimeout(300);
        await noOverflow(page);
        await shoot(page, 'queue-hold', width, scheme === 'dark' ? '-dark' : '');
      });
    });
  }
}
