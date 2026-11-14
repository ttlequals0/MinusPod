import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createDumper } from './dumps.mjs';
import { windowFixtureDir } from './lib/transcripts.mjs';
import { applyOverrides, mergeIndex, writeFixture } from './lib/store.mjs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { fileFor } from './lib/keys.mjs';

process.env.DUMP_RETRY_MS = '0';
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const EP = (status, off) => `/feeds/a/episodes?limit=500&offset=${off}&sort_by=created_at&sort_dir=desc&status=${status}`;

function fakeFetch(hits, feeds = [{ slug: 'a', feedUrl: 'http://h/f?key=secret' }]) {
  return async (url) => {
    const u = new URL(url);
    const path = u.pathname.replace('/api/v1', '');
    hits.push(path + u.search);
    const q = u.searchParams;
    if (path === '/feeds') return json(200, { feeds });
    if (path === '/history') return json(200, { history: Array.from({ length: q.get('page') === '1' ? 100 : 3 }, (_, i) => ({ i })) });
    if (path === '/feeds/a/episodes') {
      const off = Number(q.get('offset'));
      if (q.get('status') === 'filtered') return json(200, { episodes: Array.from({ length: off === 0 ? 500 : 1 }, (_, i) => ({ id: `f${off + i}` })) });
      return json(200, { episodes: Array.from({ length: off === 0 ? 500 : 2 }, (_, i) => ({ id: `e${off + i}` })), total: 502 });
    }
    if (path.endsWith('/episodes')) return json(200, { episodes: [{ id: 'r1' }] });
    if (path === '/feeds/a/tags') return new Response(null, { status: 204 });
    if (path === '/feeds/a/episodes/e0') return json(404, { error: 'gone' });
    if (path === '/changelog') return json(200, { sections: [] });
    if (path === '/detections' || path === '/history/downloads') return json(200, { [path === '/detections' ? 'detections' : 'downloads']: [] });
    if (path.startsWith('/stats/') && path.split('/').length === 3) return json(200, { items: [] });
    return json(200, {});
  };
}

const run = async (fetchImpl) => {
  const out = mkdtempSync(join(tmpdir(), 'dump-'));
  const d = createDumper({ base: 'http://h', out, fetchImpl });
  await d.run();
  const index = JSON.parse(readFileSync(join(out, 'index.json'), 'utf8'));
  const read = (key) => JSON.parse(readFileSync(join(out, index[key]), 'utf8'));
  return { d, index, read, out };
};

test('dumper pages, concatenates episodes, scrubs keys and records every status', async () => {
  const hits = [];
  const { d, index, read } = await run(fakeFetch(hits));
  const feedsKey = '/feeds?episodesPerFeed=20&includeLatestEpisodes=true';
  assert.deepEqual(hits.slice(0, 8), [
    '/feeds?episodesPerFeed=20&includeLatestEpisodes=true',
    '/patterns',
    '/sponsors?include_inactive=true',
    '/episodes/awaiting-approval?limit=500&offset=0',
    '/episodes/held?limit=500&offset=0',
    '/episodes/processing?limit=1000&offset=0',
    '/patterns/cleanup/suggestions?limit=200&offset=0',
    '/changelog?limit=50',
  ]);
  assert.equal(index[feedsKey], fileFor(feedsKey));
  assert.equal(read(feedsKey).body.feeds[0].feedUrl, 'http://h/f?key=demo');
  assert.ok(index['/history?limit=100&page=2&sort_by=processed_at&sort_dir=desc']);
  assert.equal(index['/history?limit=100&page=3&sort_by=processed_at&sort_dir=desc'], undefined);
  assert.equal(read(EP('all', 0)).body.episodes.length, 502);
  assert.equal(read(EP('filtered', 0)).body.episodes.length, 501);
  assert.equal(index[EP('all', 500)], undefined);
  assert.deepEqual(read('/feeds/a/tags'), { status: 204 });
  assert.deepEqual(read('/feeds/a/episodes/e0'), { status: 404, body: { error: 'gone' } });
  assert.ok(index['/stats/spend/attempts?episodeId=e501&slug=a']);
  assert.ok(index['/stats/reviewer?episode_id=e501&podcast_slug=a']);
  assert.ok(index['/stats/reviewer?episode_id=f500&podcast_slug=a']);
  assert.equal(d.stats.byStatus[404], 1);
  assert.deepEqual(d.stats.errors, []);
});

