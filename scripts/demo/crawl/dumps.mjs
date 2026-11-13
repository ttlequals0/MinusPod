import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fixtureKey } from './lib/keys.mjs';
import { scrubBody, writeFixture, mergeIndex, applyOverrides, readOverrides } from './lib/store.mjs';
import { windowFixtureDir } from './lib/transcripts.mjs';
import { argValue, checkFlags } from './lib/args.mjs';

const PAGE = 100;
const EPISODE_PAGE = 500;
const MATCH_PAGE = 10;
const POOL = 8;

const STATIC_PATHS = [
  '/feeds?episodesPerFeed=20&includeLatestEpisodes=true',
  '/patterns',
  '/sponsors?include_inactive=true',
  '/episodes/awaiting-approval?limit=500&offset=0',
  '/episodes/held?limit=500&offset=0',
  '/episodes/processing?limit=1000&offset=0',
  '/patterns/cleanup/suggestions?limit=200&offset=0',
  '/changelog?limit=50',
  '/podping/nodes', // the Settings query is gated on a setting that is off
  // settings sections and pattern tabs the crawl only reaches after their parent request succeeds
  '/failover',
  '/networks',
  '/settings/offline-queue',
  '/settings/provider-budget',
  '/settings/rate-limit-hold',
  '/settings/processing-schedule',
  '/settings/providers/primary/affected-runs',
  '/settings/providers/secondary/affected-runs',
  '/sponsors/normalizations',
];

const PAGED = [
  { path: (p) => `/history?limit=${PAGE}&page=${p}&sort_by=processed_at&sort_dir=desc`, field: 'history' },
  { path: (p) => `/history/downloads?limit=${PAGE}&page=${p}&sortBy=last_seen_at&sortDir=desc`, field: 'downloads' },
  { path: (p) => `/detections?limit=${PAGE}&order=desc&page=${p}&sort=date&status=all`, field: 'detections' },
  { path: (p) => `/stats/model-usage?limit=${PAGE}&page=${p}&sortBy=knownCostUsd&sortDir=desc`, field: 'items' },
  { path: (p) => `/stats/episode-costs?limit=${PAGE}&page=${p}&sortBy=lastActivityAt&sortDir=desc`, field: 'items' },
];

// The frontend builds these paths raw and ids are hex today, so these keys agree with its requests.
const FEED_PATHS = (slug) => {
  const f = `/feeds/${encodeURIComponent(slug)}`;
  return [f, `${f}/tags`, `${f}/cue-templates`, `${f}/cue-detections/advisory`, `${f}/ad-distribution`,
    `${f}/subscriber-keys`, `${f}/cue-candidate-dismissals`];
};

const EPISODE_PATHS = (slug, id) => {
  const s = encodeURIComponent(slug);
  const e = encodeURIComponent(id);
  const f = `/feeds/${s}/episodes/${e}`;
  return [f, `${f}/original-segments`, `${f}/final-segments`,
    `${f}/original-transcript`, `${f}/sponsorblock/candidates`,
    `/stats/episode-costs/runs?episodeId=${e}&slug=${s}`, `/stats/spend/attempts?episodeId=${e}&slug=${s}`, `/stats/reviewer?episode_id=${e}&podcast_slug=${s}`];
};

async function pool(items, worker) {
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(POOL, items.length) }, async () => {
    while (next < items.length) await worker(items[next++]);
  }));
}

function rowsOf(body, field) {
  if (!Array.isArray(body?.[field])) throw new Error(`dump response has no ${field} array`);
  return body[field];
}

