import { createHash } from 'node:crypto';

const API_PREFIX = '/api/v1';

// Mirrors frontend/src/demo/fixtures.ts fixtureKey.
export function fixtureKey(input) {
  const url = typeof input === 'string' ? new URL(input) : input;
  const entries = [...url.searchParams.entries()].sort(([an, av], [bn, bv]) =>
    an < bn ? -1 : an > bn ? 1 : av < bv ? -1 : av > bv ? 1 : 0);
  const qs = new URLSearchParams(entries).toString();
  const p = url.pathname;
  return (p.startsWith(API_PREFIX) ? p.slice(API_PREFIX.length) : p) + (qs ? `?${qs}` : '');
}

export function fileFor(key) {
  return createHash('sha1').update(key).digest('hex') + '.json';
}
