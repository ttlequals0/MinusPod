import { mkdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import { argValue } from './lib/args.mjs';
import { writeAtomic } from './lib/store.mjs';

const SETTLE_MS = 2500;
const SECTION_SETTLE_MS = 300;
const OPEN_EDITOR = '[data-testid^="open-editor-"]';

// feeds: [{ slug, feedType, episodes: [id] }]. Recents feeds get no per-episode routes.
export function expandRoutes(routes, feeds) {
  const out = [...routes.static];
  for (const f of feeds) {
    const slug = encodeURIComponent(f.slug);
    for (const r of routes.perFeed) out.push(r.replace('{slug}', slug));
    if (f.feedType === 'recents') continue;
    for (const id of (f.episodes ?? []).slice(0, routes.episodesPerFeed)) {
      for (const r of routes.perEpisode) out.push(r.replace('{slug}', slug).replace('{id}', encodeURIComponent(id)));
    }
  }
  return out;
}

async function getJson(base, path) {
  const url = `${base}/api/v1${path}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

async function listFeeds(base, perFeed) {
  const { feeds } = await getJson(base, '/feeds');
  const out = [];
  for (const f of feeds) {
    let episodes = [];
    if (f.feedType !== 'recents' && perFeed > 0) {
      const q = `/feeds/${encodeURIComponent(f.slug)}/episodes?limit=${perFeed}&offset=0&status=all`;
      episodes = ((await getJson(base, q)).episodes ?? []).map((e) => e.id);
    }
    out.push({ slug: f.slug, feedType: f.feedType, episodes });
  }
  return out;
}

const MAX_DISCLOSURE_PASSES = 4;
const MAX_CLICKS_PER_PASS = 80;
// Menu triggers (Download, Reprocess) carry aria-haspopup and are not disclosures.
export const CLOSED = 'main button[aria-expanded="false"]:not([aria-haspopup]), main summary';
const DIALOG = '[role=dialog][aria-modal="true"]';
const CLOSE_BUTTON = 'button[aria-label="Close"], button:text-is("Close")';
const MAX_DIALOG_ROUNDS = 3;

// A throttled or failing response means the crawl cannot produce trustworthy fixtures.
export const isCrawlError = (status) => status >= 500 || status === 429;

async function dialogHeading(dialog) {
  const heading = await dialog.locator('h1, h2, h3').first().textContent({ timeout: 1000 }).catch(() => null);
  if (heading?.trim()) return heading.trim();
  return (await dialog.getAttribute('aria-labelledby', { timeout: 1000 }).catch(() => null)) ?? '';
}

// Modals (What's new) set inert on everything else, so no click lands until they close.
export async function dismissDialogs(page) {
  const headings = [];
  for (let round = 0; round < MAX_DIALOG_ROUNDS; round++) {
    const dialog = page.locator(DIALOG).first();
    if (!(await dialog.isVisible())) break;
    headings.push(await dialogHeading(dialog));
    const close = dialog.locator(CLOSE_BUTTON).first();
    if (await close.count()) await close.click({ timeout: 2000 }).catch(() => {});
    else await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden', timeout: 3000 }).catch(() => {});
  }
  return headings;
}

// Each tab is visited once with its disclosures expanded. Disclosures are re-queried after
// every click because polling pages re-render and stale handles stop being clickable.
async function expandAll(page) {
  const tabs = page.locator('main [role=tab]');
  const count = await tabs.count();
  if (!count) return expandDisclosures(page);
  for (let i = 0; i < count; i++) {
    await tabs.nth(i).click({ timeout: 2000 }).catch(() => {});
    await page.waitForLoadState('networkidle').catch(() => {});
    await expandDisclosures(page);
  }
}

async function expandDisclosures(page) {
  for (let pass = 0; pass < MAX_DISCLOSURE_PASSES; pass++) {
    let opened = 0;
    let skip = 0;
    for (let clicks = 0; clicks < MAX_CLICKS_PER_PASS; clicks++) {
      const closed = page.locator(CLOSED);
      const before = await closed.count();
      if (skip >= before) break;
      const t = closed.nth(skip);
      await t.click({ timeout: 2000 }).catch(() => {});
      // Sections query only while open and on screen (useSectionVisible).
      await t.scrollIntoViewIfNeeded({ timeout: 1000 }).catch(() => {});
      await page.waitForTimeout(SECTION_SETTLE_MS);
      if ((await page.locator(CLOSED).count()) < before) opened++;
      else skip++;
    }
    await page.waitForLoadState('networkidle').catch(() => {});
    if (!opened) return;
  }
}

// Returns true when the editor dialog actually appeared.
async function openEditor(page) {
  const btn = page.locator(OPEN_EDITOR).first();
  if (!(await btn.count())) return false;
  await btn.click({ timeout: 5000 });
  const opened = await page.locator('[role=dialog]').first()
    .waitFor({ state: 'visible', timeout: 5000 }).then(() => true, () => false);
  if (opened) {
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(1000);
  }
  await page.keyboard.press('Escape');
  return opened;
}

export async function crawl({ base, out, uiBase = '', routes }) {
  mkdirSync(out, { recursive: true });
  const feeds = await listFeeds(base, routes.episodesPerFeed);
  const expected = expandRoutes(routes, feeds);
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    recordHar: { path: join(out, 'crawl.har'), content: 'embed', urlFilter: /\/api\/v1\// },
  });
  const page = await context.newPage();
  const exceptions = [];
  const serverErrors = [];
  const failures = [];
  let responses = 0;
  page.on('pageerror', (err) => exceptions.push(`${page.url()}: ${err.message}`));
  page.on('response', (res) => {
    if (!res.url().includes('/api/v1/')) return;
    responses++;
    if (isCrawlError(res.status())) serverErrors.push(`${res.status()} ${res.url()}`);
  });
  const visited = [];
  const dialogsDismissed = [];
  let editorOpened = false;
  for (const route of expected) {
    try {
      await page.goto(`${base}${uiBase}${route}`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(SETTLE_MS);
      dialogsDismissed.push(...await dismissDialogs(page));
      await expandAll(page);
      dialogsDismissed.push(...await dismissDialogs(page));
      await page.waitForTimeout(500);
      if (!editorOpened && /\/episodes\/[^/?]+$/.test(route)) editorOpened = await openEditor(page);
      visited.push(route);
    } catch (err) {
      failures.push(`${route}: ${err.message}`);
    }
  }
  await context.close();
  await browser.close();
  const missing = expected.filter((r) => !visited.includes(r));
  writeAtomic(join(out, 'routes-visited.json'),
    JSON.stringify({ expected, visited, failures, serverErrors, exceptions, editorOpened, dialogsDismissed }, null, 2));
  return { responses, expected, visited, missing, failures, serverErrors, exceptions, editorOpened, dialogsDismissed };
}

async function main(argv) {
  const base = argValue(argv, '--base');
  const out = argValue(argv, '--out');
  const uiBase = argValue(argv, '--ui-base') ?? '';
  if (!base || !out) throw new Error('usage: --base http://host:8000 --out <dir> [--ui-base /ui]');
  const routes = JSON.parse(readFileSync(new URL('./routes.json', import.meta.url), 'utf8'));
  const r = await crawl({ base: base.replace(/\/$/, ''), out: resolve(out), uiBase, routes });
  for (const line of [...r.failures, ...r.serverErrors, ...r.exceptions]) console.error(line);
  if (!r.editorOpened) console.warn('warning: ad-review modal never opened; its traffic is missing from the HAR');
  console.log(`pages visited: ${r.visited.length}/${r.expected.length}, API responses captured: ${r.responses}, 5xx/429: ${r.serverErrors.length}, exceptions: ${r.exceptions.length}, load failures: ${r.failures.length}, dialogs dismissed: ${r.dialogsDismissed.length}`);
  if (r.serverErrors.length || r.exceptions.length || r.failures.length || r.missing.length) process.exit(1);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main(process.argv.slice(2)).catch((err) => { console.error(String(err)); process.exit(1); });
}
