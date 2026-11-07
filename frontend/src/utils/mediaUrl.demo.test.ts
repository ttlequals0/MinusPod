import { test, expect, vi } from 'vitest';
import { mediaUrl } from './mediaUrl';

vi.mock('../demo/flag', () => ({ IS_DEMO: true }));

test('demo artwork comes from static files and audio is absent', () => {
  expect(mediaUrl({ kind: 'feedArtwork', slug: 's', apiArtworkUrl: 'https://cdn/x.png' })).toBe('/demo/artwork/feeds/s.jpg');
  expect(mediaUrl({ kind: 'episodeArtwork', slug: 's', episodeId: 'a b' })).toBe('/demo/artwork/episodes/s/a%20b.jpg');
  expect(mediaUrl({ kind: 'audio', url: '/episodes/s/e.mp3' })).toBeNull();
});

test('demo artwork URLs encode the slug', () => {
  expect(mediaUrl({ kind: 'feedArtwork', slug: 'a b/c' })).toBe('/demo/artwork/feeds/a%20b%2Fc.jpg');
  expect(mediaUrl({ kind: 'episodeArtwork', slug: 'a b/c', episodeId: 'e' })).toBe('/demo/artwork/episodes/a%20b%2Fc/e.jpg');
});
