import { describe, it, expect, afterEach, vi } from 'vitest';
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
  it('routes an episode cover through the proxy instead of hot-linking it', () => {
    expect(episodeArtworkSrc('show', 'abc123def456', 'https://cdn.example/ep1.jpg', '/feed.jpg'))
      .toBe('/api/v1/feeds/show/episodes/abc123def456/artwork');
  });

  it('proxies an insecure episode cover too, since the fetch is server-side', () => {
    expect(episodeArtworkSrc('show', 'abc123def456', 'http://cdn.example/ep1.jpg', '/feed.jpg'))
      .toBe('/api/v1/feeds/show/episodes/abc123def456/artwork');
  });

  it('falls back to the feed cover when the episode has none', () => {
    expect(episodeArtworkSrc('show', 'abc123def456', null, '/feed.jpg')).toBe('/feed.jpg');
  });

  it('falls back to the feed cover when the episode id is missing', () => {
    expect(episodeArtworkSrc('show', null, 'https://cdn.example/ep1.jpg', '/feed.jpg'))
      .toBe('/feed.jpg');
  });

  it('escapes an episode id rather than letting it alter the path', () => {
    expect(episodeArtworkSrc('show', 'a/../b', 'https://cdn.example/ep1.jpg', null))
      .toBe('/api/v1/feeds/show/episodes/a%2F..%2Fb/artwork');
  });

  it('falls back to the proxy endpoint when nothing else is available', () => {
    expect(episodeArtworkSrc('show', undefined, undefined, undefined))
      .toBe('/api/v1/feeds/show/artwork');
  });
});

describe('with a URL prefix', () => {
  afterEach(() => {
    document.head.querySelectorAll('meta[name="minuspod-url-prefix"]').forEach(m => m.remove());
    vi.resetModules();
  });

  it('prefixes the proxy endpoints', async () => {
    const meta = document.createElement('meta');
    meta.name = 'minuspod-url-prefix';
    meta.content = '/minuspod';
    document.head.appendChild(meta);
    vi.resetModules();
    const { episodeArtworkSrc: ep, feedArtworkSrc: feed } = await import('./artworkUrl');
    expect(feed('show', null)).toBe('/minuspod/api/v1/feeds/show/artwork');
    expect(ep('show', 'abc', 'https://cdn.example/x.jpg', null))
      .toBe('/minuspod/api/v1/feeds/show/episodes/abc/artwork');
  });
});
