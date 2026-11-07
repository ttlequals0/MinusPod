import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fixtureKey } from './lib/keys.mjs';
import { scrubFeedKeys, writeFixture, mergeIndex, argValue } from './lib/store.mjs';

const PAGE = 100;
const EPISODE_PAGE = 500;
const POOL = 8;
const RETRY_MS = Number(process.env.DUMP_RETRY_MS ?? 1000);

const STATIC_PATHS = [
  '/feeds?episodesPerFeed=20&includeLatestEpisodes=true',
  '/patterns',
  '/sponsors?include_inactive=true',
  '/episodes/awaiting-approval?limit=500&offset=0',
  '/episodes/held?limit=500&offset=0',
  '/episodes/processing?limit=1000&offset=0',
  '/patterns/cleanup/suggestions?limit=200&offset=0',
  '/changelog?limit=50',
];

const PAGED = [
  { path: (p) => `/history?limit=${PAGE}&page=${p}&sort_by=processed_at&sort_dir=desc`, field: 'history' },
  { path: (p) => `/history/downloads?limit=${PAGE}&page=${p}&sortBy=last_seen_at&sortDir=desc`, field: 'downloads' },
  { path: (p) => `/detections?limit=${PAGE}&order=desc&page=${p}&sort=date&status=all`, field: null },
  { path: (p) => `/stats/model-usage?limit=${PAGE}&page=${p}&sortBy=knownCostUsd&sortDir=desc`, field: null },
  { path: (p) => `/stats/episode-costs?limit=${PAGE}&page=${p}&sortBy=lastActivityAt&sortDir=desc`, field: null },
];

const FEED_PATHS = (slug) => [
  `/feeds/${slug}`,
  `/feeds/${slug}/tags`,
  `/feeds/${slug}/cue-templates`,
  `/feeds/${slug}/cue-detections/advisory`,
  `/feeds/${slug}/ad-distribution`,
  `/feeds/${slug}/subscriber-keys`,
  `/feeds/${slug}/cue-candidate-dismissals`,
];

const EPISODE_PATHS = (slug, id) => [
  `/feeds/${slug}/episodes/${id}`,
  `/feeds/${slug}/episodes/${id}/peaks?resolution_ms=100`,
  `/feeds/${slug}/episodes/${id}/original-segments`,
  `/feeds/${slug}/episodes/${id}/final-segments`,
  `/feeds/${slug}/episodes/${id}/original-transcript`,
  `/feeds/${slug}/episodes/${id}/sponsorblock/candidates`,
  `/stats/episode-costs/runs?episodeId=${id}&slug=${slug}`,
  `/stats/reviewer?episode_id=${id}&podcast_slug=${slug}`,
];

async function pool(items, worker) {
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(POOL, items.length) }, async () => {
    while (next < items.length) await worker(items[next++]);
  }));
}

function rowsOf(body, field) {
  if (field) return body?.[field];
  const arrays = Object.values(body ?? {}).filter(Array.isArray);
  return arrays.sort((a, b) => b.length - a.length)[0];
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
        await new Promise((r) => setTimeout(r, Math.min(wait, 30) * RETRY_MS));
      }
    } catch (err) {
      stats.errors.push({ path, error: String(err) });
      return null;
    }
    const type = res.headers.get('content-type') ?? '';
    const text = await res.text();
    const fixture = { status: res.status };
    if (res.status !== 204 && res.status !== 304 && text) {
      if (/json/i.test(type)) {
        try { fixture.body = scrubFeedKeys(JSON.parse(text)); } catch (err) { stats.errors.push({ path, error: `bad json: ${err.message}` }); }
      } else if (/^text\/plain/i.test(type)) {
        fixture.body = text.replace(/([?&]key=)[^&"'\s]+/g, '$1demo');
      }
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

  async function dumpPaged({ path, field }) {
    for (let page = 1; ; page++) {
      const fx = await dump(path(page));
      const rows = fx?.status === 200 ? rowsOf(fx.body, field) : null;
      if (!rows || rows.length < PAGE) return;
    }
  }

  async function dumpEpisodes(slug, status) {
    const q = (offset) => `/feeds/${slug}/episodes?limit=${EPISODE_PAGE}&offset=${offset}&sort_by=created_at&sort_dir=desc&status=${status}`;
    const first = await get(q(0));
    if (!first) return [];
    let all = first.fixture.body?.episodes ?? [];
    let last = all;
    // Concatenate further pages under the canonical offset=0 key.
    for (let offset = EPISODE_PAGE; first.fixture.status === 200 && last.length === EPISODE_PAGE; offset += EPISODE_PAGE) {
      const next = await get(q(offset));
      last = next?.fixture.status === 200 ? (next.fixture.body?.episodes ?? []) : [];
      all = all.concat(last);
    }
    if (first.fixture.body?.episodes) first.fixture.body.episodes = all;
    record(first);
    return all;
  }

  async function run() {
    let slugs = [];
    for (const p of STATIC_PATHS) {
      const fx = await dump(p);
      if (p === STATIC_PATHS[0]) slugs = (fx?.body?.feeds ?? []).map((f) => f.slug);
    }
    for (const spec of PAGED) await dumpPaged(spec);
    const episodes = [];
    await pool(slugs, async (slug) => {
      for (const p of FEED_PATHS(slug)) await dump(p);
      const seen = new Set();
      for (const status of ['all', 'filtered']) {
        for (const e of await dumpEpisodes(slug, status)) {
          if (!seen.has(e.id)) { seen.add(e.id); episodes.push([slug, e.id]); }
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
  const base = argValue(argv, '--base');
  const out = argValue(argv, '--out');
  if (!base || !out) throw new Error('usage: --base http://host:8000 --out <fixtures dir>');
  const { run, stats } = createDumper({ base: base.replace(/\/$/, ''), out });
  await run();
  console.log(JSON.stringify(stats, null, 2));
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main(process.argv.slice(2));
