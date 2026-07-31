/** Feed cover: the API's URL, else the proxy endpoint. */
export function feedArtworkSrc(slug: string, feedArtworkUrl?: string | null): string {
  return feedArtworkUrl || `/api/v1/feeds/${slug}/artwork`;
}

/**
 * Episode cover, falling back to the feed's. An insecure URL is dropped
 * rather than rendered: the browser blocks it, showing the grey placeholder
 * instead of the feed cover we already have.
 */
export function episodeArtworkSrc(
  slug: string,
  episodeArtworkUrl?: string | null,
  feedArtworkUrl?: string | null,
): string {
  if (episodeArtworkUrl && !episodeArtworkUrl.startsWith('http://')) {
    return episodeArtworkUrl;
  }
  return feedArtworkSrc(slug, feedArtworkUrl);
}