test('slugs and ids are percent-encoded in paths and queries', async () => {
  const hits = [];
  const f = async (url) => {
    const u = new URL(url);
    const path = u.pathname.replace('/api/v1', '');
    hits.push(path + u.search);
    if (path === '/feeds') return json(200, { feeds: [{ slug: 'a b', feedType: 'subscribed' }] });
    if (path.endsWith('/episodes')) return json(200, { episodes: [{ id: 'x 1%' }] });
    if (path === '/history') return json(200, { history: [] });
    if (path === '/detections') return json(200, { detections: [] });
    if (path === '/history/downloads') return json(200, { downloads: [] });
    if (path.startsWith('/stats/') && path.split('/').length === 3) return json(200, { items: [] });
    return json(200, {});
  };
  const { index } = await run(f);
  assert.ok(hits.includes('/feeds/a%20b/episodes/x%201%25/original-segments'));
  assert.ok(hits.includes('/stats/reviewer?episode_id=x%201%25&podcast_slug=a%20b'));
  assert.ok(index['/feeds/a%20b/episodes/x%201%25/original-segments']);
  assert.ok(index['/stats/episode-costs/runs?episodeId=x+1%25&slug=a+b']);
});

test('pattern audio and every match page use the UI request keys', async () => {
  const hits = [];
  const inner = fakeFetch(hits, []);
  const { index, read, d } = await run(async (url) => {
    const u = new URL(url);
    if (u.pathname === '/api/v1/patterns') return json(200, { patterns: [{ id: 7 }, { id: 8 }] });
    if (u.pathname === '/api/v1/patterns/7/audio') return json(200, { state: 'off' });
    if (u.pathname === '/api/v1/patterns/8/audio') return json(200, { state: 'none' });
    if (u.pathname.endsWith('/audio/matches')) {
      const page = Number(u.searchParams.get('page'));
      const count = u.pathname.includes('/7/') ? (page === 1 ? 10 : 1) : 0;
      assert.equal(u.searchParams.get('perPage'), '10');
      return json(200, { matches: Array.from({ length: count }, (_, id) => ({ id })), page, perPage: 10 });
    }
    return inner(url);
  });
  assert.equal(read('/patterns/7/audio').body.state, 'off');
  assert.equal(read('/patterns/8/audio').body.state, 'none');
  assert.equal(read('/patterns/7/audio/matches?page=1&perPage=10').body.matches.length, 10);
  assert.equal(read('/patterns/7/audio/matches?page=2&perPage=10').body.matches.length, 1);
  assert.equal(index['/patterns/7/audio/matches?page=3&perPage=10'], undefined);
  assert.deepEqual(read('/patterns/8/audio/matches?page=1&perPage=10').body.matches, []);
  assert.deepEqual(d.stats.errors, []);
});

test('a 429 that exhausts retries is an error, not a fixture', async () => {
  const f = async (url) => (new URL(url).pathname.endsWith('/feeds')
    ? json(200, { feeds: [] })
    : new Response('slow down', { status: 429 }));
  const { d, index } = await run(f);
  assert.equal(index['/patterns'], undefined);
  assert.ok(d.stats.errors.some((e) => e.key === '/patterns' && e.status === 429));
});

test('recents feeds dump their episode lists but no per-episode endpoints', async () => {
  const hits = [];
  const feeds = [{ slug: 'a', feedType: 'subscribed' }, { slug: 'rec', feedType: 'recents' }];
  const base = fakeFetch(hits, feeds);
  const { index } = await run(base);
  assert.ok(index['/feeds/rec/episodes?limit=500&offset=0&sort_by=created_at&sort_dir=desc&status=all']);
  assert.ok(index['/feeds/rec/episodes?limit=500&offset=0&sort_by=created_at&sort_dir=desc&status=filtered']);
  assert.equal(hits.some((h) => h.startsWith('/feeds/rec/episodes/')), false);
  assert.ok(hits.some((h) => h.startsWith('/feeds/a/episodes/e0')));
});

