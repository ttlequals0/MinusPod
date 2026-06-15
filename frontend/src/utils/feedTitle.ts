import type { Feed } from '../api/types';

/**
 * Title to show in the UI: the user's per-feed override when set, otherwise
 * the source feed title (#375). Use everywhere a feed name is rendered so the
 * custom title is consistent across the app.
 */
export function feedDisplayTitle(feed: Pick<Feed, 'title' | 'titleOverride'>): string {
  return feed.titleOverride || feed.title;
}
