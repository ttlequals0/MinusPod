import { test, expect } from 'vitest';
import { episodesHandler } from './episodes';

type Row = { id: string };
type Body = { episodes: Row[]; selection: Row[]; total: number; limit: number; offset: number };
const dump = { status: 200, body: { episodes: [
  { id: 'a', title: 'Alpha show', status: 'processed', published: '2026-11-01T00:00:00Z', createdAt: '2026-11-01T00:00:00Z', episodeNumber: 2 },
  { id: 'b', title: 'Beta', status: 'failed', published: null, createdAt: '2026-11-03T00:00:00Z', episodeNumber: null },
  { id: 'c', title: 'alpha two', status: 'processed', published: '2026-11-02T00:00:00Z', createdAt: '2026-11-02T00:00:00Z', episodeNumber: 1 },
], total: 3, limit: 500, offset: 0 } };
const load = async () => dump;
const url = (q: string) => new URL(`http://x/api/v1/feeds/s/episodes?${q}`);

test('status completed aliases processed and search is case-insensitive', async () => {
  const r = await episodesHandler(url('status=completed&search=ALPHA'), load);
  expect((r!.body as Body).episodes.map((e) => e.id)).toEqual(['c', 'a']);
  expect((r!.body as Body).total).toBe(2);
});
test('published_at sort coalesces to createdAt and paginates', async () => {
  const r = await episodesHandler(url('sort_by=published_at&sort_dir=desc&limit=2&offset=0'), load);
  expect((r!.body as Body).episodes.map((e) => e.id)).toEqual(['b', 'c']);
  expect((r!.body as Body).limit).toBe(2);
});
test('published_at sort reads published, not createdAt', async () => {
  const rows = [
    { id: 'x', title: 'x', status: 'processed', published: '2026-12-01T00:00:00Z', createdAt: '2026-01-01T00:00:00Z' },
    { id: 'y', title: 'y', status: 'processed', published: '2026-02-01T00:00:00Z', createdAt: '2026-06-01T00:00:00Z' },
  ];
  const r = await episodesHandler(url('sort_by=published_at&sort_dir=desc'), async () => ({ status: 200, body: { episodes: rows } }));
  expect((r!.body as Body).episodes.map((e) => e.id)).toEqual(['x', 'y']);
});
test('episode_number sorts nulls last', async () => {
  const r = await episodesHandler(url('sort_by=episode_number&sort_dir=asc'), load);
  expect((r!.body as Body).episodes.map((e) => e.id)).toEqual(['c', 'a', 'b']);
});
test('selection returns an empty episodes list and the trimmed selection', async () => {
  const r = await episodesHandler(url('selection=true&status=failed&offset=5'), load);
  const b = r!.body as Body;
  expect(b.episodes).toEqual([]);
  expect(b.selection.map((e) => e.id)).toEqual(['b']);
  expect(b.offset).toBe(0);
});
test('missing dump returns null', async () => {
  expect(await episodesHandler(url(''), async () => null)).toBeNull();
});
