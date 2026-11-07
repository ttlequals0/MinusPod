import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fixtureKey, fileFor } from './keys.mjs';

test('key sorts params and strips the prefix', () => {
  assert.equal(fixtureKey('http://h/api/v1/feeds/a/episodes?status=all&limit=25'), '/feeds/a/episodes?limit=25&status=all');
  assert.equal(fixtureKey('http://h/api/v1/search?q=a%20b'), '/search?q=a+b');
  assert.equal(fixtureKey('http://h/api/v1/settings?'), '/settings');
  assert.match(fileFor('/settings'), /^[0-9a-f]{40}\.json$/);
});

test('every shared vector matches', () => {
  const vectors = JSON.parse(readFileSync(new URL('./key-vectors.json', import.meta.url), 'utf8'));
  for (const [url, key] of Object.entries(vectors)) assert.equal(fixtureKey(url), key, url);
});
