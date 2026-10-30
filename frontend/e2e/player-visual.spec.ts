import { test, expect, type Page, type Route } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

test.skip(!process.env.MINUSPOD_VISUAL, 'visual sweep runs on demand');

const SHOTS = path.resolve('..', '.superpowers', 'screenshots', 'player');
const WIDTHS = [320, 390, 768, 1024];
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
const longDescription = '<p>' + 'An example episode description that runs long enough to need collapsing on a phone. '.repeat(12) + '</p>';
const episode = {
  id: EPISODE_ID, title: 'An example episode with a reasonably long title', published: iso(-86_400_000), status: 'completed',
  ad_count: 0, processedAt: iso(-80_000_000), description: longDescription,
  duration: DURATION_S, originalDuration: DURATION_S, newDuration: DURATION_S, timeSaved: 0,
  adMarkers: [], rejectedAdMarkers: [], pendingReviewMarkers: [], keptMarkers: [], corrections: [],
  processedUrl: `/episodes/${SLUG}/${EPISODE_ID}.mp3`,
  chaptersAvailable: true, chaptersUrl: `/episodes/${SLUG}/${EPISODE_ID}/chapters.json`,
  transcriptAvailable: false,
};
const chapters = {
  version: '1.2.0',
  chapters: [
    { startTime: 0, title: 'Intro' },
    { startTime: 420, title: 'A long chapter title that has to truncate on a narrow phone screen' },
    { startTime: 1500, title: 'Interview' },
    { startTime: 2700, title: 'Wrap up' },
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
    // Honour Range so the element can seek.
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

for (const width of WIDTHS) {
  test.describe(`player width ${width}`, () => {
    test.use({ viewport: { width, height: 900 }, hasTouch: width < 768, isMobile: width < 768 });

    test('episode player', async ({ page }) => {
      await mockApi(page);
      await page.goto(`feeds/${SLUG}/episodes/${EPISODE_ID}`);
      const player = page.getByRole('group', { name: 'Episode player' });
      await expect(player).toBeVisible({ timeout: 15_000 });
      await expect(player.getByText('Intro')).toBeVisible();
      await expect(player.getByText('-1:00:00')).toBeVisible();
      await player.getByRole('button', { name: 'Next chapter' }).click();
      await expect(player.getByText('A long chapter title', { exact: false })).toBeVisible();
      await page.mouse.move(0, 0);
      await player.scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);

      // Header rows stay on one line: row height equals one control, no page scroll.
      const rows = [
        page.locator('div.flex-nowrap').filter({ hasText: 'Chapters' }).filter({ hasNot: page.getByRole('button') }).first(),
        page.locator('div.flex-nowrap').filter({ has: page.getByRole('button', { name: 'Reprocess' }) }).first(),
      ];
      const firstControls = [page.getByText('Completed', { exact: true }), page.getByRole('button', { name: 'Reprocess' })];
      for (const [i, row] of rows.entries()) {
        const rowBox = (await row.boundingBox())!;
        const controlBox = (await firstControls[i].boundingBox())!;
        expect(await row.evaluate((el) => el.scrollHeight)).toBeLessThanOrEqual(Math.ceil(controlBox.height) + 1);
        expect(rowBox.height).toBeLessThanOrEqual(Math.ceil(controlBox.height) + 1);
        const fit = await row.evaluate((el) => [el.scrollWidth, el.clientWidth, el.scrollHeight]);
        console.log(`header row ${i} ${width}`, fit.join('/'), controlBox.height);
        expect(fit[0]).toBeLessThanOrEqual(fit[1]);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      const measured: Record<string, string> = {};
      const buttons: Array<[string | RegExp, number]> = [
        ['Play', 56], ['Previous chapter', 44], ['Next chapter', 44],
        ['Back 15 seconds', 44], ['Forward 30 seconds', 44], [/Playback speed/, 44],
      ];
      for (const [name, min] of buttons) {
        const box = (await player.getByRole('button', { name, exact: typeof name === 'string' }).boundingBox())!;
        measured[String(name)] = `${box.width}x${box.height}`;
        // Desktop sizes the secondary buttons by recipe; the floor applies at phone widths.
        if (min === 56 || width < 768) {
          expect(box.height).toBeGreaterThanOrEqual(min);
          expect(box.width).toBeGreaterThanOrEqual(min);
        }
      }
      console.log(`player ${width}`, JSON.stringify(measured));
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

      const toggle = page.getByRole('button', { name: 'Show more' });
      await expect(toggle).toBeVisible();
      expect(await toggle.getAttribute('aria-expanded')).toBe('false');
      if (width < 768) expect((await toggle.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      await page.screenshot({ path: path.join(SHOTS, `player-${width}.png`), fullPage: false });
      await toggle.click();
      await expect(page.getByRole('button', { name: 'Show less' })).toHaveAttribute('aria-expanded', 'true');
      await page.waitForTimeout(200);
      await page.screenshot({ path: path.join(SHOTS, `player-${width}-expanded.png`), fullPage: false });
    });
  });
}
