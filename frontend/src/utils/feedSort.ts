import { Feed } from '../api/types';

export type FeedSortBy = 'recent' | 'title';

// Shared so the dashboard list and the feed-detail prev/next nav read the same
// persisted sort. A rename here can't silently desync the two.
export const DASHBOARD_SORT_KEY = 'dashboardSortBy';
export const DEFAULT_FEED_SORT: FeedSortBy = 'recent';

// Single source of truth for dashboard feed ordering, so the dashboard list and
// the feed-detail prev/next nav can never drift out of sync.
export function sortFeeds(feeds: Feed[], sortBy: FeedSortBy): Feed[] {
  return [...feeds].sort((a, b) => {
    if (sortBy === 'recent') {
      const dateA = a.lastEpisodeDate ? new Date(a.lastEpisodeDate).getTime() : 0;
      const dateB = b.lastEpisodeDate ? new Date(b.lastEpisodeDate).getTime() : 0;
      return dateB - dateA;
    }
    return a.title.localeCompare(b.title, undefined, { sensitivity: 'base' });
  });
}
