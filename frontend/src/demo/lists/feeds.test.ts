/* eslint-disable @typescript-eslint/no-explicit-any */
import { test, expect } from 'vitest';
import { feedsHandler } from './feeds';

const ep = (id: string) => ({ id });
const feeds = [
  { slug: 'a', title: 'Zeta', lastEpisodeDate: '2026-01-01', latestEpisodes: [ep('1'), ep('2'), ep('3'), ep('4')] },
  { slug: 'b', title: 'alpha', lastEpisodeDate: '2026-03-01', latestEpisodes: [ep('5')] },
  { slug: 'd', title: 'Aardvark', titleOverride: 'Yak', lastEpisodeDate: '2026-02-01', latestEpisodes: [] },
  { slug: 'c', title: 'Mid', lastEpisodeDate: null, latestEpisodes: [] },
];
const dump = { status: 200, body: { feeds, total: 4, totalPages: 1, page: 1, limit: 50, offset: 0, lastRefreshCompletedAt: 'T' } };
const load = async () => dump;
const url = (q: string) => new URL(`http://x/api/v1/feeds?${q}`);
const body = async (q: string) => (await feedsHandler(url(q), load))!.body as any;

test('bare request is unbounded and drops latestEpisodes', async () => {
  const b = await body('');
  expect(b.limit).toBe(4);
  expect(b.feeds.every((f: any) => !('latestEpisodes' in f))).toBe(true);
  expect(b.lastRefreshCompletedAt).toBe('T');
});
test('includeLatestEpisodes trims to episodesPerFeed', async () => {
  const b = await body('includeLatestEpisodes=true&episodesPerFeed=2');
  expect(b.feeds[0].latestEpisodes).toHaveLength(2);
});
test('sortBy recent is last episode date desc with missing dates last', async () => {
  const b = await body('sortBy=recent');
  expect(b.feeds.map((f: any) => f.slug)).toEqual(['b', 'd', 'a', 'c']);
});
test('sortBy title is case-insensitive asc, uses titleOverride, and pages', async () => {
  const b = await body('sortBy=title&limit=2&page=2');
  expect(b.feeds.map((f: any) => f.slug)).toEqual(['d', 'a']);
  expect(b).toMatchObject({ total: 4, totalPages: 2, page: 2, limit: 2, offset: 2 });
});
