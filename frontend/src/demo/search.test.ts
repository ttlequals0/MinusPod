import MiniSearch from 'minisearch';
import { test, expect } from 'vitest';
import { __setIndexForTests, runSearch, searchHandler, searchStatsHandler } from './search';

const options = { fields: ['title', 'body', 'metadata'], storeFields: ['type'] };
const ms = new MiniSearch(options);
ms.addAll([
  { id: 'show:a', type: 'show', title: 'Darknet Diaries', body: 'True stories from the dark side' },
  { id: 'episode:a/1', type: 'episode', title: 'The Big Heist of the century', metadata: 'A heist' },
  { id: 'pattern:7', type: 'pattern', title: 'Acme', body: 'brought to you by acme' },
]);
__setIndexForTests({ version: 1, options, index: ms.toJSON(), docs: {
  'show:a': { type: 'show', slug: 'a', title: 'Darknet Diaries' },
  'episode:a/1': { type: 'episode', feedSlug: 'a', feedTitle: 'Darknet Diaries', episodeId: '1', title: 'The Big Heist of the century', status: 'completed', publishDate: '2026-11-01T00:00:00Z' },
  'pattern:7': { type: 'pattern', scope: 'global', sponsor: 'Acme', title: 'Acme' },
} });

test('two non-adjacent words match an episode title', async () => {
  const r = await runSearch('heist century', 50, null);
  expect(r.episodes.map(e => e.episodeId)).toEqual(['1']);
  expect(r.shows).toEqual([]);
});
test('groups filter and short queries return empty groups', async () => {
  expect((await runSearch('acme', 50, ['patterns'])).patterns[0]).toMatchObject({ id: '7', scope: 'global', sponsor: 'Acme' });
  expect(await runSearch('a', 50, null)).toMatchObject({ shows: [], episodes: [], transcripts: [], patterns: [], sponsors: [] });
});
test('titles are marked', async () => {
  const r = await runSearch('heist', 50, null);
  expect(r.episodes[0].titleSnippet).toBe('The Big <mark>Heist</mark> of the century');
});
test('stats count docs by type', async () => {
  const res = await searchStatsHandler();
  expect(await res.json()).toEqual({ stats: { podcast: 1, episode: 1, pattern: 1, total: 3 } });
});
test('unknown group is a 400', async () => {
  const res = await searchHandler({ request: new Request('http://x/api/v1/search?q=acme&groups=bogus') });
  expect(res.status).toBe(400);
  expect(await res.json()).toEqual({ error: 'Unknown group: bogus' });
});
