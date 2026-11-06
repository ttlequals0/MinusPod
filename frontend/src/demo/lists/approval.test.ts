/* eslint-disable @typescript-eslint/no-explicit-any */
import { test, expect } from 'vitest';
import { awaitingApprovalHandler, heldHandler } from './approval';

const rows = [{ slug: 'a', episodeId: '1' }, { slug: 'b', episodeId: '2' }, { slug: 'a', episodeId: '3' }];
const load = async () => ({ status: 200, body: { count: 3, episodes: rows } });
const get = async (h: typeof heldHandler, q: string) => (await h(new URL(`http://x/api/v1/episodes/x?${q}`), load))!.body as any;

test('slug filters awaiting and count is the filtered total', async () => {
  const b = await get(awaitingApprovalHandler, 'slug=a');
  expect(b.episodes.map((e: any) => e.episodeId)).toEqual(['1', '3']);
  expect(b.count).toBe(2);
});
test('limit and offset slice while count stays the total', async () => {
  const b = await get(heldHandler, 'limit=1&offset=1');
  expect(b.episodes.map((e: any) => e.episodeId)).toEqual(['2']);
  expect(b.count).toBe(3);
});
test('held ignores slug', async () => {
  expect((await get(heldHandler, 'slug=a')).episodes).toHaveLength(3);
});
