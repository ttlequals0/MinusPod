import { matchList } from './lists/index';

export type Fixture = { status: number; body: unknown };

const API_PREFIX = '/api/v1';
const FIXTURE_BASE = '/demo/fixtures/';

let index = new Map<string, string>();
let paths = new Set<string>();
const cache = new Map<string, Promise<Fixture>>();

let loader: (file: string) => Promise<Fixture> = async (file) => {
  const res = await fetch(FIXTURE_BASE + file);
  if (!res.ok) throw new Error(`[demo] fixture load failed: ${FIXTURE_BASE}${file} (${res.status})`);
  return (await res.json()) as Fixture;
};

function pathOf(key: string): string {
  const q = key.indexOf('?');
  return q === -1 ? key : key.slice(0, q);
}

function installIndex(entries: Record<string, string>): void {
  index = new Map(Object.entries(entries));
  paths = new Set([...index.keys()].map(pathOf));
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

export function normalizedPath(url: URL): string {
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

export async function loadFixture(key: string): Promise<Fixture | null> {
  const file = index.get(key);
  if (file === undefined) return null;
  let pending = cache.get(file);
  if (!pending) {
    pending = loader(file);
    cache.set(file, pending);
    pending.catch(() => cache.delete(file));
  }
  return pending;
}

export function warnDev(...args: unknown[]): void {
  if (__DEMO__ && import.meta.env.DEV) console.warn(...args);
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
  [/^\/patterns\/\d+\/audio$/, { state: 'none', available: false, counts: { matches: 0, rejected: 0, outOfScope: 0 } }],
  [/^\/patterns\/\d+\/audio\/matches$/, { matches: [], page: 1, perPage: 10, total: 0 }],
  [/^\/sponsors$/, { sponsors: [] }],
  [/^\/episodes\/processing$/, []],
  [/^\/episodes\/(awaiting-approval|held|pending-recuts)$/, queue],
  [/^\/stats\/audio-matching$/, { patternsWithAudio: 0, patternsTotal: 0, audioMatches: 0, foundByAudioOnly: 0, notThisAd: 0 }],
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
  return { status: 404, body: { error: 'Not found' } };
}

export async function resolveFixture(url: URL): Promise<Fixture> {
  const key = fixtureKey(url);
  const pathname = normalizedPath(url);
  try {
    const exact = await loadFixture(key);
    if (exact) return exact;

    const handler = matchList(pathname);
    if (handler) {
      const listed = await handler(url, loadFixture);
      if (listed) return listed;
    }
  } catch (err) {
    warnDev('[demo] fixture load failed', key, err);
    return { status: 503, body: { error: 'Could not load demo data. Try again.' } };
  }

  warnDev('[demo] no fixture for', key);
  if (paths.has(pathname)) {
    return { status: 422, body: { error: 'These filters are not available in this demo snapshot.' } };
  }
  return emptyDefault(pathname);
}