export function createDumper({ base, out, fetchImpl = fetch }) {
  const index = {};
  const stats = { written: 0, byStatus: {}, errors: [] };

  async function get(path) {
    const url = `${base}/api/v1${path}`;
    const key = fixtureKey(url);
    let res;
    try {
      // A 429 is the server's rate limit, not data: wait and retry.
      for (let attempt = 0; ; attempt++) {
        res = await fetchImpl(url, { headers: { Accept: 'application/json' } });
        if (res.status !== 429 || attempt >= 8) break;
        const wait = Number(res.headers.get('retry-after')) || 2 ** attempt;
        await res.arrayBuffer();
        await new Promise((r) => setTimeout(r, Math.min(wait, 30) * Number(process.env.DUMP_RETRY_MS ?? 1000)));
      }
    } catch (err) {
      stats.errors.push({ path, error: String(err) });
      return null;
    }
    if (res.status === 429 || res.status >= 500) {
      stats.errors.push({ key, status: res.status });
      return null;
    }
    const type = res.headers.get('content-type') ?? '';
    const text = await res.text();
    const fixture = { status: res.status };
    if (res.status !== 204 && res.status !== 304 && text) {
      if (/json/i.test(type)) {
        try { fixture.body = scrubBody(JSON.parse(text)); } catch (err) { stats.errors.push({ path, error: `bad json: ${err.message}` }); }
      } else if (/^text\/plain/i.test(type)) {
        fixture.body = text.replace(/([?&]key=)[^&"'\s]+/g, '$1demo');
      } else if (res.ok) {
        stats.errors.push({ path, error: `unexpected content-type: ${type}` });
      } // non-2xx non-JSON (the backend's HTML 404) keeps status only
    }
    return { key, fixture };
  }

  function record({ key, fixture }) {
    index[key] = writeFixture(out, key, fixture);
    stats.written++;
    stats.byStatus[fixture.status] = (stats.byStatus[fixture.status] ?? 0) + 1;
  }

  async function dump(path) {
    const r = await get(path);
    if (r) record(r);
    return r?.fixture;
  }

  async function dumpPaged({ path, field, pageSize = PAGE }) {
    for (let page = 1; ; page++) {
      const fx = await dump(path(page));
      const rows = fx?.status === 200 ? rowsOf(fx.body, field) : null;
      if (!rows || rows.length < pageSize) return;
    }
  }

  async function dumpEpisodes(slug, status) {
    const q = (offset) => `/feeds/${encodeURIComponent(slug)}/episodes?limit=${EPISODE_PAGE}&offset=${offset}&sort_by=created_at&sort_dir=desc&status=${status}`;
    const first = await get(q(0));
    if (!first) return [];
    let all = first.fixture.body?.episodes ?? [];
    let last = all;
    // Concatenate further pages under the canonical offset=0 key.
    for (let offset = EPISODE_PAGE; first.fixture.status === 200 && last.length === EPISODE_PAGE; offset += EPISODE_PAGE) {
      const next = await get(q(offset));
      if (next?.fixture.status !== 200) {
        if (next) stats.errors.push({ key: next.key, status: next.fixture.status });
        break;
      }
      last = next.fixture.body?.episodes ?? [];
      all = all.concat(last);
    }
    if (first.fixture.body?.episodes) first.fixture.body.episodes = all;
    record(first);
    return all;
  }

  async function run() {
    let feeds = [];
    let patterns = [];
    for (const p of STATIC_PATHS) {
      const fx = await dump(p);
      if (p === STATIC_PATHS[0]) {
        if (fx?.status !== 200 || !Array.isArray(fx.body?.feeds)) throw new Error('/feeds dump failed: no feeds array');
        feeds = fx.body.feeds.map((f) => ({ slug: f.slug, feedType: f.feedType }));
      }
      if (p === '/patterns') patterns = fx?.body?.patterns ?? [];
    }
    for (const spec of PAGED) await dumpPaged(spec);
    await pool(patterns, async ({ id }) => {
      const path = `/patterns/${encodeURIComponent(id)}/audio`;
      await dump(path);
      await dumpPaged({
        path: (page) => `${path}/matches?page=${page}&perPage=${MATCH_PAGE}`,
        field: 'matches',
        pageSize: MATCH_PAGE,
      });
    });
    const episodes = [];
    await pool(feeds, async ({ slug, feedType }) => {
      for (const p of FEED_PATHS(slug)) await dump(p);
      const seen = new Set();
      for (const status of ['all', 'filtered']) {
        for (const e of await dumpEpisodes(slug, status)) {
          if (!seen.has(e.id)) { seen.add(e.id); if (feedType !== 'recents') episodes.push([slug, e.id]); }
        }
      }
    });
    await pool(episodes, async ([slug, id]) => {
      for (const p of EPISODE_PATHS(slug, id)) await dump(p);
    });
    mergeIndex(out, index);
    return stats;
  }

  return { run, stats };
}

async function main(argv) {
  checkFlags(argv, ['--base', '--out', '--overrides', '--transcript-window']);
  const base = argValue(argv, '--base');
  const out = argValue(argv, '--out');
  const overrides = argValue(argv, '--overrides');
  const windowSec = argValue(argv, '--transcript-window');
  if (!base || !out) throw new Error('usage: --base http://host:8000 --out <fixtures dir> [--overrides <dir>] [--transcript-window <seconds>]');
  if (windowSec !== undefined && !/^\d+$/.test(windowSec)) {
    console.error('--transcript-window must be a non-negative integer number of seconds');
    process.exit(2);
  }
  const overrideIndex = overrides ? readOverrides(overrides) : null;
  const { run, stats } = createDumper({ base: base.replace(/\/$/, ''), out });
  try { await run(); } catch (err) { console.error(String(err)); process.exit(1); }
  if (overrideIndex) {
    applyOverrides(out, overrides, overrideIndex);
    stats.overrides = Object.keys(overrideIndex).length;
  }
  if (windowSec !== undefined) stats.windowed = windowFixtureDir(out, Number(windowSec));
  console.log(JSON.stringify(stats, null, 2));
  if (stats.errors.length) process.exit(1);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main(process.argv.slice(2));
