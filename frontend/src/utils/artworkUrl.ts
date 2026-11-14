import { mediaUrl } from './mediaUrl';

/** Feed cover: the API's URL, else the proxy endpoint. */
export function feedArtworkSrc(slug: string, feedArtworkUrl?: string | null): string {
  return mediaUrl({ kind: 'feedArtwork', slug, apiArtworkUrl: feedArtworkUrl })!;
}

/**
 * Episode cover, falling back to the feed's. Publisher URLs are never rendered directly: hotlink protection
 * rejects a browser's cross-site Referer (#617), so the episode goes through MinusPod's proxy, which fetches
 * it server-side.
 */
export function episodeArtworkSrc(
  slug: string,
  episodeId?: string | null,
  episodeArtworkUrl?: string | null,
  feedArtworkUrl?: string | null,
): string {
  if (episodeArtworkUrl && episodeId) {
    return mediaUrl({ kind: 'episodeArtwork', slug, episodeId })!;
  }
  return feedArtworkSrc(slug, feedArtworkUrl);
}
