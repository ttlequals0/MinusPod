import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { readIndex, writeAtomic } from './store.mjs';

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

export const DETAIL = /^\/feeds\/([^/?]+)\/episodes\/([^/?]+)$/;

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

// Applies the window to every episode already written to a fixtures dir; returns how many files it rewrote.
export function windowFixtureDir(dir, n) {
  const index = readIndex(dir);
  const fixtures = new Map();
  for (const key of Object.keys(index)) {
    if (DETAIL.test(key) || /\/original-(transcript|segments)$/.test(key)) fixtures.set(key, JSON.parse(readFileSync(join(dir, index[key]), 'utf8')));
  }
  applyTranscriptWindow(fixtures, n);
  for (const [key, fixture] of fixtures) writeAtomic(join(dir, index[key]), JSON.stringify(fixture));
  return fixtures.size;
}
