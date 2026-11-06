import { matchList } from './lists/index';

export type Fixture = { status: number; body: unknown };

const API_PREFIX = '/api/v1';
const FIXTURE_BASE = '/demo/fixtures/';

let index = new Map<string, string>();
let byPath = new Map<string, string[]>();
const cache = new Map<string, Fixture>();

let loader: (file: string) => Promise<Fixture> = async (file) => {
  const res = await fetch(FIXTURE_BASE + file);
  if (!res.ok) throw new Error(`[demo] fixture load failed: ${FIXTURE_BASE}${file} (${res.status})`);
  return (await res.json()) as Fixture;
};

function pathOf(key: string): string {
  const q = key.indexOf('?');
  return q === -1 ? key : key.slice(0, q);
}

function paramCount(key: string): number {
  const q = key.indexOf('?');
  return q === -1 ? 0 : key.slice(q + 1).split('&').length;
}

function installIndex(entries: Record<string, string>): void {
  index = new Map(Object.entries(entries));
  byPath = new Map();
  for (const key of index.keys()) {
    const p = pathOf(key);
    const keys = byPath.get(p);
    if (keys) keys.push(key);
    else byPath.set(p, [key]);
  }
  cache.clear();
}

export function __setIndexForTests(entries: Record<string, string>): void {
  installIndex(entries);
}

export function __setLoaderForTests(fn: (file: string) => Promise<Fixture>): void {
  loader = fn;
  cache.clear();
}

export async function loadFixtureIndex(): Promise<void> {
  const res = await fetch(FIXTURE_BASE + 'index.json');
  if (!res.ok) throw new Error('[demo] fixture index missing: /demo/fixtures/index.json (' + res.status + ')');
  installIndex((await res.json()) as Record<string, string>);
}

function normalizedPath(url: URL): string {
  const p = url.pathname;
  return p.startsWith(API_PREFIX) ? p.slice(API_PREFIX.length) : p;
}

export function fixtureKey(input: URL | string): string {
  const url = typeof input === 'string' ? new URL(input) : input;
  const entries = [...url.searchParams.entries()].sort(
    ([an, av], [bn, bv]) => (an < bn ? -1 : an > bn ? 1 : av < bv ? -1 : av > bv ? 1 : 0),
  );
  const qs = new URLSearchParams(entries).toString();
  return normalizedPath(url) + (qs ? `?${qs}` : '');
}

export function hasFixture(key: string): boolean {
  return index.has(key);
}

export async function loadFixture(key: string): Promise<Fixture | null> {
  const file = index.get(key);
  if (file === undefined) return null;
  let fixture = cache.get(file);
  if (!fixture) {
    fixture = await loader(file);
    cache.set(file, fixture);
  }
  return fixture;
}

function warnDev(...args: unknown[]): void {
  if (__DEMO__ && import.meta.env.DEV) console.warn(...args);
}

async function tryLoad(key: string): Promise<Fixture | null> {
  try {
    return await loadFixture(key);
  } catch (err) {
    warnDev('[demo] fixture load failed', key, err);
    return null;
  }
}

const page = (limit: number) => ({ total: 0, page: 1, limit, totalPages: 0 });
const costs = { items: [], total: 0, totalPages: 0, page: 1, limit: 50 };
const queue = { count: 0, episodes: [] };

const emptyTable: Array<[RegExp, unknown]> = [
  [/^\/feeds$/, { feeds: [], lastRefreshCompletedAt: null, total: 0, totalPages: 0, page: 1, limit: 50, offset: 0 }],
  [/^\/feeds\/[^/]+\/episodes$/, { episodes: [], total: 0, limit: 25, offset: 0 }],
  [/^\/history$/, { history: [], total: 0, page: 1, limit: 50, totalPages: 0 }],
  [/^\/history\/downloads$/, { downloads: [], total: 0, page: 1, limit: 50 }],
  [/^\/detections$/, { detections: [], ...page(20), counts: {}, cutSummary: {} }],
  [/^\/patterns$/, { patterns: [] }],
  [/^\/sponsors$/, { sponsors: [] }],
  [/^\/search$/, { query: '', shows: [], episodes: [], transcripts: [], patterns: [], sponsors: [] }],
  [/^\/episodes\/processing$/, []],
  [/^\/episodes\/(awaiting-approval|held|pending-recuts)$/, queue],
  [/^\/stats\/by-day$/, { days: [] }],
  [/^\/stats\/by-podcast$/, { podcasts: [] }],
  [/^\/stats\/(model-usage|episode-costs)$/, costs],
];

const entityPath = /^\/feeds\/[^/]+(\/episodes\/[^/]+)?$/;

export function emptyDefault(pathname: string): Fixture {
  for (const [pattern, body] of emptyTable) {
    if (pattern.test(pathname)) return { status: 200, body };
  }
  if (entityPath.test(pathname)) return { status: 404, body: { error: 'Not found in demo' } };
  return { status: 200, body: {} };
}

export async function resolveFixture(url: URL): Promise<Fixture> {
  const key = fixtureKey(url);
  const exact = await tryLoad(key);
  if (exact) return exact;

  const pathname = normalizedPath(url);
  const handler = matchList(pathname);
  if (handler) {
    try {
      const listed = await handler(url, loadFixture);
      if (listed) return listed;
    } catch (err) {
      warnDev('[demo] fixture load failed', key, err);
    }
  }

  const candidates = byPath.get(pathname);
  if (candidates && candidates.length > 0) {
    const best = [...candidates].sort((a, b) => paramCount(a) - paramCount(b) || (a < b ? -1 : a > b ? 1 : 0))[0];
    const matched = await tryLoad(best);
    if (matched) return matched;
  }

  warnDev('[demo] no fixture for', key);
  return emptyDefault(pathname);
}
