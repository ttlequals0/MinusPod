import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Locator, type Page, type Route } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

test.skip(!process.env.MINUSPOD_VISUAL, 'visual sweep runs on demand');

const SHOTS = path.resolve('..', '.superpowers', 'screenshots', 'youtube');
const WIDTHS = [320, 390, 466, 626, 768, 890, 1024, 1280, 1440];
const SLUG = 'example-youtube';
const EPISODE_ID = 'a1b2c3d4e5f6';
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
    boolean: false,
    string: '',
    number: 1,
    'string | null': null,
    WhisperBackend: 'local',
    LlmProvider: 'anthropic',
  };
  const out: Record<string, unknown> = {};
  for (const m of body.matchAll(/^ {2}(\w+): (.+?);/gm)) if (m[2] in fill) out[m[1]] = fill[m[2]];
  Object.assign(out, {
    segmentCategoryActions: { value: {}, isDefault: true },
    jitBlockedUserAgents: { value: [], isDefault: true },
    modelPricingOverrides: { value: {}, isDefault: true },
    defaults: {},
    youtubeEnabled: { value: true, isDefault: false },
    youtubeApiKeyConfigured: true,
    podcastSearchReady: true,
    podcastSearchProvider: { value: 'podcastindex', isDefault: true },
  });
  return out;
}
const settings = settingsFixture();

const youtubeSettings = {
  enabled: true,
  apiKeyConfigured: true,
  processDelayMinutes: 0,
  sbProbeAttempts: 6,
  sbProbeIntervalMinutes: 30,
  sbWatchIntervalHours: 12,
  sbWatchMaxDays: 7,
  llmFallback: true,
  captionsAsTranscript: false,
  hidePrivate: true,
  transcribeFallback: false,
  proxyUrl: 'socks5h://proxy:1080',
  ytdlpExtraArgs: '--limit-rate 2M',
  ytdlp: {
    autoUpdate: true,
    version: '2026.10.15',
    lastUpdateAt: iso(-86_400_000),
    lastCheckAt: iso(-3_600_000),
    lastError: null,
    runtimePath: '/data/ytdlp/yt-dlp-2026.10.15-linux-x86_64-with-a-very-long-runtime-path/bin/yt-dlp',
    bakedVersion: '2026.09.01',
  },
  cookies: { set: true, lines: 42, updatedAt: iso(-172_800_000) },
  bounds: {
    processDelayMinutes: [0, 1440],
    sbProbeAttempts: [1, 48],
    sbProbeIntervalMinutes: [5, 720],
    sbWatchIntervalHours: [1, 168],
    sbWatchMaxDays: [1, 90],
  },
};

const baseFeed = { episodeCount: 3, processedCount: 1, lastRefreshed: iso(-3_600_000), createdAt: iso(-86_400_000 * 9) };
const youtubeFeed = {
  ...baseFeed,
  slug: SLUG,
  title: 'The Daily Tech Show',
  sourceUrl: 'https://www.youtube.com/playlist?list=PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222',
  feedUrl: `https://example.com/feeds/${SLUG}`,
  feedType: 'youtube',
  youtubeSourceKind: 'playlist',
  youtubeSourceId: 'PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222',
  youtubeSourceUrl: 'https://www.youtube.com/playlist?list=PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222',
  youtubeIncludeShorts: null,
  ytProcessDelayMinutesOverride: null,
  ytProbeAttemptsOverride: null,
  ytProbeIntervalMinutesOverride: null,
  ytLlmFallbackOverride: null,
  ytLlmOnTopOverride: true,
  ytCaptionsOverride: null,
  ytHidePrivateOverride: null,
  ytTranscribeOverride: null,
  description: 'Example description.',
};
const feeds = [
  youtubeFeed,
  { ...baseFeed, slug: 'example-podcast', title: 'Example Podcast', sourceUrl: 'https://example.com/rss', feedUrl: 'https://example.com/feeds/example-podcast', feedType: 'subscribed' },
  { ...baseFeed, slug: 'example-local', title: 'Example Local Archive', sourceUrl: '', feedUrl: 'https://example.com/feeds/example-local', feedType: 'local' },
];

