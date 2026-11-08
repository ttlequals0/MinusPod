import { test, expect } from 'vitest';
import { mapItunesResults } from './podcastSearch';

test('maps iTunes fields and drops entries without a feed', () => {
  const out = mapItunesResults({ results: [
    { collectionId: 1, collectionName: 'A', primaryGenreName: 'Tech', artworkUrl600: 'x600', artworkUrl100: 'x100', feedUrl: 'https://f', artistName: 'Me', collectionViewUrl: 'https://v' },
    { collectionId: 2, collectionName: 'No feed' },
  ] });
  expect(out).toEqual([{ id: 1, title: 'A', description: 'Tech', artworkUrl: 'x600', feedUrl: 'https://f', author: 'Me', link: 'https://v' }]);
});

test('returns an empty list when results are missing', () => {
  expect(mapItunesResults({})).toEqual([]);
});
