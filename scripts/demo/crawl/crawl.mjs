import { mkdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import { argValue } from './lib/args.mjs';
import { writeAtomic } from './lib/store.mjs';

const SETTLE_MS = 2500;
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

// Each tab is clicked once from a snapshot; disclosures loop until none are left closed.
async function expandAll(page) {
  const tabs = await page.locator('main [role=tab][aria-selected="false"]').elementHandles();
  for (const tab of tabs) await tab.click({ timeout: 2000 }).catch(() => {});
  await page.waitForLoadState('networkidle').catch(() => {});
  for (let pass = 0; pass < MAX_DISCLOSURE_PASSES; pass++) {
    const toggles = await page.locator('main button[aria-expanded="false"], main summary').elementHandles();
    if (!toggles.length) return;
    for (const t of toggles) await t.click({ timeout: 2000 }).catch(() => {});
    await page.waitForLoadState('networkidle').catch(() => {});
  }
}

// Returns true when the editor dialog actually appeared.
async function openEditor(page) {
  const btn = page.locator(OPEN_EDITOR).first();
  if (!(await btn.count())) return false;
  await btn.click();
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
    if (res.status() >= 500) serverErrors.push(`${res.status()} ${res.url()}`);
  });
  const visited = [];
  let editorOpened = false;
  for (const route of expected) {
    try {
      await page.goto(`${base}${uiBase}${route}`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(SETTLE_MS);
      await expandAll(page);
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
    JSON.stringify({ expected, visited, failures, serverErrors, exceptions, editorOpened }, null, 2));
  return { responses, expected, visited, missing, failures, serverErrors, exceptions, editorOpened };
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
  console.log(`pages visited: ${r.visited.length}/${r.expected.length}, API responses captured: ${r.responses}, 5xx: ${r.serverErrors.length}, exceptions: ${r.exceptions.length}, load failures: ${r.failures.length}`);
  if (r.serverErrors.length || r.exceptions.length || r.failures.length || r.missing.length) process.exit(1);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main(process.argv.slice(2)).catch((err) => { console.error(String(err)); process.exit(1); });
}
