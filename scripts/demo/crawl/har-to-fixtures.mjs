import { readFileSync, readdirSync, copyFileSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fixtureKey, fileFor } from './lib/keys.mjs';
import { scrubFeedKeys, writeFixture, mergeIndex, readIndex, argValue } from './lib/store.mjs';

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

// Keep segments that overlap any marker widened by n seconds on each side.
export function windowTranscript(segments, markers, n) {
  const kept = segments.filter((s) => markers.some((m) => s.end >= m.start - n && s.start <= m.end + n));
  return { segments: kept, text: kept.map((s) => s.text.trim()).join(' ') };
}

const DETAIL = /^\/feeds\/([^/?]+)\/episodes\/([^/?]+)$/;

// Rewrites fixtures in place: detail.transcript, original-segments.segments, original-transcript.originalTranscript.
export function applyTranscriptWindow(fixtures, n) {
  for (const [key, detail] of fixtures) {
    const m = DETAIL.exec(key);
    if (!m || !detail.body) continue;
    const base = `/feeds/${m[1]}/episodes/${m[2]}`;
    const segFx = fixtures.get(`${base}/original-segments`);
    if (!Array.isArray(segFx?.body?.segments)) continue;
    const { segments, text } = windowTranscript(segFx.body.segments, detail.body.adMarkers ?? [], n);
    segFx.body.segments = segments;
    if (typeof detail.body.transcript === 'string') detail.body.transcript = text;
    const txFx = fixtures.get(`${base}/original-transcript`);
    if (txFx?.body) txFx.body.originalTranscript = text;
  }
}

function main(argv) {
  const harPath = argValue(argv, '--har');
  const out = argValue(argv, '--out');
  const overrides = argValue(argv, '--overrides');
  const windowSec = argValue(argv, '--transcript-window');
  if (!harPath || !out) throw new Error('usage: --har <file> --out <dir> [--overrides <dir>] [--transcript-window <seconds>]');
  const fixtures = entriesToFixtures(JSON.parse(readFileSync(harPath, 'utf8')));
  if (windowSec !== undefined) applyTranscriptWindow(fixtures, Number(windowSec));
  const index = {};
  for (const [key, fixture] of fixtures) index[key] = writeFixture(out, key, fixture);
  mergeIndex(out, index);
  if (overrides) {
    mkdirSync(out, { recursive: true });
    for (const f of readdirSync(overrides)) {
      if (f !== 'index.json') copyFileSync(join(overrides, f), join(out, f));
    }
    mergeIndex(out, readIndex(overrides));
  }
  console.log(`wrote ${fixtures.size} fixtures to ${out}`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main(process.argv.slice(2));
