import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, vi } from 'vitest';
import { fixtureKey, emptyDefault, loadFixture, resolveFixture, __setIndexForTests, __setLoaderForTests } from './fixtures';

test('fixtureKey strips the api prefix and sorts params', () => {
  expect(fixtureKey('http://x/api/v1/feeds/a/episodes?status=all&limit=25')).toBe('/feeds/a/episodes?limit=25&status=all');
  expect(fixtureKey('http://x/api/v1/settings?')).toBe('/settings');
  expect(fixtureKey('http://x/api/v1/search?q=a%20b')).toBe('/search?q=a+b');
});

test('emptyDefault matches by path pattern', () => {
  expect(emptyDefault('/feeds/x/episodes').body).toEqual({ episodes: [], total: 0, limit: 25, offset: 0 });
  expect(emptyDefault('/feeds/x/episodes/e1')).toEqual({ status: 404, body: { error: 'Not found in demo' } });
  expect(emptyDefault('/episodes/processing').body).toEqual([]);
  expect(emptyDefault('/unknown/thing')).toEqual({ status: 404, body: { error: 'Not found' } });
});

test('emptyDefault answers the pattern audio routes with an empty state', () => {
  expect(emptyDefault('/patterns/7/audio')).toEqual({
    status: 200, body: { state: 'none', available: false, counts: { matches: 0, rejected: 0, outOfScope: 0 } },
  });
  expect(emptyDefault('/patterns/7/audio/matches')).toEqual({
    status: 200, body: { matches: [], page: 1, perPage: 10, total: 0 },
  });
  expect(emptyDefault('/patterns/7/audio/preview').status).toBe(404);
});

test('emptyDefault answers audio matching stats with zeros', () => {
  expect(emptyDefault('/stats/audio-matching')).toEqual({
    status: 200, body: { patternsWithAudio: 0, patternsTotal: 0, audioMatches: 0, foundByAudioOnly: 0, notThisAd: 0 },
  });
});

test('resolveFixture never substitutes a recording with different filters', async () => {
  __setIndexForTests({ '/settings': 'a.json', '/stats/llm?from=2026-01-01&to=2026-01-31': 'b.json', '/stats/llm?from=2026-01-01': 'c.json' });
  __setLoaderForTests(async (file) => ({ status: 200, body: { file } }));
  expect((await resolveFixture(new URL('http://x/api/v1/settings'))).body).toEqual({ file: 'a.json' });
  expect((await resolveFixture(new URL('http://x/api/v1/stats/llm?from=2026-03-01&to=2026-03-31'))).status).toBe(422);
  expect((await resolveFixture(new URL('http://x/api/v1/sponsors'))).body).toEqual({ sponsors: [] });
});

test('resolveFixture reports a failed load instead of claiming an empty list', async () => {
  __setIndexForTests({ '/sponsors': 'x.json' });
  __setLoaderForTests(async () => { throw new Error('boom'); });
  expect((await resolveFixture(new URL('http://x/api/v1/sponsors'))).status).toBe(503);
});

test('a missing filtered episode dump never returns the unfiltered recording', async () => {
  __setIndexForTests({ '/feeds/a/episodes?limit=500&offset=0&sort_by=created_at&sort_dir=desc&status=all': 'a.json' });
  const loader = vi.fn();
  __setLoaderForTests(loader);
  expect((await resolveFixture(new URL('http://x/api/v1/feeds/a/episodes?status=filtered'))).status).toBe(422);
  expect(loader).not.toHaveBeenCalled();
});

test('concurrent loadFixture calls for one key share a single load', async () => {
  __setIndexForTests({ '/settings': 'a.json' });
  const loader = vi.fn(async (file: string) => ({ status: 200, body: { file } }));
  __setLoaderForTests(loader);
  const [a, b] = await Promise.all([loadFixture('/settings'), loadFixture('/settings')]);
  expect(a).toBe(b);
  expect(loader).toHaveBeenCalledTimes(1);
});

test('fixtureKey matches the shared key vectors', () => {
  const file = resolve(dirname(fileURLToPath(import.meta.url)), '../../../scripts/demo/crawl/lib/key-vectors.json');
  const vectors = JSON.parse(readFileSync(file, 'utf8')) as Record<string, string>;
  for (const [url, key] of Object.entries(vectors)) expect(fixtureKey(url)).toBe(key);
});
