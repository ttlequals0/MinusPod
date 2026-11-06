import type { Feed } from '../../api/types';
import type { ListHandler } from './index';
import { clampInt, pageSlice, sortBy } from './paging';

type Body = { feeds: Feed[] } & Record<string, unknown>;

// Ports list_feeds and _sort_podcasts (api/feeds.py).
export const feedsHandler: ListHandler = async (url, load) => {
  const dump = await load('/feeds?includeLatestEpisodes=true&episodesPerFeed=20');
  if (!dump) return null;
  const p = url.searchParams;
  const sort = p.get('sortBy');
  const dir = p.get('sortDir') ?? (sort === 'title' ? 'asc' : 'desc');
  let feeds = (dump.body as Body).feeds;
  if (sort === 'title') feeds = sortBy(feeds, (f) => [(f.titleOverride || f.title || f.slug).toLowerCase(), f.slug], dir as 'asc' | 'desc');
  else if (sort === 'recent') feeds = sortBy(feeds, (f) => [f.lastEpisodeDate || '', f.slug], dir as 'asc' | 'desc');

  const total = feeds.length;
  const bounded = p.has('limit') || p.has('page');
  const limit = bounded ? clampInt(p.get('limit') || 50, 50, 1, 200) : total;
  const page = bounded ? clampInt(p.get('page'), 1, 1, Infinity) : 1;
  const perFeed = clampInt(p.get('episodesPerFeed'), 3, 1, 20);
  const withLatest = (p.get('includeLatestEpisodes') ?? '').toLowerCase() === 'true';

  const rows = (bounded ? pageSlice(feeds, page, limit) : feeds).map(({ latestEpisodes, ...rest }) =>
    withLatest ? { ...rest, latestEpisodes: (latestEpisodes ?? []).slice(0, perFeed) } : rest);
  return {
    status: 200,
    body: { ...dump.body as Body, feeds: rows, total, totalPages: total && limit ? Math.ceil(total / limit) : 1, page, limit, offset: (page - 1) * limit },
  };
};
