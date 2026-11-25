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

const held = (start: number, end: number, usableProposal: { start: number; end: number } | null) => ({
  start, end, confidence: 0.9, reason: 'Acme sponsor read', sponsor: 'Acme', detection_stage: 'llm',
  held_for_review: true,
  hold_reason: 'reviewer_contradiction',
  allowedBounds: { min: start - 30, max: end + 30 },
  reviewer_verdict: 'adjust',
  reviewer_proposed_start: usableProposal?.start ?? end + 100,
  reviewer_proposed_end: usableProposal?.end ?? end + 160,
  reviewer_reasoning: 'Reviewer found the ad in a different span.',
  usableProposal,
});

const episode = {
  id: EPISODE_ID,
  title: 'Episode three, held markers',
  published: iso(-86_400_000),
  status: 'completed',
  description: 'Example episode description.',
  duration: 1800,
  originalDuration: 1800,
  newDuration: 1800,
  timeSaved: 0,
  adMarkers: [],
  rejectedAdMarkers: [],
  pendingReviewMarkers: [
    held(120, 180, { start: 130, end: 170 }),
    held(900, 990, { start: 880, end: 990 }),
    held(1400, 1460, null),
  ],
  keptMarkers: [],
  corrections: [],
  processedUrl: `https://example.com/episodes/${EPISODE_ID}.mp3`,
  transcriptAvailable: false,
  hasOriginalAudio: true,
};

async function mockApi(page: Page, detail: unknown) {
  await page.route('**/api/v1/**', async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, '');
    let body: unknown;
    if (p === '/auth/status') body = { passwordSet: true, authenticated: true };
    else if (p === '/feeds') body = { feeds: [feed], lastRefreshCompletedAt: null };
    else if (p === `/feeds/${SLUG}`) body = feed;
    else if (p === `/feeds/${SLUG}/episodes/${EPISODE_ID}`) body = detail;
    if (body === undefined) return route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not mocked"}' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

async function shoot(page: Page, name: string) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false });
}

test('held rows show the usable proposal button', async ({ page }, info) => {
  await mockApi(page, episode);
  await page.goto(`feeds/${SLUG}/episodes/${EPISODE_ID}`);

  const trimmed = page.getByTestId('approve-trimmed-0');
  const adjusted = page.getByTestId('approve-trimmed-1');
  await expect(trimmed).toHaveText(/^Confirm trimmed \(.+\)$/);
  await expect(trimmed).toHaveAttribute('title', 'Cut only the part the reviewer marked as ad.');
  await expect(adjusted).toHaveText(/^Confirm adjusted \(.+\)$/);
  await expect(adjusted).toHaveAttribute('title', "Cut the reviewer's span, including audio past this marker.");
  await expect(page.getByTestId('approve-recut-2')).toBeVisible();
  await expect(page.getByTestId('approve-trimmed-2')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^Confirm (trimmed|adjusted)/ })).toHaveCount(2);

  await trimmed.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const phone = info.project.name.startsWith('phone');
  for (const [name, button] of [['trimmed', trimmed], ['adjusted', adjusted]] as const) {
    const box = (await button.boundingBox())!;
    console.log(`${info.project.name} ${name}: "${(await button.textContent())?.trim()}" ${box.width}x${box.height}px`);
    if (phone) {
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
  }
  if (phone) {
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  }
  await shoot(page, `held-proposal-${info.project.name}`);
});