test('non-200 later episode page, bad feeds dump and unexpected content-type are surfaced', async () => {
  const hits = [];
  const inner = fakeFetch(hits);
  const { d } = await run(async (url) => {
    const u = new URL(url);
    if (u.pathname.endsWith('/episodes') && u.searchParams.get('offset') === '500') return json(500, { error: 'x' });
    if (u.pathname.endsWith('/patterns')) return new Response('<html>', { status: 200, headers: { 'content-type': 'text/html' } });
    if (u.pathname.endsWith('/sponsors')) return new Response('<html>', { status: 404, headers: { 'content-type': 'text/html' } });
    return inner(url);
  });
  assert.ok(d.stats.errors.some((e) => e.status === 500));
  assert.ok(d.stats.errors.some((e) => /content-type/.test(e.error ?? '')));
  assert.equal(d.stats.errors.filter((e) => /content-type/.test(e.error ?? '')).length, 1);
  const out = mkdtempSync(join(tmpdir(), 'dump-'));
  await assert.rejects(createDumper({ base: 'http://h', out, fetchImpl: async () => json(500, {}) }).run(), /feeds/);
  assert.equal(readdirSync(out).includes('index.json'), false);
});

test('overrides applied after a dump win over the dumped fixture', async () => {
  const { index, read, out } = await run(fakeFetch([]));
  assert.deepEqual(read('/patterns'), { status: 200, body: {} });
  const over = mkdtempSync(join(tmpdir(), 'ov-'));
  mergeIndex(over, { '/patterns': writeFixture(over, '/patterns', { status: 200, body: { live: true } }) });
  applyOverrides(out, over);
  assert.deepEqual(JSON.parse(readFileSync(join(out, index['/patterns']), 'utf8')), { status: 200, body: { live: true } });
});

test('a bad --transcript-window exits 2 before any request', () => {
  const r = spawnSync(process.execPath, [fileURLToPath(new URL('./dumps.mjs', import.meta.url)), '--base', 'http://127.0.0.1:9', '--out', 'y', '--transcript-window', '-3']);
  assert.equal(r.status, 2);
});

test('a 5xx from the backend is an error and writes no fixture', async () => {
  const inner = fakeFetch([]);
  const { d, index } = await run(async (url) => (new URL(url).pathname.endsWith('/patterns') ? json(503, { error: 'down' }) : inner(url)));
  assert.equal(index['/patterns'], undefined);
  assert.ok(d.stats.errors.some((e) => e.key === '/patterns' && e.status === 503));
});

test('documented order: dump, then overrides, then transcript window', async () => {
  const transcript = ['[00:00:00.0 --> 00:00:05.0] far before', '[00:01:40.0 --> 00:01:45.0] near the ad', '[00:08:20.0 --> 00:08:25.0] far after'].join('\n');
  const inner = fakeFetch([], [{ slug: 'a', feedType: 'subscribed' }]);
  const { index, read, out } = await run(async (url) => {
    const path = new URL(url).pathname.replace('/api/v1', '');
    if (path === '/feeds/a/episodes') return json(200, { episodes: [{ id: 'e1' }] });
    if (path === '/feeds/a/episodes/e1') return json(200, { id: 'e1', transcript, adMarkers: [{ start: 100, end: 110 }] });
    return inner(url);
  });
  const over = mkdtempSync(join(tmpdir(), 'ov-'));
  mergeIndex(over, { '/patterns': writeFixture(over, '/patterns', { status: 200, body: { live: true } }) });
  applyOverrides(out, over);
  assert.ok(windowFixtureDir(out, 10) >= 1);
  assert.deepEqual(read('/patterns'), { status: 200, body: { live: true } });
  assert.equal(read('/feeds/a/episodes/e1').body.transcript, '[00:01:40.0 --> 00:01:45.0] near the ad');
  assert.ok(index['/feeds/a/episodes/e1']);
});
