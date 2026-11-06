import type { DownloadHistoryEntry } from '../../api/types';
import type { ListHandler } from './index';
import { clampInt, pageSlice, sortBy } from './paging';
import { loadAllPages } from './pagedDump';

type Body = { downloads: DownloadHistoryEntry[] };
type Row = DownloadHistoryEntry;

const lower = (s: string | null | undefined) => (s ?? '').toLowerCase();
const SORT: Record<string, (r: Row) => unknown> = {
  last_seen_at: (r) => r.lastSeenAt,
  podcast: (r) => lower(r.podcastTitle || r.podcastSlug),
  client_ip: (r) => r.clientIp,
  location: (r) => lower(`${r.country ?? ''}, ${r.city ?? ''}`),
  subscriber: (r) => lower(r.subscriberLabel),
};

// Ports list_downloads (database/downloads.py) and get_download_history (api/history.py).
export const downloadsHandler: ListHandler = async (url, load) => {
  const all = await loadAllPages<Body, Row>(
    '/history/downloads?limit=100&page=1&sortBy=last_seen_at&sortDir=desc', load, (b) => b.downloads);
  if (!all) return null;
  const p = url.searchParams;
  const limit = clampInt(p.get('limit'), 50, 1, 100);
  const page = clampInt(p.get('page'), 1, 1, Infinity);
  const slug = p.get('podcastSlug');
  const key = p.get('subscriberKeyId');
  const since = p.get('since')?.slice(0, 10);
  const until = p.get('until')?.slice(0, 10);
  const search = (p.get('search') ?? '').trim().toLowerCase();
  let rows = all.rows;
  if (slug) rows = rows.filter((r) => r.podcastSlug === slug);
  if (key) rows = rows.filter((r) => r.subscriberKeyId === key);
  if (since) rows = rows.filter((r) => r.lastSeenAt.slice(0, 10) >= since);
  if (until) rows = rows.filter((r) => r.lastSeenAt.slice(0, 10) <= until);
  if (search) rows = rows.filter((r) => lower(r.clientIp).includes(search) || lower(r.userAgent).includes(search));
  const sortKey = SORT[p.get('sortBy') ?? ''] ?? SORT.last_seen_at;
  // id desc first so the stable sort keeps it as the tie-break in either direction.
  rows = sortBy(sortBy(rows, 'id', 'desc'), sortKey, (p.get('sortDir') ?? 'desc').toLowerCase() === 'asc' ? 'asc' : 'desc');
  return { status: 200, body: { ...all.body, downloads: pageSlice(rows, page, limit), total: rows.length, page, limit } };
};
