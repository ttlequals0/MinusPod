import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { entriesToFixtures, windowTranscript, applyTranscriptWindow } from './har-to-fixtures.mjs';

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

const TX = '[00:00:00.000 --> 00:00:19.280] intro\n[00:01:00.000 --> 00:01:10.000] ad read\n[00:10:00.000 --> 00:10:05.000] far away';

test('windowTranscript keeps timestamped lines near a marker and keeps the format', () => {
  const out = windowTranscript(TX, [{ start: 62, end: 70 }], 5);
  assert.equal(out, '[00:01:00.000 --> 00:01:10.000] ad read');
  assert.equal(windowTranscript(TX, [{ start: 0, end: 5 }], 120), TX.split('\n').slice(0, 2).join('\n'));
  assert.equal(windowTranscript('just plain text', [{ start: 0, end: 5 }], 1), 'just plain text');
});

test('applyTranscriptWindow rewrites detail, original-segments and original-transcript only', () => {
  const segs = [0, 60, 600].map((s) => ({ start: s, end: s + 10, text: `s${s}` }));
  const fx = new Map([
    ['/feeds/s/episodes/e', { status: 200, body: { adMarkers: [{ start: 62, end: 70 }], transcript: TX, upstreamTranscript: { text: TX } } }],
    ['/feeds/s/episodes/e/original-segments', { status: 200, body: { episodeId: 'e', segments: segs } }],
    ['/feeds/s/episodes/e/original-transcript', { status: 200, body: { episodeId: 'e', originalTranscript: TX } }],
  ]);
  applyTranscriptWindow(fx, 5);
  const d = fx.get('/feeds/s/episodes/e').body;
  assert.equal(d.transcript, '[00:01:00.000 --> 00:01:10.000] ad read');
  assert.equal(d.upstreamTranscript.text, TX);
  assert.equal(fx.get('/feeds/s/episodes/e/original-transcript').body.originalTranscript, d.transcript);
  assert.deepEqual(fx.get('/feeds/s/episodes/e/original-segments').body.segments.map((x) => x.start), [60]);
});

test('invalid --transcript-window exits 2', () => {
  const r = spawnSync(process.execPath, [fileURLToPath(new URL('./har-to-fixtures.mjs', import.meta.url)), '--har', 'x', '--out', 'y', '--transcript-window', '-3']);
  assert.equal(r.status, 2);
});
