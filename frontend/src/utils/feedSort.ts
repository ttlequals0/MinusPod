import { Feed } from '../api/types';
import { feedDisplayTitle } from './feedTitle';

export type FeedSortBy = 'recent' | 'title';

// Shared so the dashboard list and the feed-detail prev/next nav read the same
// persisted sort. A rename here can't silently desync the two.
export const DASHBOARD_SORT_KEY = 'dashboardSortBy';
export const DEFAULT_FEED_SORT: FeedSortBy = 'recent';

// Direction each sort reads as, shared by the dashboard's server-side sort
// params and sortFeeds below so the two orderings stay identical.
export function feedSortDirection(sortBy: FeedSortBy): 'asc' | 'desc' {
  return sortBy === 'title' ? 'asc' : 'desc';
}

// Mirrors the server's feed ordering (GET /feeds?sortBy=), slug tiebreak
// included, so the feed-detail prev/next nav walks the dashboard's sequence.
export function sortFeeds(feeds: Feed[], sortBy: FeedSortBy): Feed[] {
  return [...feeds].sort((a, b) => {
    if (sortBy === 'recent') {
      const dateA = a.lastEpisodeDate ? new Date(a.lastEpisodeDate).getTime() : 0;
      const dateB = b.lastEpisodeDate ? new Date(b.lastEpisodeDate).getTime() : 0;
      if (dateA !== dateB) return dateB - dateA;
      return a.slug.localeCompare(b.slug);
    }
    const byTitle = feedDisplayTitle(a).localeCompare(
      feedDisplayTitle(b), undefined, { sensitivity: 'base' });
    return byTitle !== 0 ? byTitle : a.slug.localeCompare(b.slug);
  });
}
