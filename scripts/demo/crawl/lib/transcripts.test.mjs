import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { windowTranscript, applyTranscriptWindow, windowFixtureDir } from './transcripts.mjs';
import { writeFixture, mergeIndex } from './store.mjs';

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

test('windowFixtureDir rewrites the written episode fixtures in place and leaves the rest alone', () => {
  const dir = mkdtempSync(join(tmpdir(), 'win-'));
  const detail = { status: 200, body: { adMarkers: [{ start: 62, end: 70 }], transcript: TX } };
  const index = {
    '/feeds/s/episodes/e': writeFixture(dir, '/feeds/s/episodes/e', detail),
    '/feeds/s/episodes/e/original-transcript': writeFixture(dir, '/feeds/s/episodes/e/original-transcript', { status: 200, body: { originalTranscript: TX } }),
    '/settings': writeFixture(dir, '/settings', { status: 200, body: { transcript: TX } }),
  };
  mergeIndex(dir, index);
  assert.equal(windowFixtureDir(dir, 5), 2);
  const read = (k) => JSON.parse(readFileSync(join(dir, index[k]), 'utf8')).body;
  assert.equal(read('/feeds/s/episodes/e').transcript, '[00:01:00.000 --> 00:01:10.000] ad read');
  assert.equal(read('/feeds/s/episodes/e/original-transcript').originalTranscript, '[00:01:00.000 --> 00:01:10.000] ad read');
  assert.equal(read('/settings').transcript, TX);
});
