import { describe, it, expect } from 'vitest';
import { episodeArtworkSrc, feedArtworkSrc } from './artworkUrl';

describe('feedArtworkSrc', () => {
  it('uses the API URL when present', () => {
    expect(feedArtworkSrc('show', '/api/v1/feeds/show/artwork'))
      .toBe('/api/v1/feeds/show/artwork');
  });

  it('falls back to the proxy endpoint', () => {
    expect(feedArtworkSrc('show', null)).toBe('/api/v1/feeds/show/artwork');
  });
});

describe('episodeArtworkSrc', () => {
  it('prefers the episode cover', () => {
    expect(episodeArtworkSrc('show', 'https://cdn.example/ep1.jpg', '/feed.jpg'))
      .toBe('https://cdn.example/ep1.jpg');
  });

  it('falls back to the feed cover when the episode has none', () => {
    expect(episodeArtworkSrc('show', null, '/feed.jpg')).toBe('/feed.jpg');
  });

  it('drops an insecure episode cover rather than triggering mixed content', () => {
    expect(episodeArtworkSrc('show', 'http://cdn.example/ep1.jpg', '/feed.jpg'))
      .toBe('/feed.jpg');
  });

  it('falls back to the proxy endpoint when nothing else is available', () => {
    expect(episodeArtworkSrc('show', undefined, undefined))
      .toBe('/api/v1/feeds/show/artwork');
  });
});