const sbEpisodes = [
  { id: 'b1b2c3d4e5f6', title: 'Episode one, still probing', published: iso(-3_600_000), status: 'pending', sponsorBlock: { state: 'probing', attempts: 2, nextCheckAt: iso(25 * 60_000), segmentCount: 0, note: null, lastError: null } },
  { id: EPISODE_ID, title: 'Episode two, SponsorBlock done', published: iso(-86_400_000), status: 'completed', ad_count: 2, processedAt: iso(-80_000_000), sponsorBlock: { state: 'done', attempts: 3, nextCheckAt: null, segmentCount: 4, note: null, lastError: null } },
  { id: 'c1b2c3d4e5f6', title: 'Episode three, SponsorBlock timed out', published: iso(-172_800_000), status: 'completed', ad_count: 1, processedAt: iso(-170_000_000), sponsorBlock: { state: 'exhausted', attempts: 6, nextCheckAt: null, segmentCount: 0, note: null, lastError: null } },
];
const marker = (start: number, end: number) => ({ start, end, confidence: 1, reason: 'SponsorBlock sponsor segment', sponsor: 'Acme', detection_stage: 'sponsorblock', validation: { decision: 'ACCEPT', adjusted_start: start, adjusted_end: end, adjustments: [], confidence: 1, flags: [], original_start: start, original_end: end } });
const episodeDetail = {
  ...sbEpisodes[1],
  description: 'Example episode description.',
  duration: 1800,
  originalDuration: 1800,
  newDuration: 1650,
  timeSaved: 150,
  adMarkers: [marker(120, 180), marker(900, 990)],
  rejectedAdMarkers: [],
  pendingReviewMarkers: [],
  keptMarkers: [],
  corrections: [],
  processedUrl: `https://example.com/episodes/${EPISODE_ID}.mp3`,
  transcriptAvailable: false,
};

const processingTimeouts = {
  softTimeoutSeconds: 900,
  hardTimeoutSeconds: 3600,
  defaults: { softTimeoutSeconds: 900, hardTimeoutSeconds: 3600 },
  limits: { softMin: 60, hardMax: 86400 },
};

const preview = {
  enabled: true,
  source: { kind: 'channel', id: 'UCabcdefghijklmnopqrstuv', title: 'The Daily Tech Show', description: 'Example description of a channel that covers technology news, reviews and interviews. '.repeat(3), artworkUrl: null, videoCount: 412, alreadyAdded: null },
  rssMatches: [
    { title: 'The Daily Tech Show (audio)', feedUrl: 'https://example.com/rss/daily-tech', artworkUrl: '' },
    { title: 'The Daily Tech Show Extra', feedUrl: 'https://example.com/rss/daily-tech-extra', artworkUrl: '' },
  ],
};

async function mockApi(page: Page) {
  await page.route('**/api/v1/**', async (route: Route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname.replace(/^.*\/api\/v1/, '');
    const m = req.method();
    let body: unknown;
    if (p === '/auth/status') body = { passwordSet: true, authenticated: true };
    else if (p === '/settings' && m === 'GET') body = settings;
    else if (p === '/settings/youtube') body = youtubeSettings;
    else if (p === '/settings/processing-timeouts') body = processingTimeouts;
    else if (p === '/settings/whisper-models') body = { models: [] };
    else if (p === '/feeds') body = { feeds, lastRefreshCompletedAt: null };
    else if (p === `/feeds/${SLUG}`) body = youtubeFeed;
    else if (p === `/feeds/${SLUG}/episodes`) body = { episodes: sbEpisodes, total: 3, limit: 50, offset: 0 };
    else if (p === `/feeds/${SLUG}/episodes/${EPISODE_ID}`) body = episodeDetail;
    else if (p === '/youtube/preview') body = preview;
    // Unmocked endpoints fail like an unavailable backend; the pages under test do not depend on them.
    if (body === undefined) return route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not mocked"}' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

async function routeEpisode(page: Page, extra: Record<string, unknown>) {
  await page.route(`**/api/v1/feeds/${SLUG}/episodes/${EPISODE_ID}`, (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ ...episodeDetail, ...extra }),
  }));
}

async function seedOpen(page: Page) {
  await page.addInitScript(() => { localStorage.setItem('settings-section-youtube', 'true'); localStorage.setItem('youtube-feed-example-youtube', 'true'); });
}

async function expectNoOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

async function expectTouchTargets(page: Page, scope: ReturnType<Page['locator']>, width: number) {
  if (width >= 768) return;
  const failures: string[] = [];
  for (const el of await scope.locator('button:visible, [role=switch]:visible').all()) {
    // The collapsible header button sits inside a taller padded click row.
    if (await el.getAttribute('aria-expanded') !== null) continue;
    const box = await el.boundingBox();
    if (box && box.height < 44) failures.push(`${(await el.innerText()).trim() || (await el.getAttribute('aria-label'))}: ${box.height}px`);
  }
  expect(failures).toEqual([]);
}

