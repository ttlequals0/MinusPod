import { test, expect } from 'vitest';
import { fixtureKey } from '../fixtures';
import { matchList, registry } from './index';

test('anchored patterns pick distinct handlers', () => {
  const hs = ['/feeds', '/feeds/x/episodes', '/history', '/history/downloads'].map(matchList);
  hs.forEach((h) => expect(h).not.toBeNull());
  expect(new Set(hs).size).toBe(4);
});
test('unlisted paths do not match', () => {
  expect(matchList('/feeds/x')).toBeNull();
});
test('every dump key is already in fixtureKey form', async () => {
  for (const { pattern, handler } of registry) {
    const path = pattern.source.replace(/^\^|\$$/g, '').replace(/\[\^\/\]\+/g, 'demo').replace(/\\/g, '');
    expect(pattern.test(path)).toBe(true);
    const keys: string[] = [];
    await handler(new URL('http://x/api/v1' + path), async (key) => { keys.push(key); return null; });
    expect(keys).toHaveLength(1);
    expect(fixtureKey('http://x/api/v1' + keys[0])).toBe(keys[0]);
  }
});
