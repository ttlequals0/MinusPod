import type { ReviewDetection, DetectionCounts, CutSummary } from '../../api/detections';
import type { ListHandler } from './index';
import { clampInt, pageSlice, sortBy } from './paging';
import { loadAllPages } from './pagedDump';

type Body = { detections: ReviewDetection[] };
type Item = ReviewDetection;

const CATEGORIES = ['sponsor', 'cross_promo', 'self_promo', 'interaction', 'intro', 'outro', 'recap'];
const UNSET = 'none';
const keepLike = (a: string | null) => a === 'keep' || a === 'mark';
const awaitsDecision = (i: Item) =>
  (i.status === 'pending' || i.status === 'rejected') && i.resolution === 'unresolved' && !keepLike(i.actionApplied);

function summarize(items: Item[]): Omit<DetectionCounts, 'pendingByHoldReason'> {
  const c = { total: items.length, needsReview: 0, pending: 0, rejected: 0, accepted: 0, confirmed: 0, dismissed: 0 };
  for (const i of items) {
    c[i.status]++;
    if (i.resolution === 'confirmed') c.confirmed++;
    else if (i.resolution === 'dismissed') c.dismissed++;
    else if (awaitsDecision(i)) c.needsReview++;
  }
  return c;
}

function summarizeCut(items: Item[]): CutSummary {
  const byCategory: Record<string, number> = Object.fromEntries([...CATEGORIES, UNSET].map((c) => [c, 0]));
  const sponsors = new Set<string>();
  const podcasts = new Set<string>();
  let duration = 0;
  for (const i of items) {
    const cat = i.category || UNSET;
    byCategory[cat] = (byCategory[cat] ?? 0) + 1;
    if (i.end > i.start) duration += i.end - i.start;
    const sponsor = (i.sponsor ?? '').trim().toLowerCase();
    if (sponsor) sponsors.add(sponsor);
    if (i.feedSlug) podcasts.add(i.feedSlug);
  }
  return { count: items.length, durationSeconds: Math.round(duration * 1000) / 1000, byCategory, distinctSponsors: sponsors.size, distinctPodcasts: podcasts.size };
}

type Filter = { status?: string; feed?: string | null; q?: string | null; category?: string | null; reviewer?: string | null; holdReason?: string | null };

// Ports filter_detections (detection_review.py).
function filter(items: Item[], f: Filter): Item[] {
  let out = items;
  if (f.status === 'needs_review') out = out.filter(awaitsDecision);
  else if (f.status === 'pending' || f.status === 'rejected' || f.status === 'accepted') out = out.filter((i) => i.status === f.status);
  if (f.category === UNSET) out = out.filter((i) => !i.category);
  else if (f.category) out = out.filter((i) => i.category === f.category);
  if (f.holdReason) out = out.filter((i) => i.holdReason === f.holdReason);
  if (f.reviewer === 'adjusted') out = out.filter((i) => i.reviewerMoved);
  else if (f.reviewer === 'unadjusted') out = out.filter((i) => !i.reviewerMoved);
  if (f.feed) out = out.filter((i) => i.feedSlug === f.feed);
  if (f.q) {
    const n = f.q.toLowerCase();
    out = out.filter((i) => (i.sponsor ?? '').toLowerCase().includes(n) || (i.reason ?? '').toLowerCase().includes(n));
  }
  return out;
}

// Ports sort_detections; null confidences stay last in both directions.
function sort(items: Item[], by: string, dir: 'asc' | 'desc'): Item[] {
  const tail = (i: Item) => [i.publishDate || '', i.start || 0];
  if (by === 'podcast') return sortBy(items, (i) => [(i.feedTitle || '').toLowerCase(), ...tail(i)], dir);
  if (by !== 'confidence') return sortBy(items, tail, dir);
  const rank = (i: Item) => [i.confidence ?? 0, ...tail(i)];
  return [...sortBy(items.filter((i) => i.confidence !== null), rank, dir), ...sortBy(items.filter((i) => i.confidence === null), rank, dir)];
}

// Ports list_detections (api/detections.py).
export const detectionsHandler: ListHandler = async (url, load) => {
  const all = await loadAllPages<Body, Item>('/detections?limit=100&page=1&status=all&sort=date&order=desc', load, (b) => b.detections);
  if (!all) return null;
  const p = url.searchParams;
  const limit = clampInt(p.get('limit'), 20, 1, 100);
  const status = p.get('status') ?? 'needs_review';
  const byFeed = filter(all.rows, { status, feed: p.get('feed') });
  const pendingByHoldReason: DetectionCounts['pendingByHoldReason'] = {};
  for (const i of byFeed) if (i.holdReason) pendingByHoldReason[i.holdReason] = (pendingByHoldReason[i.holdReason] ?? 0) + 1;
  let items = filter(byFeed, { status: 'all', q: p.get('q'), reviewer: p.get('reviewer'), holdReason: p.get('holdReason') });
  const cutSummary = summarizeCut(items.filter((i) => i.status === 'accepted'));
  items = filter(items, { status: 'all', category: p.get('category') });
  items = sort(items, p.get('sort') ?? 'date', p.get('order') === 'asc' ? 'asc' : 'desc');
  const total = items.length;
  const totalPages = total ? Math.ceil(total / limit) : 1;
  const page = Math.min(clampInt(p.get('page'), 1, 1, Infinity), totalPages);
  return {
    status: 200,
    body: { ...all.body, detections: pageSlice(items, page, limit), total, page, totalPages, limit, counts: { ...summarize(all.rows), pendingByHoldReason }, cutSummary },
  };
};
