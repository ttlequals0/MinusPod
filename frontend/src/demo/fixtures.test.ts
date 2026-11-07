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
