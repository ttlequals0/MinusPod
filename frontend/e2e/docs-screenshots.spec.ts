import { test, expect, type Page, type Route } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

test.skip(!process.env.MINUSPOD_DOC_SHOTS, 'doc screenshots run on demand');

const OUT = path.resolve('..', 'docs', 'screenshots');
const SLUG = 'example-podcast';
const EPISODE_ID = 'a1b2c3d4e5f6';
const DURATION_S = 3600;
const now = Date.now();
const iso = (offsetMs: number) => new Date(now + offsetMs).toISOString();

// Fills every required Settings key from its declared type so the real page renders.
function settingsFixture(): Record<string, unknown> {
  const src = readFileSync(path.resolve('src/api/types.ts'), 'utf8');
  const body = src.slice(src.indexOf('export interface Settings {')).split('\n}\n')[0];
  const fill: Record<string, unknown> = {
    SettingValue: { value: '', isDefault: true },
    SettingValueNumber: { value: 1, isDefault: true },
    SettingValueBoolean: { value: false, isDefault: true },
    SettingValueNumberOrNull: { value: null, isDefault: true },
    boolean: false, string: '', number: 1, 'string | null': null,
    WhisperBackend: 'local', LlmProvider: 'anthropic',
  };
  const out: Record<string, unknown> = {};
  for (const m of body.matchAll(/^ {2}(\w+): (.+?);/gm)) if (m[2] in fill) out[m[1]] = fill[m[2]];
  Object.assign(out, {
    segmentCategoryActions: { value: {}, isDefault: true },
    jitBlockedUserAgents: { value: [], isDefault: true },
    modelPricingOverrides: { value: {}, isDefault: true },
    defaults: {},
  });
  return out;
}
const settings = settingsFixture();

const feed = {
  slug: SLUG, title: 'Example Podcast', sourceUrl: 'https://example.com/rss', feedUrl: `https://example.com/feeds/${SLUG}`,
  feedType: 'subscribed', episodeCount: 1, processedCount: 1, lastRefreshed: iso(-3_600_000), createdAt: iso(-86_400_000 * 9),
};
const description = '<p>In this example episode the hosts walk through the main topic and explain why it matters. They answer a few listener questions near the end. Links to everything mentioned are collected in the show notes below, along with a short reading list for anyone who wants to go deeper on the subject. The hosts also recap the main points from the previous episode, thank the listeners who sent in feedback, and preview what is coming next week, including a guest interview and a deeper look at a few of the questions that did not fit into this one. Chapter markers are included so you can jump straight to the part you care about, and a full transcript is available for anyone who prefers to read along while they listen. Thanks for listening, and see you next time.</p>';
const episode = {
  id: EPISODE_ID, title: 'An example episode', published: iso(-86_400_000), status: 'completed',
  ad_count: 0, processedAt: iso(-80_000_000), description,
  duration: DURATION_S, originalDuration: DURATION_S, newDuration: DURATION_S, timeSaved: 0,
  adMarkers: [], rejectedAdMarkers: [], pendingReviewMarkers: [], keptMarkers: [], corrections: [],
  processedUrl: `/episodes/${SLUG}/${EPISODE_ID}.mp3`,
  chaptersAvailable: true, chaptersUrl: `/episodes/${SLUG}/${EPISODE_ID}/chapters.json`,
  transcriptAvailable: false,
};
const chapters = {
  version: '1.2.0',
  chapters: [
    { startTime: 0, title: 'Introduction' },
    { startTime: 420, title: 'Main topic' },
    { startTime: 2400, title: 'Listener questions' },
  ],
};

// Silent mono 8 kHz 8-bit WAV so the audio element reports a real duration.
function silentWav(seconds: number): Buffer {
  const rate = 8000;
  const data = Buffer.alloc(seconds * rate, 128);
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + data.length, 4); h.write('WAVEfmt ', 8);
  h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22);
  h.writeUInt32LE(rate, 24); h.writeUInt32LE(rate, 28); h.writeUInt16LE(1, 32); h.writeUInt16LE(8, 34);
  h.write('data', 36); h.writeUInt32LE(data.length, 40);
  return Buffer.concat([h, data]);
}
const wav = silentWav(DURATION_S);

async function mockApi(page: Page) {
  await page.route(`**/episodes/${SLUG}/${EPISODE_ID}.mp3`, (route) => {
    const m = /bytes=(\d+)-(\d*)/.exec(route.request().headers().range ?? '');
    if (!m) return route.fulfill({ contentType: 'audio/wav', headers: { 'accept-ranges': 'bytes' }, body: wav });
    const start = Number(m[1]);
    const end = m[2] ? Number(m[2]) : wav.length - 1;
    return route.fulfill({
      status: 206,
      contentType: 'audio/wav',
      headers: { 'accept-ranges': 'bytes', 'content-range': `bytes ${start}-${end}/${wav.length}` },
      body: wav.subarray(start, end + 1),
    });
  });
  await page.route('**/chapters.json', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(chapters) }));
  await page.route('**/api/v1/**', async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, '');
    let body: unknown;
    if (p === '/auth/status') body = { passwordSet: true, authenticated: true };
    else if (p === '/settings') body = settings;
    else if (p === '/feeds') body = { feeds: [feed], lastRefreshCompletedAt: null };
    else if (p === `/feeds/${SLUG}`) body = feed;
    else if (p === `/feeds/${SLUG}/episodes`) body = { episodes: [episode], total: 1, limit: 50, offset: 0 };
    else if (p === `/feeds/${SLUG}/episodes/${EPISODE_ID}`) body = episode;
    if (body === undefined) return route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not mocked"}' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

const SHOTS = [
  { name: 'desktop', width: 1280, height: 900, mobile: false },
  { name: 'mobile', width: 390, height: 844, mobile: true },
];

for (const s of SHOTS) {
  test.describe(`doc screenshot ${s.name}`, () => {
    test.use({ viewport: { width: s.width, height: s.height }, hasTouch: s.mobile, isMobile: s.mobile, colorScheme: 'dark' });

    test('episode detail', async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem('theme', 'dark'));
      await mockApi(page);
      await page.goto(`feeds/${SLUG}/episodes/${EPISODE_ID}`);
      const player = page.getByRole('group', { name: 'Episode player' });
      await expect(player).toBeVisible({ timeout: 15_000 });
      await expect(player.getByText('Introduction')).toBeVisible();
      await expect(page.getByRole('button', { name: 'Show more' })).toBeVisible();
      await player.getByRole('button', { name: 'Play', exact: true }).click();
      await expect(player.getByRole('button', { name: 'Pause' })).toBeVisible();
      await player.getByRole('button', { name: 'Next chapter' }).click();
      await expect(player.getByText('Main topic')).toBeVisible();
      await expect(player.getByText(/^7:0\d$/)).toBeVisible();
      await page.mouse.move(0, 0);
      await page.waitForTimeout(400);
      await page.screenshot({ path: path.join(OUT, `episode-detail-${s.name}.png`), fullPage: false });
    });
  });
}
