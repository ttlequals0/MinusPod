import { test, expect, type Page, type Route } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

// Screenshots are written only when MINUSPOD_SHOTS names a directory.
const SHOTS = process.env.MINUSPOD_SHOTS;
const SLUG = 'example-podcast';
const EPISODE_ID = 'a1b2c3d4e5f6';
const iso = (offsetMs: number) => new Date(Date.now() + offsetMs).toISOString();

const feed = {
  slug: SLUG,
  title: 'The Daily Tech Show',
  sourceUrl: 'https://www.youtube.com/playlist?list=PLexample',
  feedUrl: `https://example.com/feeds/${SLUG}`,
  feedType: 'youtube',
  episodeCount: 1,
  processedCount: 1,
  lastRefreshed: iso(-3_600_000),
  createdAt: iso(-86_400_000),
};

const marker = (start: number, end: number) => ({
  start, end, confidence: 1, reason: 'Acme sponsor read', sponsor: 'Acme', detection_stage: 'llm',
  validation: { decision: 'ACCEPT', adjusted_start: start, adjusted_end: end, adjustments: [], confidence: 1, flags: [], original_start: start, original_end: end },
});

const episode = {
  id: EPISODE_ID,
  title: 'Episode two, SponsorBlock done',
  published: iso(-86_400_000),
  status: 'completed',
  description: 'Example episode description.',
  duration: 1800,
  originalDuration: 1800,
  newDuration: 1650,
  timeSaved: 150,
  adMarkers: [marker(120, 180), marker(900, 990), marker(1400, 1460)],
  rejectedAdMarkers: [],
  pendingReviewMarkers: [],
  keptMarkers: [],
  corrections: [],
  processedUrl: `https://example.com/episodes/${EPISODE_ID}.mp3`,
  transcriptAvailable: false,
  hasOriginalAudio: true,
  sponsorBlock: {
    state: 'done', attempts: 3, nextCheckAt: null, segmentCount: 0, note: null, lastError: null,
    submissions: [], submitAllowed: false, submitBlockReason: 'disabled',
  },
};

const seg = (start: number, end: number) => ({
  start, end, category: 'sponsor', sbCategory: 'sponsor', evidence: ['Acme sponsor read'], submission: null,
});
const candidates = { allowed: true, reason: null, segments: [seg(120, 180), seg(900, 990), seg(1400, 1460)] };
const at = iso(0);
const submitResult = {
  results: [
    { start: 120, end: 180, category: 'sponsor', status: 'submitted', at, uuid: 'u1' },
    { start: 900, end: 990, category: 'sponsor', status: 'rejected', at, detail: 'Segment too short' },
    { start: 1400, end: 1460, category: 'sponsor', status: 'duplicate', at },
  ],
};

async function mockApi(page: Page, detail: unknown) {
  await page.route('**/api/v1/**', async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, '');
    let body: unknown;
    if (p === '/auth/status') body = { passwordSet: true, authenticated: true };
    else if (p === '/feeds') body = { feeds: [feed], lastRefreshCompletedAt: null };
    else if (p === `/feeds/${SLUG}`) body = feed;
    else if (p === `/feeds/${SLUG}/episodes/${EPISODE_ID}`) body = detail;
    else if (p === `/feeds/${SLUG}/episodes/${EPISODE_ID}/sponsorblock/candidates`) body = candidates;
    else if (p === `/feeds/${SLUG}/episodes/${EPISODE_ID}/sponsorblock/submit`) body = submitResult;
    if (body === undefined) return route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not mocked"}' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

async function shoot(page: Page, name: string) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false });
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
}

test.describe('sponsorblock submit outcomes', () => {
  test('disabled button shows its reason', async ({ page }, info) => {
    await mockApi(page, episode);
    await page.goto(`feeds/${SLUG}/episodes/${EPISODE_ID}`);
    const button = page.getByRole('button', { name: 'Submit to SponsorBlock' });
    await expect(button).toBeDisabled();
    const reason = page.getByText(/SponsorBlock submissions are turned off/);
    await expect(reason).toBeVisible();
    await button.scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollBy(0, 250));
    await page.waitForTimeout(300);
    const box = (await button.boundingBox())!;
    console.log(`disabled button height ${info.project.name}: ${box.height}px`);
    if (info.project.name.startsWith('phone')) {
      expect(box.height).toBeGreaterThanOrEqual(44);
      await noOverflow(page);
    }
    await shoot(page, `sb-disabled-${info.project.name}`);
  });

  test('dialog shows per-segment outcomes', async ({ page }, info) => {
    await mockApi(page, { ...episode, sponsorBlock: { ...episode.sponsorBlock, submitAllowed: true, submitBlockReason: null } });
    await page.goto(`feeds/${SLUG}/episodes/${EPISODE_ID}`);
    const button = page.getByRole('button', { name: 'Submit to SponsorBlock' });
    await expect(button).toBeEnabled();
    await button.click();
    const dialog = page.getByRole('dialog');
    const boxes = dialog.getByRole('checkbox');
    await expect(boxes).toHaveCount(3);
    for (const evidence of await dialog.getByText('Acme sponsor read').all()) await evidence.click();
    await dialog.getByRole('button', { name: 'Submit 3 segments' }).click();
    await expect(dialog.getByText('Rejected: Segment too short')).toBeVisible();
    await expect(dialog.getByText('Already on SponsorBlock')).toBeVisible();
    await expect(dialog.getByText('Submitted', { exact: true })).toBeVisible();
    await page.waitForTimeout(300);
    if (info.project.name.startsWith('phone')) await noOverflow(page);
    await shoot(page, `sb-dialog-${info.project.name}`);
  });
});