// Episode-list rules the page already fails before this feature; the new panel is also scanned alone.
const EPISODE_LIST_RULES = ['label', 'nested-interactive', 'select-name'];

async function expectAxe(page: Page, opts: { include?: string; disableRules?: string[] } = {}) {
  let builder = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).disableRules(opts.disableRules ?? []);
  if (opts.include) builder = builder.include(opts.include);
  const scan = await builder.analyze();
  const bad = scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  expect(bad.map((v) => ({ id: v.id, targets: v.nodes.map((n) => n.target) }))).toEqual([]);
}

async function shoot(page: Page, slug: string, width: number) {
  await page.screenshot({ path: path.join(SHOTS, `${slug}-${width}.png`), fullPage: false });
  if (width === 390 || width === 1280) {
    await page.screenshot({ path: path.join(SHOTS, `${slug}-${width}-full.png`), fullPage: true });
  }
}

async function measureAndShoot(page: Page, target: Locator, name: string, width: number) {
  await target.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  const box = (await target.boundingBox())!;
  if (width < 768) expect(box.height).toBeGreaterThanOrEqual(44);
  await shoot(page, name, width);
}

for (const width of WIDTHS) {
  test.describe(`width ${width}`, () => {
    test.use({ viewport: { width, height: 900 }, hasTouch: width < 768, isMobile: width < 768 });

    test.beforeEach(async ({ page }) => {
      await mockApi(page);
      await seedOpen(page);
    });

    test('settings youtube section', async ({ page }) => {
      await page.goto('settings#youtube');
      const section = page.locator('[data-search-key="settings-section-youtube"]');
      await expect(section.getByRole('button', { name: 'Save cookies' })).toBeVisible({ timeout: 15_000 });
      await section.scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      expect(page.viewportSize()!.width).toBe(width);
      await expectNoOverflow(page);
      await expectTouchTargets(page, section, width);
      await expectAxe(page);
      await shoot(page, 'settings-youtube', width);
    });

    test('settings ytdlp extra arguments', async ({ page }) => {
      await page.goto('settings#youtube');
      const input = page.getByLabel('Extra yt-dlp arguments');
      await expect(input).toBeVisible({ timeout: 15_000 });
      await page.getByRole('button', { name: 'Update now' }).scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      const proxy = page.getByLabel('Proxy URL');
      await expect(proxy).toBeVisible();
      const box = (await input.boundingBox())!;
      const proxyBox = (await proxy.boundingBox())!;
      if (width < 768) {
        expect(box.height).toBeGreaterThanOrEqual(44);
        expect(proxyBox.height).toBeGreaterThanOrEqual(44);
      }
      await expectNoOverflow(page);
      if (width === 390 || width === 1024) {
        await page.screenshot({ path: path.join(SHOTS, `ytdlp-args-${width}.png`), fullPage: false });
      }
    });

    test('settings youtube reset button', async ({ page }) => {
      await page.goto('settings#youtube');
      const reset = page.getByRole('button', { name: 'Reset to defaults' });
      await expect(reset).toBeVisible({ timeout: 15_000 });
      await reset.scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      const box = (await reset.boundingBox())!;
      if (width < 768) expect(box.height).toBeGreaterThanOrEqual(44);
      await expectNoOverflow(page);
      if (width === 390 || width === 1024) {
        await page.screenshot({ path: path.join(SHOTS, `youtube-reset-${width}.png`), fullPage: false });
      }
    });

    test('hide private settings toggle', async ({ page }) => {
      await page.goto('settings#youtube');
      const toggle = page.getByRole('switch', { name: 'Hide private and deleted episodes' });
      await expect(toggle).toBeVisible({ timeout: 15_000 });
      await measureAndShoot(page, toggle, 'hide-private-settings', width);
    });

    test('hide private feed override', async ({ page }) => {
      await page.goto(`feeds/${SLUG}`);
      const select = page.getByLabel('Hide private and deleted episodes');
      await expect(select).toBeVisible({ timeout: 15_000 });
      await measureAndShoot(page, select, 'hide-private-feed', width);
    });

    test('add feed youtube lookup card', async ({ page }) => {
      await page.goto('add');
      await page.locator('#podcastInput').fill('https://www.youtube.com/@exampletech');
      await page.getByRole('button', { name: 'Look up', exact: true }).click();
      const card = page.locator('div.border.rounded-lg').filter({ has: page.getByRole('heading', { name: 'The Daily Tech Show' }) });
      await expect(card).toBeVisible();
      await expect(page.getByRole('button', { name: 'Add YouTube feed' })).toBeVisible();
      await card.scrollIntoViewIfNeeded();
      expect(page.viewportSize()!.width).toBe(width);
      await expectNoOverflow(page);
      await expectTouchTargets(page, card, width);
      await expectAxe(page);
      await shoot(page, 'add', width);
    });

    test('add feed youtube video card', async ({ page }) => {
      await page.route('**/api/v1/youtube/preview', (route) => route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          enabled: true, rssMatches: [],
          source: { kind: 'video', id: 'abc123def45', title: 'One Example Video', description: 'One video served as a one-episode feed.', author: 'Example Channel', artworkUrl: null, videoCount: 1, alreadyAdded: null },
        }),
      }));
      await page.goto('add');
      await page.locator('#podcastInput').fill('https://youtu.be/abc123def45');
      await page.getByRole('button', { name: 'Look up', exact: true }).click();
      const card = page.locator('div.border.rounded-lg').filter({ has: page.getByRole('heading', { name: 'One Example Video' }) });
      await expect(card).toBeVisible();
      await expect(card.getByText('Video', { exact: true })).toBeVisible();
      await card.scrollIntoViewIfNeeded();
      expect(page.viewportSize()!.width).toBe(width);
      await expectNoOverflow(page);
      await expectTouchTargets(page, card, width);
      await expectAxe(page);
      await shoot(page, 'add-video', width);
    });

    test('feed detail youtube panel', async ({ page }) => {
      await page.goto(`feeds/${SLUG}`);
      const panel = page.locator(`[data-search-key="youtube-feed-${SLUG}"]`);
      await expect(panel.getByRole('heading', { name: 'YouTube' })).toBeVisible({ timeout: 15_000 });
      await expect(page.getByText(/Waiting for SponsorBlock, attempt 2/)).toBeVisible();
      const actionRow = page.getByRole('button', { name: 'Delete feed' }).locator('xpath=../..');
      await panel.scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      expect(page.viewportSize()!.width).toBe(width);
      await expectNoOverflow(page);
      await expectTouchTargets(page, panel, width);
      await expectAxe(page, { include: `[data-search-key="youtube-feed-${SLUG}"]` });
      await expectAxe(page, { disableRules: EPISODE_LIST_RULES });
      await actionRow.scrollIntoViewIfNeeded();
      await expectTouchTargets(page, actionRow, width);
      await shoot(page, 'feed-detail', width);
    });

    test('episode detail sponsorblock state', async ({ page }) => {
      await page.goto(`feeds/${SLUG}/episodes/${EPISODE_ID}`);
      await expect(page.getByText(/SponsorBlock: 4 segments/).first()).toBeVisible({ timeout: 15_000 });
      expect(page.viewportSize()!.width).toBe(width);
      await expectNoOverflow(page);
      await expectAxe(page);
      await shoot(page, 'episode-detail', width);
    });

    test('episode detail youtube reprocess menu', async ({ page }) => {
      test.skip(width !== 390 && width !== 768, 'menu check runs at 390 and 768');
      await routeEpisode(page, { transcriptAvailable: true });
      await page.goto(`feeds/${SLUG}/episodes/${EPISODE_ID}`);
      await expect(page.getByText(/SponsorBlock: 4 segments/).first()).toBeVisible({ timeout: 15_000 });
      await page.getByRole('button', { name: 'Reprocess' }).click();
      for (const subtitle of ['SponsorBlock marks + patterns', 'Marks, patterns + pass 1 LLM', 'Keep transcript, add pass 1 LLM']) {
        await expect(page.getByRole('menuitem').getByText(subtitle)).toBeVisible();
      }
      await page.waitForTimeout(400);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await shoot(page, 'episode-detail-youtube-menu', width);
    });
  });
}

const sponsorBlockRun = {
  runNumber: 1,
  processedAt: sbEpisodes[1].processedAt,
  status: 'completed',
  adsDetected: 2,
  processingDurationSeconds: 48,
  errorMessage: null,
  inputTokens: 0,
  outputTokens: 0,
  llmCost: 0,
  stats: {
    mode: 'auto',
    verificationSkipped: true,
    audioAnalysisSkipped: true,
    downloadedDuration: 1800,
    transcriptSegments: 0,
    timings: {
      downloadSeconds: 31,
      sponsorblockSeconds: 1.4,
      transcriptionSeconds: null,
      audioAnalysisSeconds: null,
      detectionSeconds: 2,
      cutSeconds: 9,
      chaptersSeconds: 0.6,
      finalizeSeconds: 1,
      ffmpegSeconds: 11,
    },
  },
};
