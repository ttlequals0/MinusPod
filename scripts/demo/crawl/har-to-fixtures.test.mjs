import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { entriesToFixtures } from './har-to-fixtures.mjs';

const entry = (method, url, status, mimeType, text) => ({ request: { method, url }, response: { status, content: { mimeType, text } } });

test('keeps status codes and json bodies, ignores non-api and non-json', () => {
  const har = { log: { entries: [
    entry('GET', 'http://h/api/v1/feeds/a/episodes/e/runs/1/log', 404, 'application/json', '{"error":"gone"}'),
    entry('GET', 'http://h/api/v1/settings', 200, 'application/json; charset=utf-8', '{"a":1}'),
    entry('POST', 'http://h/api/v1/settings', 200, 'application/json', '{}'),
    entry('GET', 'http://h/assets/x.js', 200, 'text/javascript', ''),
  ] } };
  const out = entriesToFixtures(har);
  assert.deepEqual(out.get('/feeds/a/episodes/e/runs/1/log'), { status: 404, body: { error: 'gone' } });
  assert.deepEqual(out.get('/settings'), { status: 200, body: { a: 1 } });
  assert.equal(out.size, 2);
});

test('later duplicates win, 204/304 carry no body, feed keys are scrubbed', () => {
  const har = { log: { entries: [
    entry('GET', 'http://h/api/v1/a', 200, 'application/json', '{"v":1}'),
    entry('GET', 'http://h/api/v1/a', 200, 'application/json', '{"v":2,"u":"http://h/f?key=secret"}'),
    entry('GET', 'http://h/api/v1/b', 304, 'application/json', ''),
  ] } };
  const out = entriesToFixtures(har);
  assert.deepEqual(out.get('/a'), { status: 200, body: { v: 2, u: 'http://h/f?key=demo' } });
  assert.deepEqual(out.get('/b'), { status: 304 });
});

test('an unknown flag such as the removed --overrides is rejected', () => {
  const r = spawnSync(process.execPath, [fileURLToPath(new URL('./har-to-fixtures.mjs', import.meta.url)), '--har', 'x', '--out', 'y', '--overrides', 'z']);
  assert.notEqual(r.status, 0);
  assert.match(String(r.stderr), /unknown option --overrides/);
});

test('a non-empty --out is refused unless --clean, which removes the old files', () => {
  const dir = mkdtempSync(join(tmpdir(), 'har-'));
  const har = join(dir, 'x.har');
  writeFileSync(har, JSON.stringify({ log: { entries: [] } }));
  const out = join(dir, 'out');
  mkdirSync(out);
  writeFileSync(join(out, 'old.json'), '{}');
  const script = fileURLToPath(new URL('./har-to-fixtures.mjs', import.meta.url));
  const refused = spawnSync(process.execPath, [script, '--har', har, '--out', out]);
  assert.notEqual(refused.status, 0);
  assert.match(String(refused.stderr), /--clean/);
  assert.deepEqual(readdirSync(out), ['old.json']);
  const cleaned = spawnSync(process.execPath, [script, '--har', har, '--out', out, '--clean']);
  assert.equal(cleaned.status, 0);
  assert.equal(existsSync(join(out, 'old.json')), false);
});
