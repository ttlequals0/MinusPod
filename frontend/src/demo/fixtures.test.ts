import { test, expect } from 'vitest';
import { fixtureKey, emptyDefault, resolveFixture, __setIndexForTests, __setLoaderForTests } from './fixtures';

test('fixtureKey strips the api prefix and sorts params', () => {
  expect(fixtureKey('http://x/api/v1/feeds/a/episodes?status=all&limit=25')).toBe('/feeds/a/episodes?limit=25&status=all');
  expect(fixtureKey('http://x/api/v1/settings?')).toBe('/settings');
  expect(fixtureKey('http://x/api/v1/search?q=a%20b')).toBe('/search?q=a+b');
});

test('emptyDefault matches by path pattern', () => {
  expect(emptyDefault('/feeds/x/episodes').body).toEqual({ episodes: [], total: 0, limit: 25, offset: 0 });
  expect(emptyDefault('/feeds/x/episodes/e1')).toEqual({ status: 404, body: { error: 'Not found in demo' } });
  expect(emptyDefault('/episodes/processing').body).toEqual([]);
});

test('resolveFixture prefers exact, then path-only with fewest params, then default', async () => {
  __setIndexForTests({ '/settings': 'a.json', '/stats/llm?from=2026-01-01&to=2026-01-31': 'b.json', '/stats/llm?from=2026-01-01': 'c.json' });
  __setLoaderForTests(async (file) => ({ status: 200, body: { file } }));
  expect((await resolveFixture(new URL('http://x/api/v1/settings'))).body).toEqual({ file: 'a.json' });
  expect((await resolveFixture(new URL('http://x/api/v1/stats/llm?from=2026-03-01&to=2026-03-31'))).body).toEqual({ file: 'c.json' });
  expect((await resolveFixture(new URL('http://x/api/v1/sponsors'))).body).toEqual({ sponsors: [] });
});

test('resolveFixture falls through when the loader rejects', async () => {
  __setIndexForTests({ '/sponsors': 'x.json' });
  __setLoaderForTests(async () => { throw new Error('boom'); });
  expect((await resolveFixture(new URL('http://x/api/v1/sponsors'))).body).toEqual({ sponsors: [] });
});
