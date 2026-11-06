/* eslint-disable @typescript-eslint/no-explicit-any */
import { test, expect } from 'vitest';
import { downloadsHandler } from './downloads';

const rows = [
  { id: 1, podcastSlug: 'a', podcastTitle: 'Zed', clientIp: '1.1.1.1', userAgent: 'Overcast', subscriberKeyId: 'k1', lastSeenAt: '2026-05-01T10:00:00Z', country: 'US', city: 'Austin', subscriberLabel: null },
  { id: 2, podcastSlug: 'b', podcastTitle: 'alpha', clientIp: '2.2.2.2', userAgent: 'Spotify', subscriberKeyId: null, lastSeenAt: '2026-05-02T23:59:00Z', country: 'DE', city: 'Berlin', subscriberLabel: 'Bob' },
  { id: 3, podcastSlug: 'a', podcastTitle: 'Zed', clientIp: '3.3.3.3', userAgent: 'overcast pro', subscriberKeyId: 'k1', lastSeenAt: '2026-05-02T01:00:00Z', country: null, city: null, subscriberLabel: null },
];
const load = async () => ({ status: 200, body: { downloads: rows, total: 3, page: 1, limit: 100 } });
const body = async (q: string) => (await downloadsHandler(new URL(`http://x/api/v1/history/downloads?${q}`), load))!.body as any;
const ids = (b: any) => b.downloads.map((r: any) => r.id);

test('since and until compare the day inclusively', async () => {
  expect(ids(await body('since=2026-05-02T09:00:00Z'))).toEqual([2, 3]);
  expect(ids(await body('until=2026-05-01'))).toEqual([1]);
});
test('search matches ip or user agent case-insensitively', async () => {
  expect(ids(await body('search=OVERCAST'))).toEqual([3, 1]);
  expect(ids(await body('search=2.2'))).toEqual([2]);
});
test('podcastSlug and subscriberKeyId are exact', async () => {
  expect(ids(await body('podcastSlug=a&subscriberKeyId=k1'))).toEqual([3, 1]);
});
test('podcast sort is case-insensitive and ties break by id desc', async () => {
  expect(ids(await body('sortBy=podcast&sortDir=asc'))).toEqual([2, 3, 1]);
});
test('envelope is downloads, total, page, limit', async () => {
  expect(await body('limit=1&page=2')).toEqual({ downloads: [rows[2]], total: 3, page: 2, limit: 1 });
});
