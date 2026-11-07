import { test, expect } from 'vitest';
import { mediaUrl } from './mediaUrl';
test('non-demo artwork and audio urls', () => {
  expect(mediaUrl({ kind: 'feedArtwork', slug: 's' })).toBe('/api/v1/feeds/s/artwork');
  expect(mediaUrl({ kind: 'feedArtwork', slug: 's', apiArtworkUrl: 'https://cdn/x.png' })).toBe('https://cdn/x.png');
  expect(mediaUrl({ kind: 'episodeArtwork', slug: 's', episodeId: 'a b' })).toBe('/api/v1/feeds/s/episodes/a%20b/artwork');
  expect(mediaUrl({ kind: 'audio', url: '/episodes/s/e.mp3' })).toBe('/episodes/s/e.mp3');
});
