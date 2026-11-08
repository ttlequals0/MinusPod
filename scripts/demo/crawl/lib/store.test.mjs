import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { applyOverrides, readOverrides, scrubFeedKeys, writeFixture, mergeIndex } from './store.mjs';

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

test('applyOverrides copies override files over the fixtures and merges their index on top', () => {
  const out = mkdtempSync(join(tmpdir(), 'fx-'));
  const over = mkdtempSync(join(tmpdir(), 'ov-'));
  mergeIndex(out, { '/a': writeFixture(out, '/a', { status: 200, body: 'crawled' }), '/b': writeFixture(out, '/b', { status: 200, body: 'kept' }) });
  mergeIndex(over, { '/a': writeFixture(over, '/a', { status: 200, body: 'live' }) });
  applyOverrides(out, over);
  const index = JSON.parse(readFileSync(join(out, 'index.json'), 'utf8'));
  assert.deepEqual(Object.keys(index), ['/a', '/b']);
  assert.equal(JSON.parse(readFileSync(join(out, index['/a']), 'utf8')).body, 'live');
  assert.equal(JSON.parse(readFileSync(join(out, index['/b']), 'utf8')).body, 'kept');
});

test('applyOverrides copies only indexed files, and fails on a missing index or file', () => {
  const out = mkdtempSync(join(tmpdir(), 'fx-'));
  const over = mkdtempSync(join(tmpdir(), 'ov-'));
  assert.throws(() => readOverrides(over), /no index\.json/);
  mergeIndex(over, { '/a': writeFixture(over, '/a', { status: 200, body: 1 }) });
  writeFileSync(join(over, '.DS_Store'), 'junk');
  applyOverrides(out, over);
  assert.equal(existsSync(join(out, '.DS_Store')), false);
  assert.throws(() => applyOverrides(out, over, { '/gone': 'missing.json' }), /ENOENT/);
});
