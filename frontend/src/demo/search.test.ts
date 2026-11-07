import MiniSearch from 'minisearch';
import { test, expect, vi } from 'vitest';
import { __failIndexForTests, __resetIndexForTests, __setIndexForTests, runSearch, searchHandler, searchStatsHandler } from './search';
import { __setIndexForTests as setFixtureIndex, __setLoaderForTests } from './fixtures';

const options = { fields: ['title', 'body', 'metadata'], storeFields: ['type'] };
const ms = new MiniSearch(options);
ms.addAll([
  { id: 'show:a', type: 'show', title: 'Darknet Diaries', body: 'True stories from the dark side' },
  { id: 'episode:a/1', type: 'episode', title: 'The Big Heist of the century', metadata: 'A heist' },
  { id: 'episode:a/2', type: 'episode', title: 'Bank heist recap', metadata: 'Vaults' },
  { id: 'transcript:a/1', type: 'transcript', title: 'The Big Heist of the century', body: 'the vault door was open' },
  { id: 'pattern:7', type: 'pattern', title: 'Acme', body: 'brought to you by acme' },
]);
__setIndexForTests({ version: 1, options, index: ms.toJSON(), docs: {
  'show:a': { type: 'show', slug: 'a', title: 'Darknet Diaries' },
  'episode:a/1': { type: 'episode', feedSlug: 'a', feedTitle: 'Darknet Diaries', episodeId: '1', title: 'The Big Heist of the century', status: 'completed', publishDate: '2026-11-01T00:00:00Z' },
  'episode:a/2': { type: 'episode', feedSlug: 'a', feedTitle: 'Darknet Diaries', episodeId: '2', title: 'Bank heist recap', status: 'completed', publishDate: '2026-11-02T00:00:00Z' },
  'transcript:a/1': { type: 'transcript', feedSlug: 'a', episodeId: '1', title: 'The Big Heist of the century' },
  'pattern:7': { type: 'pattern', scope: 'global', sponsor: 'Acme', title: 'Acme' },
} });

const ids = (r: Awaited<ReturnType<typeof runSearch>>) => r.episodes.map(e => e.episodeId).sort();

test('two non-adjacent words match an episode title', async () => {
  const r = await runSearch('heist century', 50, null);
  expect(ids(r)).toEqual(['1']);
  expect(r.shows).toEqual([]);
});
test('every term must match, with no OR fallback', async () => {
  expect(ids(await runSearch('heist xyzzy', 50, null))).toEqual([]);
});
test('stopwords are dropped', async () => {
  expect(ids(await runSearch('the heist', 50, null))).toEqual(['1', '2']);
});
test('only the last term is a prefix', async () => {
  expect(ids(await runSearch('bank hei', 50, null))).toEqual(['2']);
  expect(ids(await runSearch('ban heist', 50, null))).toEqual([]);
});
test('groups filter and short queries return empty groups', async () => {
  expect((await runSearch('acme', 50, ['patterns'])).patterns[0]).toMatchObject({ id: '7', scope: 'global', sponsor: 'Acme' });
  expect(await runSearch('a', 50, null)).toMatchObject({ shows: [], episodes: [], transcripts: [], patterns: [], sponsors: [] });
});
test('titles are marked', async () => {
  const r = await runSearch('heist', 50, null);
  expect(r.episodes.find(e => e.episodeId === '1')?.titleSnippet).toBe('The Big <mark>Heist</mark> of the century');
});
test('transcript snippets come from the transcript text', async () => {
  setFixtureIndex({ '/feeds/a/episodes/1/original-transcript': 't.json' });
  __setLoaderForTests(async () => ({ status: 200, body: { episodeId: '1', originalTranscript: 'Intro music. Then the vault door was open all night.' } }));
  const r = await runSearch('vault', 50, ['transcripts']);
  expect(r.transcripts).toHaveLength(1);
  expect(r.transcripts[0].snippet).toBe('Intro music. Then the <mark>vault</mark> door was open all night.');
});
test('stats count docs by type', async () => {
  const res = await searchStatsHandler();
  expect(await res.json()).toEqual({ stats: { podcast: 1, episode: 2, pattern: 1, total: 4 } });
});
test('unknown group is a 400', async () => {
  const res = await searchHandler({ request: new Request('http://x/api/v1/search?q=acme&groups=bogus') });
  expect(res.status).toBe(400);
  expect(await res.json()).toEqual({ error: 'Unknown group: bogus' });
});

test('only the first transcript hits load a transcript fixture', async () => {
  const docs: Record<string, unknown> = {};
  const ms2 = new MiniSearch(options);
  for (let n = 0; n < 15; n++) {
    ms2.add({ id: `transcript:b/${n}`, type: 'transcript', title: `Episode ${n}`, body: 'zebra crossing' });
    docs[`transcript:b/${n}`] = { type: 'transcript', feedSlug: 'b', episodeId: String(n), title: `Episode ${n}` };
  }
  __setIndexForTests({ version: 1, options, index: ms2.toJSON(), docs } as never);
  setFixtureIndex(Object.fromEntries(Array.from({ length: 15 }, (_, n) => [`/feeds/b/episodes/${n}/original-transcript`, `t${n}.json`])));
  let loads = 0;
  __setLoaderForTests(async () => { loads++; return { status: 200, body: { originalTranscript: 'a zebra crossing' } }; });
  const r = await runSearch('zebra', 50, ['transcripts']);
  expect(r.transcripts).toHaveLength(15);
  expect(loads).toBe(10);
});

test('a failed index load yields empty 200 responses', async () => {
  __failIndexForTests();
  const res = await searchHandler({ request: new Request('http://x/api/v1/search?q=acme') });
  expect(res.status).toBe(200);
  expect(await res.json()).toEqual({ query: 'acme', shows: [], episodes: [], transcripts: [], patterns: [], sponsors: [] });
  const stats = await searchStatsHandler();
  expect(stats.status).toBe(200);
  expect(await stats.json()).toEqual({ stats: { total: 0 } });
});

test('a failed index load is retried only after the cooldown', async () => {
  __resetIndexForTests();
  vi.useFakeTimers();
  const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 404 });
  vi.stubGlobal('fetch', fetchMock);
  try {
    await searchHandler({ request: new Request('http://x/api/v1/search?q=acme') });
    await searchHandler({ request: new Request('http://x/api/v1/search?q=acme') });
    await searchStatsHandler();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(30_000);
    await searchHandler({ request: new Request('http://x/api/v1/search?q=acme') });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  } finally {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  }
});
