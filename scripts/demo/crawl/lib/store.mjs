import { mkdirSync, readFileSync, renameSync, writeFileSync, existsSync } from 'node:fs';
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

export function readOverrides(dir) {
  const p = join(dir, 'index.json');
  if (!existsSync(p)) throw new Error(`${dir} has no index.json; pass the overrides directory record_live wrote`);
  return JSON.parse(readFileSync(p, 'utf8'));
}

// Copies the indexed override files over out and merges their index on top, so the overrides win.
export function applyOverrides(out, dir, index = readOverrides(dir)) {
  mkdirSync(out, { recursive: true });
  for (const file of new Set(Object.values(index))) writeAtomic(join(out, file), readFileSync(join(dir, file)));
  mergeIndex(out, index);
}
