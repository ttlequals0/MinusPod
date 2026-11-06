import { test, expect } from 'vitest';
import { matchList } from './index';

test('anchored patterns pick distinct handlers', () => {
  const hs = ['/feeds', '/feeds/x/episodes', '/history', '/history/downloads'].map(matchList);
  hs.forEach((h) => expect(h).not.toBeNull());
  expect(new Set(hs).size).toBe(4);
});
test('unlisted paths do not match', () => {
  expect(matchList('/feeds/x')).toBeNull();
});
