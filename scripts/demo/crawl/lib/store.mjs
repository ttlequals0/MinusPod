import { mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileFor } from './keys.mjs';

const KEY_PARAM = /([?&]key=)[^&"'\s]+/g;

// Feed auth keys must not reach fixtures; walk values, never the serialized text.
export function scrubFeedKeys(value) {
  if (typeof value === 'string') return value.replace(KEY_PARAM, '$1demo');
  if (Array.isArray(value)) return value.map(scrubFeedKeys);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, scrubFeedKeys(v)]));
  }
  return value;
}

export function writeAtomic(path, text) {
  const tmp = `${path}.${process.pid}.tmp`;
  writeFileSync(tmp, text);
  renameSync(tmp, path);
}

export function writeFixture(dir, key, fixture) {
  mkdirSync(dir, { recursive: true });
  const file = fileFor(key);
  writeAtomic(join(dir, file), JSON.stringify(fixture));
  return file;
}

export function readIndex(dir) {
  const p = join(dir, 'index.json');
  return existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : {};
}

// New entries win over the existing index.
export function mergeIndex(dir, entries) {
  mkdirSync(dir, { recursive: true });
  const all = { ...readIndex(dir), ...entries };
  const merged = Object.fromEntries(Object.keys(all).sort().map((k) => [k, all[k]]));
  writeAtomic(join(dir, 'index.json'), JSON.stringify(merged, null, 2));
  return merged;
}

// Copies every fixture file in dir over out and merges its index on top, so the overrides win.
export function applyOverrides(out, dir) {
  mkdirSync(out, { recursive: true });
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.isFile() && e.name !== 'index.json') writeAtomic(join(out, e.name), readFileSync(join(dir, e.name)));
  }
  mergeIndex(out, readIndex(dir));
}
