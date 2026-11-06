/* eslint-disable @typescript-eslint/no-explicit-any */
import { test, expect } from 'vitest';
import { historyHandler } from './history';

const rows = [
  { id: 1, podcastSlug: 'a', status: 'completed', processedAt: '2026-01-01', adsDetected: 3 },
  { id: 2, podcastSlug: 'b', status: 'failed', processedAt: '2026-01-03', adsDetected: 1 },
  { id: 3, podcastSlug: 'a', status: 'completed', processedAt: '2026-01-02', adsDetected: 2 },
];
const load = async () => ({ status: 200, body: { history: rows, total: 3, totalPages: 1, page: 1, limit: 100, offset: 0 } });
const url = (q: string) => new URL(`http://x/api/v1/history?${q}`);
const body = async (q: string) => (await historyHandler(url(q), load))!.body as any;
const ids = (b: any) => b.history.map((r: any) => r.id);

test('default sort is processed_at desc', async () => {
  expect(ids(await body(''))).toEqual([2, 3, 1]);
});
test('status filter accepts only completed or failed', async () => {
  expect(ids(await body('status=failed'))).toEqual([2]);
  expect(ids(await body('status=bogus'))).toEqual([2, 3, 1]);
});
test('podcast_slug is exact and sort_dir other than desc is asc', async () => {
  expect(ids(await body('podcast_slug=a&sort_by=ads_detected&sort_dir=asc'))).toEqual([3, 1]);
});
test('page and limit recompute the envelope', async () => {
  const b = await body('limit=2&page=2');
  expect(ids(b)).toEqual([1]);
  expect(b).toMatchObject({ total: 3, totalPages: 2, page: 2, limit: 2, offset: 2 });
});
test('offset without page slices by offset and non-integer page is absent', async () => {
  const many = Array.from({ length: 20 }, (_, i) => ({ id: i, status: 'completed', processedAt: String(100 - i).padStart(3, '0') }));
  const l = async () => ({ status: 200, body: { history: many } });
  const b = (await historyHandler(url('offset=5&limit=10&page=abc'), l))!.body as any;
  expect(ids(b)).toEqual([5, 6, 7, 8, 9, 10, 11, 12, 13, 14]);
  expect(b).toMatchObject({ offset: 5, page: 1, total: 20 });
});
test('full pages are followed until a short page', async () => {
  const full = Array.from({ length: 100 }, (_, i) => ({ id: i, status: 'completed', processedAt: String(i) }));
  const keys: string[] = [];
  const paged = async (key: string) => {
    keys.push(key);
    return key.includes('page=1&') ? { status: 200, body: { history: full } } : key.includes('page=2&') ? { status: 200, body: { history: [{ id: 999, status: 'completed' }] } } : null;
  };
  const r = await historyHandler(url('limit=100'), paged);
  expect((r!.body as any).total).toBe(101);
  expect(keys).toEqual([
    '/history?limit=100&page=1&sort_by=processed_at&sort_dir=desc',
    '/history?limit=100&page=2&sort_by=processed_at&sort_dir=desc',
  ]);
});
