import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createDumper } from './dumps.mjs';
import { fileFor } from './lib/keys.mjs';

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

function fakeFetch(hits) {
  return async (url) => {
    const u = new URL(url);
    hits.push(u.pathname + u.search);
    const p = u.pathname.replace('/api/v1', '');
    const q = u.searchParams;
    if (p === '/feeds') return json(200, { feeds: [{ slug: 'a', feedUrl: 'http://h/f?key=secret' }] });
    if (p === '/history') return json(200, { history: Array.from({ length: q.get('page') === '1' ? 100 : 3 }, (_, i) => ({ i })) });
    if (p === '/feeds/a/episodes') {
      const off = Number(q.get('offset'));
      return json(200, { episodes: Array.from({ length: off === 0 ? 500 : 2 }, (_, i) => ({ id: `e${off + i}` })), total: 502 });
    }
    if (p === '/feeds/a/tags') return new Response(null, { status: 204 });
    if (p === '/feeds/a/episodes/e0') return json(404, { error: 'gone' });
    return json(200, {});
  };
}

test('dumper pages, concatenates episodes, scrubs keys and records every status', async () => {
  const out = mkdtempSync(join(tmpdir(), 'dump-'));
  const hits = [];
  const d = createDumper({ base: 'http://h', out, fetchImpl: fakeFetch(hits) });
  await d.run();
  const index = JSON.parse(readFileSync(join(out, 'index.json'), 'utf8'));
  const read = (key) => JSON.parse(readFileSync(join(out, index[key]), 'utf8'));
  const feedsKey = '/feeds?episodesPerFeed=20&includeLatestEpisodes=true';
  assert.equal(index[feedsKey], fileFor(feedsKey));
  assert.equal(read(feedsKey).body.feeds[0].feedUrl, 'http://h/f?key=demo');
  assert.ok(index['/history?limit=100&page=1&sort_by=processed_at&sort_dir=desc']);
  assert.ok(index['/history?limit=100&page=2&sort_by=processed_at&sort_dir=desc']);
  assert.equal(index['/history?limit=100&page=3&sort_by=processed_at&sort_dir=desc'], undefined);
  const epKey = '/feeds/a/episodes?limit=500&offset=0&sort_by=created_at&sort_dir=desc&status=all';
  assert.equal(read(epKey).body.episodes.length, 502);
  assert.equal(index['/feeds/a/episodes?limit=500&offset=500&sort_by=created_at&sort_dir=desc&status=all'], undefined);
  assert.deepEqual(read('/feeds/a/tags'), { status: 204 });
  assert.deepEqual(read('/feeds/a/episodes/e0'), { status: 404, body: { error: 'gone' } });
  assert.ok(index['/stats/reviewer?episode_id=e501&podcast_slug=a']);
  assert.equal(d.stats.byStatus[404], 1);
});
