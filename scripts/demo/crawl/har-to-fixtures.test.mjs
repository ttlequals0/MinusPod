import test from 'node:test';
import assert from 'node:assert/strict';
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

const segs = [0, 10, 20, 30, 40, 50].map((s, i) => ({ start: s, end: s + 10, text: `s${i} ` }));

test('windowTranscript keeps only segments within N seconds of a marker', () => {
  const { segments, text } = windowTranscript(segs, [{ start: 30, end: 35 }], 5);
  assert.deepEqual(segments.map((s) => s.text.trim()), ['s2', 's3', 's4']);
  assert.equal(text, 's2 s3 s4');
  assert.deepEqual(windowTranscript(segs, [], 5).segments, []);
});

test('applyTranscriptWindow rewrites detail, original-segments and original-transcript', () => {
  const fx = new Map([
    ['/feeds/s/episodes/e', { status: 200, body: { adMarkers: [{ start: 0, end: 5 }], transcript: 'everything' } }],
    ['/feeds/s/episodes/e/original-segments', { status: 200, body: { episodeId: 'e', segments: segs } }],
    ['/feeds/s/episodes/e/original-transcript', { status: 200, body: { episodeId: 'e', originalTranscript: 'everything' } }],
  ]);
  applyTranscriptWindow(fx, 2);
  assert.equal(fx.get('/feeds/s/episodes/e').body.transcript, 's0');
  assert.equal(fx.get('/feeds/s/episodes/e/original-transcript').body.originalTranscript, 's0');
  assert.equal(fx.get('/feeds/s/episodes/e/original-segments').body.segments.length, 1);
});
