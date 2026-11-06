import type { ProcessingHistoryEntry } from '../../api/types';
import type { ListHandler } from './index';
import { clampInt, offsetSlice, sortBy } from './paging';
import { loadAllPages } from './pagedDump';

type Body = { history: ProcessingHistoryEntry[] };

const SORT: Record<string, keyof ProcessingHistoryEntry> = {
  processed_at: 'processedAt', podcast_title: 'podcastTitle', episode_title: 'episodeTitle',
  processing_duration_seconds: 'processingDurationSeconds', ads_detected: 'adsDetected',
  reprocess_number: 'reprocessNumber', status: 'status', llm_cost: 'llmCost', app_version: 'appVersion',
};

// Ports get_processing_history (database/stats.py) and get_processing_history (api/history.py).
export const historyHandler: ListHandler = async (url, load) => {
  const all = await loadAllPages<Body, ProcessingHistoryEntry>(
    '/history?limit=100&page=1&sort_by=processed_at&sort_dir=desc', load, (b) => b.history);
  if (!all) return null;
  const p = url.searchParams;
  const limit = clampInt(p.get('limit'), 50, 1, 100);
  const hasPage = /^\s*[+-]?\d+\s*$/.test(p.get('page') ?? '');
  const offset = hasPage ? (clampInt(p.get('page'), 1, 1, Infinity) - 1) * limit : clampInt(p.get('offset'), 0, 0, Infinity);
  const page = Math.floor(offset / limit) + 1;
  const status = p.get('status');
  const slug = p.get('podcast_slug');
  let rows = all.rows;
  if (status === 'completed' || status === 'failed') rows = rows.filter((r) => r.status === status);
  if (slug) rows = rows.filter((r) => r.podcastSlug === slug);
  const col = SORT[p.get('sort_by') ?? ''] ?? 'processedAt';
  rows = sortBy(rows, col, (p.get('sort_dir') ?? 'desc').toLowerCase() === 'desc' ? 'desc' : 'asc');
  const total = rows.length;
  return {
    status: 200,
    body: { ...all.body, history: offsetSlice(rows, offset, limit), total, totalPages: total > 0 ? Math.ceil(total / limit) : 1, page, limit, offset },
  };
};
