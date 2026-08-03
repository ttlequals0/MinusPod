/** Feed cover: the API's URL, else the proxy endpoint. */
export function feedArtworkSrc(slug: string, feedArtworkUrl?: string | null): string {
  return feedArtworkUrl || `/api/v1/feeds/${slug}/artwork`;
}

/**
 * Episode cover, falling back to the feed's. Publisher URLs are never
 * rendered directly: hotlink protection rejects a browser's cross-site
 * Referer and the reader gets a grey placeholder (#617), so the episode
 * goes through MinusPod's proxy, which fetches it server-side.
 */
export function episodeArtworkSrc(
  slug: string,
  episodeId?: string | null,
  episodeArtworkUrl?: string | null,
  feedArtworkUrl?: string | null,
): string {
  if (episodeArtworkUrl && episodeId) {
    return `/api/v1/feeds/${slug}/episodes/${encodeURIComponent(episodeId)}/artwork`;
  }
  return feedArtworkSrc(slug, feedArtworkUrl);
}
