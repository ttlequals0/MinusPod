import test from 'node:test';
import assert from 'node:assert/strict';
import { expandRoutes } from './crawl.mjs';

const routes = { static: ['/'], perFeed: ['/feeds/{slug}'], perEpisode: ['/feeds/{slug}/episodes/{id}'], episodesPerFeed: 1 };

test('expands per-feed and per-episode routes', () => {
  const r = expandRoutes(routes, [{ slug: 'a', feedType: 'rss', episodes: ['e1', 'e2'] }]);
  assert.deepEqual(r, ['/', '/feeds/a', '/feeds/a/episodes/e1']);
});

test('recents feeds get no per-episode routes', () => {
  const r = expandRoutes(routes, [{ slug: 'r', feedType: 'recents', episodes: ['e1'] }]);
  assert.deepEqual(r, ['/', '/feeds/r']);
});

test('encodes slugs and ids', () => {
  const r = expandRoutes(routes, [{ slug: 'a b', feedType: 'rss', episodes: ['x/y'] }]);
  assert.deepEqual(r, ['/', '/feeds/a%20b', '/feeds/a%20b/episodes/x%2Fy']);
});
