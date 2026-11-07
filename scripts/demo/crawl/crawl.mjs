import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import { argValue } from './lib/args.mjs';

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
  const res = await fetch(`${base}/api/v1${path}`);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
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

// Opens every collapsed section and tab until nothing new appears.
async function expandAll(page) {
  for (let pass = 0; pass < 4; pass++) {
    const toggles = page.locator('main button[aria-expanded="false"], main summary, [role=tab][aria-selected="false"]');
    const n = await toggles.count();
    if (!n) return;
    for (let i = 0; i < n; i++) {
      await toggles.nth(i).click({ timeout: 2000 }).catch(() => {});
    }
    await page.waitForLoadState('networkidle').catch(() => {});
  }
}

async function visit(page, url, { expand, openEditor }) {
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForTimeout(SETTLE_MS);
  if (expand) {
    await expandAll(page);
    await page.waitForTimeout(500);
  }
  if (openEditor) {
    const btn = page.locator(OPEN_EDITOR).first();
    if (await btn.count()) {
      await btn.click();
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.waitForTimeout(1000);
      await page.keyboard.press('Escape');
    }
  }
}

export async function crawl({ base, out, uiBase = '', routes }) {
  mkdirSync(out, { recursive: true });
  const feeds = await listFeeds(base, routes.episodesPerFeed);
  const list = expandRoutes(routes, feeds);
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    recordHar: { path: join(out, 'crawl.har'), content: 'embed', urlFilter: /\/api\/v1\// },
  });
  const page = await context.newPage();
  const stats = { pages: 0, responses: 0, fiveXX: 0, exceptions: [] };
  const failures = [];
  page.on('pageerror', (err) => stats.exceptions.push(`${page.url()}: ${err.message}`));
  page.on('response', (res) => {
    if (!res.url().includes('/api/v1/')) return;
    stats.responses++;
    if (res.status() >= 500) { stats.fiveXX++; failures.push(`${res.status()} ${res.url()}`); }
  });
  const visited = [];
  let editorOpened = false;
  for (const route of list) {
    const isEpisode = /\/episodes\/[^/?]+$/.test(route);
    const opts = {
      expand: route === '/settings' || /^\/feeds\/[^/]+$/.test(route),
      openEditor: isEpisode && !editorOpened,
    };
    try {
      await visit(page, `${base}${uiBase}${route}`, opts);
      if (opts.openEditor && (await page.locator(OPEN_EDITOR).count())) editorOpened = true;
      visited.push(route);
      stats.pages++;
    } catch (err) {
      failures.push(`${route}: ${err.message}`);
    }
  }
  await context.close();
  await browser.close();
  writeFileSync(join(out, 'routes-visited.json'), JSON.stringify({ visited, failures, exceptions: stats.exceptions }, null, 2));
  return { stats, failures, visited, expected: list };
}

async function main(argv) {
  const base = argValue(argv, '--base');
  const out = argValue(argv, '--out');
  const uiBase = argValue(argv, '--ui-base') ?? '';
  if (!base || !out) throw new Error('usage: --base http://host:8000 --out <dir> [--ui-base /ui]');
  const routes = JSON.parse(readFileSync(new URL('./routes.json', import.meta.url), 'utf8'));
  const { stats, failures } = await crawl({ base: base.replace(/\/$/, ''), out: resolve(out), uiBase, routes });
  for (const f of [...failures, ...stats.exceptions]) console.error(f);
  console.log(`pages visited: ${stats.pages}, API responses captured: ${stats.responses}, 5xx: ${stats.fiveXX}, exceptions: ${stats.exceptions.length}`);
  if (stats.fiveXX || stats.exceptions.length) process.exit(1);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main(process.argv.slice(2)).catch((err) => { console.error(String(err)); process.exit(1); });
}
