import { test, expect } from 'vitest';
import { detectionsHandler } from './detections';

type Body = { detections: { id: number }[]; total: number; counts: { pendingByHoldReason: unknown }; cutSummary: { byCategory: unknown } };

const d = (o: Record<string, unknown>) => ({
  feedSlug: 'a', feedTitle: 'Feed A', episodeId: 'e', publishDate: '2026-01-01', start: 0, end: 10,
  confidence: 0.5, sponsor: null, reason: null, category: 'sponsor', actionApplied: 'remove',
  reviewerMoved: false, holdReason: null, status: 'accepted', resolution: 'unresolved', ...o,
});
const items = [
  d({ id: 1, status: 'accepted', sponsor: 'Acme', end: 20, publishDate: '2026-01-03' }),
  d({ id: 2, status: 'pending', holdReason: 'low_confidence', confidence: null, publishDate: '2026-01-02' }),
  d({ id: 3, status: 'rejected', feedSlug: 'b', feedTitle: 'Feed B', reason: 'acme mention', category: null }),
  d({ id: 4, status: 'rejected', actionApplied: 'keep', publishDate: '2026-01-04' }),
  d({ id: 5, status: 'accepted', resolution: 'confirmed', feedSlug: 'b', sponsor: 'Beta', category: 'intro', confidence: 0.9 }),
];
const load = async () => ({ status: 200, body: { detections: items, total: 5, page: 1, totalPages: 1, limit: 100 } });
const body = async (q: string) => (await detectionsHandler(new URL(`http://x/api/v1/detections?${q}`), load))!.body as Body;
const ids = (b: Body) => b.detections.map((r) => r.id);

test('default status needs_review excludes keep-like and resolved items', async () => {
  expect(ids(await body(''))).toEqual([2, 3]);
});
test('counts overview is pre-filter but pendingByHoldReason follows status and feed', async () => {
  const b = await body('status=pending&feed=a&q=zzz');
  expect(b.counts).toMatchObject({ total: 5, needsReview: 2, pending: 1, rejected: 2, accepted: 2, confirmed: 1, dismissed: 0 });
  expect(b.counts.pendingByHoldReason).toEqual({ low_confidence: 1 });
  expect(b.total).toBe(0);
});
test('q matches sponsor or reason and cutSummary covers accepted before category', async () => {
  const b = await body('status=all&q=acme&category=intro');
  expect(ids(b)).toEqual([]);
  expect(b.cutSummary).toMatchObject({ count: 1, durationSeconds: 20, distinctSponsors: 1, distinctPodcasts: 1 });
  expect(b.cutSummary.byCategory).toMatchObject({ sponsor: 1, intro: 0, none: 0 });
});
test('confidence sort keeps null confidence last in both directions', async () => {
  expect(ids(await body('status=all&sort=confidence&order=desc'))).toEqual([5, 4, 1, 3, 2]);
  expect(ids(await body('status=all&sort=confidence&order=asc'))).toEqual([3, 1, 4, 5, 2]);
});
test('page is clamped to the last page', async () => {
  const b = await body('status=all&limit=2&page=9');
  expect(b).toMatchObject({ page: 3, totalPages: 3, total: 5, limit: 2 });
  expect(ids(b)).toHaveLength(1);
});
