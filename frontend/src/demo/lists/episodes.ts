import type { Episode } from '../../api/types';
import type { ListHandler } from './index';
import { clampInt, offsetSlice, parseDir, sortBy } from './paging';

type Row = Episode & { createdAt?: string; episodeNumber?: number | null };
type Body = { episodes: Row[]; total: number; limit: number; offset: number };

const SELECTION_CAP = 501;
const canon = (s: string | undefined) => (s === 'completed' ? 'processed' : s);

// Ports list_episodes (api/episodes/core.py) and get_episodes (database/episodes.py).
export const episodesHandler: ListHandler = async (url, load) => {
  const slug = /\/feeds\/([^/]+)\/episodes$/.exec(url.pathname)?.[1];
  const p = url.searchParams;
  const status = canon(p.get('status') ?? 'all');
  // Filter rules are not ported: the crawl must record both the status=all and status=filtered keys.
  const dumpStatus = status === 'filtered' ? 'filtered' : 'all';
  const dump = await load(`/feeds/${slug}/episodes?limit=500&offset=0&sort_by=created_at&sort_dir=desc&status=${dumpStatus}`);
  if (!dump) return null;
  const limit = clampInt(p.get('limit'), 25, 1, 500);
  const selection = (p.get('selection') ?? '').toLowerCase() === 'true';
  const offset = selection ? 0 : clampInt(p.get('offset'), 0, 0, Infinity);
  const search = (p.get('search') ?? '').trim().toLowerCase();
  const sortCol = p.get('sort_by') ?? 'published_at';
  const dir = parseDir(p.get('sort_dir'), 'desc');

  let rows = (dump.body as Body).episodes;
  if (status && status !== 'all' && status !== 'filtered') rows = rows.filter((e) => canon(e.status) === status);
  if (search) rows = rows.filter((e) => (e.title ?? '').toLowerCase().includes(search));
  if (sortCol === 'episode_number') rows = sortBy(rows, 'episodeNumber', dir, { nullsLast: true });
  else if (sortCol === 'published_at') rows = sortBy(rows, (e) => e.published ?? e.createdAt, dir);
  else if (sortCol === 'title') rows = sortBy(rows, 'title', dir);
  else if (sortCol === 'status') rows = sortBy(rows, (e) => canon(e.status), dir);
  else rows = sortBy(rows, 'createdAt', dir);

  const total = rows.length;
  const page = offsetSlice(rows, offset, selection ? SELECTION_CAP : limit);
  const body = selection
    ? {
        episodes: [],
        selection: page.map((e) => ({
          id: e.id, status: e.status, jobState: e.jobState, titleSkipped: e.titleSkipped,
          descriptionSkipped: e.descriptionSkipped, durationSkipped: e.durationSkipped,
        })),
        total, limit, offset, truncated: total > page.length,
      }
    : { episodes: page, total, limit, offset };
  return { status: 200, body };
};
