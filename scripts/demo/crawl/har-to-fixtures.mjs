import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fixtureKey, fileFor } from './lib/keys.mjs';
import { scrubFeedKeys, writeFixture, writeAtomic, mergeIndex, readIndex } from './lib/store.mjs';
import { argValue } from './lib/args.mjs';

const API_PATH = '/api/v1/';

function decodeBody(content) {
  const text = content?.text ?? '';
  return content?.encoding === 'base64' ? Buffer.from(text, 'base64').toString('utf8') : text;
}

export function entriesToFixtures(har) {
  const out = new Map();
  for (const { request, response } of har.log.entries) {
    if (request.method !== 'GET') continue;
    const url = new URL(request.url);
    if (!url.pathname.startsWith(API_PATH)) continue;
    if (!/json/i.test(response.content?.mimeType ?? '')) continue;
    const { status } = response;
    let fixture = { status };
    if (status !== 204 && status !== 304) {
      try {
        fixture = { status, body: scrubFeedKeys(JSON.parse(decodeBody(response.content))) };
      } catch { /* unparseable body: keep status only */ }
    }
    out.set(fixtureKey(url), fixture);
  }
  return out;
}

const LINE_START = /^\[(\d+):(\d+):(\d+(?:\.\d+)?) --> /;

function inWindow(t, markers, n) {
  return markers.some((m) => t >= m.start - n && t <= m.end + n);
}

// Keep timestamped lines whose start is within n seconds of a marker; untimestamped
// lines follow the line above. Text with no timestamped line is returned unchanged.
export function windowTranscript(text, markers, n) {
  const lines = text.split('\n');
  let keep = false;
  let timed = false;
  const kept = lines.filter((line) => {
    const m = LINE_START.exec(line);
    if (m) {
      timed = true;
      keep = inWindow(Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]), markers, n);
    }
    return keep;
  });
  return timed ? kept.join('\n') : text;
}

const DETAIL = /^\/feeds\/([^/?]+)\/episodes\/([^/?]+)$/;

// Rewrites in place: detail.transcript, original-transcript.originalTranscript, original-segments.segments.
export function applyTranscriptWindow(fixtures, n) {
  for (const [key, detail] of fixtures) {
    const m = DETAIL.exec(key);
    if (!m || !detail.body) continue;
    const base = `/feeds/${m[1]}/episodes/${m[2]}`;
    const markers = detail.body.adMarkers ?? [];
    if (typeof detail.body.transcript === 'string') detail.body.transcript = windowTranscript(detail.body.transcript, markers, n);
    const tx = fixtures.get(`${base}/original-transcript`)?.body;
    if (typeof tx?.originalTranscript === 'string') tx.originalTranscript = windowTranscript(tx.originalTranscript, markers, n);
    const seg = fixtures.get(`${base}/original-segments`)?.body;
    if (Array.isArray(seg?.segments)) seg.segments = seg.segments.filter((s) => inWindow(s.start, markers, n));
  }
}

function main(argv) {
  const harPath = argValue(argv, '--har');
  const out = argValue(argv, '--out');
  const overrides = argValue(argv, '--overrides');
  const windowSec = argValue(argv, '--transcript-window');
  if (!harPath || !out) throw new Error('usage: --har <file> --out <dir> [--overrides <dir>] [--transcript-window <seconds>]');
  if (windowSec !== undefined && !/^\d+$/.test(windowSec)) {
    console.error('--transcript-window must be a non-negative integer number of seconds');
    process.exit(2);
  }
  const fixtures = entriesToFixtures(JSON.parse(readFileSync(harPath, 'utf8')));
  if (windowSec !== undefined) applyTranscriptWindow(fixtures, Number(windowSec));
  const index = {};
  for (const [key, fixture] of fixtures) index[key] = writeFixture(out, key, fixture);
  mergeIndex(out, index);
  if (overrides) {
    mkdirSync(out, { recursive: true });
    for (const e of readdirSync(overrides, { withFileTypes: true })) {
      if (e.isFile() && e.name !== 'index.json') writeAtomic(join(out, e.name), readFileSync(join(overrides, e.name)));
    }
    mergeIndex(out, readIndex(overrides));
  }
  console.log(`wrote ${fixtures.size} fixtures to ${out}`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main(process.argv.slice(2));
