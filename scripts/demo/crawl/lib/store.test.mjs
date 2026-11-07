import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { scrubFeedKeys, writeFixture, mergeIndex } from './store.mjs';

test('scrubFeedKeys rewrites key= in nested strings only', () => {
  const out = scrubFeedKeys({ a: 'http://h/feed?key=abc123&x=1', b: ['u?key=zzz', 3, null], c: { d: 'say "hi" ?key=q"' }, 'key=k': 1 });
  assert.deepEqual(out, { a: 'http://h/feed?key=demo&x=1', b: ['u?key=demo', 3, null], c: { d: 'say "hi" ?key=demo"' }, 'key=k': 1 });
});

test('writeFixture and mergeIndex merge with new entries winning', () => {
  const dir = mkdtempSync(join(tmpdir(), 'fx-'));
  const f = writeFixture(dir, '/a', { status: 200, body: { x: 1 } });
  assert.deepEqual(JSON.parse(readFileSync(join(dir, f), 'utf8')), { status: 200, body: { x: 1 } });
  mergeIndex(dir, { '/a': f, '/b': 'old.json' });
  const merged = mergeIndex(dir, { '/b': 'new.json' });
  assert.deepEqual(merged, { '/a': f, '/b': 'new.json' });
});
