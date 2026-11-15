import { test, expect, vi } from 'vitest';
import { episodeThumbSrc } from './artworkUrl';
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

test('demo row covers resolve to the static episode file, with or without a thumbnail address', () => {
  const thumb = '/api/v1/feeds/s/episodes/e/artwork?size=thumb&v=0123456789ab';
  expect(episodeThumbSrc('s', 'e', thumb, 'https://cdn/x.jpg', null)).toBe('/demo/artwork/episodes/s/e.jpg');
  expect(episodeThumbSrc('s', 'e', undefined, 'https://cdn/x.jpg', null)).toBe('/demo/artwork/episodes/s/e.jpg');
  expect(episodeThumbSrc('s', 'e', null, null, null)).toBe('/demo/artwork/feeds/s.jpg');
});
