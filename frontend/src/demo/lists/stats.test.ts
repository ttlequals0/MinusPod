/* eslint-disable @typescript-eslint/no-explicit-any */
import { test, expect } from 'vitest';
import { modelUsageHandler, episodeCostsHandler } from './stats';

const usage = [
  { provider: 'a', model: 'm1', knownCostUsd: '9.5', inputTokens: 100, cacheReadTokens: 50, calls: 1 },
  { provider: 'a', model: 'm2', knownCostUsd: '10.25', inputTokens: 0, cacheReadTokens: 0, calls: 2 },
  { provider: 'b', model: 'm1', knownCostUsd: '1', inputTokens: 10, cacheReadTokens: 1, calls: 3 },
];
const usageLoad = async () => ({ status: 200, body: { items: usage, total: 3, totalPages: 1, page: 1, limit: 100 } });
const mu = async (q: string) => (await modelUsageHandler(new URL(`http://x/api/v1/stats/model-usage?${q}`), usageLoad))!.body as any;

test('model-usage default sort is numeric knownCostUsd desc', async () => {
  expect((await mu('')).items.map((r: any) => r.model + r.provider)).toEqual(['m2a', 'm1a', 'm1b']);
});
test('model-usage provider and model filters are exact', async () => {
  expect((await mu('provider=a&model=m1')).total).toBe(1);
});
test('model-usage cacheHitRate sort treats zero input as null', async () => {
  const b = await mu('sortBy=cacheHitRate&sortDir=desc');
  expect(b.items.map((r: any) => r.calls)).toEqual([1, 3, 2]);
});
test('model-usage ignores from and to and recomputes paging', async () => {
  const b = await mu('from=2020-01-01&limit=2&page=2');
  expect(b).toMatchObject({ total: 3, totalPages: 2, page: 2, limit: 2 });
  expect(b.items).toHaveLength(1);
});

const costs = [
  { podcastSlug: 'a', episodeTitle: 'x', cumulativeCostUsd: '2', lastActivityAt: '2026-01-01', modelsUsed: ['m1'] },
  { podcastSlug: 'b', episodeTitle: 'y', cumulativeCostUsd: '10', lastActivityAt: '2026-01-02', modelsUsed: ['m2'] },
];
const costLoad = async () => ({ status: 200, body: { items: costs, total: 2, totalPages: 1, page: 1, limit: 100 } });
const ec = async (q: string) => (await episodeCostsHandler(new URL(`http://x/api/v1/stats/episode-costs?${q}`), costLoad))!.body as any;

test('episode-costs default sort is lastActivityAt desc and cost sort is numeric', async () => {
  expect((await ec('')).items.map((r: any) => r.podcastSlug)).toEqual(['b', 'a']);
  expect((await ec('sortBy=cumulativeCostUsd&sortDir=asc')).items.map((r: any) => r.podcastSlug)).toEqual(['a', 'b']);
});
test('episode-costs podcastSlug filter is exact', async () => {
  expect((await ec('podcastSlug=a')).total).toBe(1);
});
