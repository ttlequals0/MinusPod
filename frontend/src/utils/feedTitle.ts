import type { Feed } from '../api/types';

/**
 * Title to show in the UI: the user's per-feed override when set, else the source feed title (#375). Use it
 * wherever a feed name is rendered so a custom title is consistent.
 */
export function feedDisplayTitle(feed: Pick<Feed, 'title' | 'titleOverride'>): string {
  return feed.titleOverride || feed.title;
}

// Local and recents feeds have no upstream RSS to refresh or fail on.
export function feedHasUpstream(feed: { feedType?: string }): boolean {
  return feed.feedType !== 'local' && feed.feedType !== 'recents';
}

// Delete-confirmation copy for a podcast with an episode actively processing
// (#745). Only 'processing' has a run to stop; a 'pending' (queued) episode
// is just removed along with the podcast, so it's excluded on purpose.
export function deleteStopsProcessingMessage(processingCount: number): string | null {
  if (processingCount <= 0) return null;
  return processingCount === 1
    ? 'An episode is processing right now. Deleting this podcast will stop it.'
    : `${processingCount} episodes are processing right now. Deleting this podcast will stop them.`;
}

// Tooltip for a feed paused by Settings > Global Defaults > dormant feeds.
export const DORMANT_TITLE =
  'No podcast app has fetched this feed recently, so new episodes are not auto-processed. '
  + 'Episodes still process when played, and auto-process resumes on the next feed fetch.';
