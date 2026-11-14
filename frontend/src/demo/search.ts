import MiniSearch, { type AsPlainObject, type Options, type SearchOptions } from 'minisearch';
import { HttpResponse } from 'msw/http';
import type {
  SearchResponse,
  SearchStats,
  SearchShowResult,
  SearchEpisodeResult,
  SearchTranscriptResult,
  SearchPatternResult,
  SearchSponsorResult,
} from '../api/search';
import { loadFixture, warnDev } from './fixtures';
import { clampInt } from './lists/paging';

const TRANSCRIPT_SNIPPET_FETCHES = 10;

type DocType = 'show' | 'episode' | 'transcript' | 'pattern' | 'sponsor';

type SearchDoc = {
  type: DocType;
  slug?: string;
  feedSlug?: string;
  feedTitle?: string;
  episodeId?: string;
  title: string;
  status?: string;
  publishDate?: string;
  scope?: string;
  sponsor?: string;
  name?: string;
};

type SearchIndexFile = {
  version: 1;
  options: Options;
  index: unknown;
  docs: Record<string, SearchDoc>;
};

const GROUPS: Record<string, { type: DocType; fields: string[] }> = {
  shows: { type: 'show', fields: ['title', 'body'] },
  episodes: { type: 'episode', fields: ['title', 'metadata'] },
  transcripts: { type: 'transcript', fields: ['body'] },
  patterns: { type: 'pattern', fields: ['title', 'body'] },
  sponsors: { type: 'sponsor', fields: ['title', 'body'] },
};
const WINDOW_WORDS = 64;
const STOPWORDS = new Set('a an and are as at be by for from in is it of on or the to with'.split(' '));

type Loaded = { ms: MiniSearch; docs: Record<string, SearchDoc> };
let loaded: Promise<Loaded> | null = null;
let failedAt = 0;
const INDEX_RETRY_MS = 30_000;

function install(file: SearchIndexFile): Loaded {
  return { ms: MiniSearch.loadJS(file.index as AsPlainObject, file.options), docs: file.docs };
}

export function __setIndexForTests(file: SearchIndexFile): void {
  loaded = Promise.resolve(install(file));
}

export function __resetIndexForTests(): void {
  loaded = null;
  failedAt = 0;
}

export function __failIndexForTests(): void {
  loaded = Promise.reject(new Error('[demo] search index load failed'));
  loaded.catch(() => {});
}

async function loadIndex(): Promise<Loaded> {
  const res = await fetch('/demo/search-index.json');
  if (!res.ok) throw new Error(`[demo] search index load failed: /demo/search-index.json (${res.status})`);
  return install((await res.json()) as SearchIndexFile);
}

function getIndex(): Promise<Loaded> {
  if (!loaded) {
    if (Date.now() - failedAt < INDEX_RETRY_MS) return Promise.reject(new Error('[demo] search index load failed'));
    const attempt = loadIndex();
    loaded = attempt;
    attempt.catch(() => {
      if (loaded === attempt) loaded = null;
      failedAt = Date.now();
    });
  }
  return loaded;
}

const escapeHtml = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const reEscape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function termRegex(terms: string[]): RegExp | null {
  const sorted = [...new Set(terms)].filter(Boolean).sort((a, b) => b.length - a.length);
  if (sorted.length === 0) return null;
  return new RegExp(`(?<![\\p{L}\\p{N}])(?:${sorted.map(reEscape).join('|')})`, 'giu');
}

// Wrap matched terms in <mark>; null when nothing matches.
function mark(text: string, re: RegExp | null): string | null {
  if (!text || !re) return null;
  let out = '';
  let last = 0;
  let hit = false;
  for (const m of text.matchAll(re)) {
    hit = true;
    out += escapeHtml(text.slice(last, m.index)) + '<mark>' + escapeHtml(m[0]) + '</mark>';
    last = m.index + m[0].length;
  }
  return hit ? out + escapeHtml(text.slice(last)) : null;
}

function windowSnippet(text: string, terms: string[], re: RegExp | null): string | null {
  const words = text.split(/\s+/).filter(Boolean);
  const set = new Set(terms.map(t => t.toLowerCase()));
  const hit = words.findIndex(w => set.has(w.toLowerCase().replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '')));
  if (hit === -1) return null;
  const start = Math.max(0, hit - WINDOW_WORDS / 2);
  const slice = words.slice(start, start + WINDOW_WORDS).join(' ');
  return mark(slice, re) ?? escapeHtml(slice);
}

async function transcriptText(feedSlug: string, episodeId: string): Promise<string | null> {
  const fx = await loadFixture(`/feeds/${feedSlug}/episodes/${episodeId}/original-transcript`);
  const t = (fx?.body as { originalTranscript?: unknown } | undefined)?.originalTranscript;
  return typeof t === 'string' ? t : null;
}

const emptyResponse = (query: string): SearchResponse => (
  { query, shows: [], episodes: [], transcripts: [], patterns: [], sponsors: [] }
);

// Mirrors _safe_fts_query: AND of non-stopword terms, prefix only on the query's last token.
function searchGroup(ms: MiniSearch, q: string, group: string, limit: number) {
  const { type, fields } = GROUPS[group];
  const tokens = q.trim().split(/\s+/);
  const kept = tokens.filter(t => !STOPWORDS.has(t.toLowerCase()));
  const terms = kept.length ? kept : tokens;
  const prefixLast = terms[terms.length - 1] === tokens[tokens.length - 1];
  const options: SearchOptions = {
    fields,
    filter: r => r.type === type,
    combineWith: 'AND',
    prefix: (_t, i, all) => prefixLast && i === all.length - 1,
  };
  return ms.search(terms.join(' '), options).slice(0, limit);
}

export async function runSearch(q: string, limit: number, groups: string[] | null): Promise<SearchResponse> {
  const out = emptyResponse(q);
  if (q.trim().length < 2) return out;
  const { ms, docs } = await getIndex();
  const wanted = groups ?? Object.keys(GROUPS);

  for (const group of wanted) {
    const hits = searchGroup(ms, q, group, limit);
    const texts = group === 'transcripts'
      ? await Promise.all(hits.map((h, i) => {
          const d = docs[String(h.id)];
          return d && i < TRANSCRIPT_SNIPPET_FETCHES ? transcriptText(d.feedSlug ?? '', d.episodeId ?? '') : null;
        }))
      : [];
    for (const [i, hit] of hits.entries()) {
      const doc = docs[String(hit.id)];
      if (!doc) continue;
      const terms = hit.terms;
      const re = termRegex(terms);
      const titleSnippet = mark(doc.title, re);
      if (group === 'shows') {
        out.shows.push({ slug: doc.slug ?? '', title: doc.title, titleSnippet, snippet: titleSnippet } satisfies SearchShowResult);
      } else if (group === 'episodes') {
        out.episodes.push({
          feedSlug: doc.feedSlug ?? '',
          feedTitle: doc.feedTitle ?? '',
          episodeId: doc.episodeId ?? '',
          title: doc.title,
          status: doc.status ?? '',
          publishDate: doc.publishDate ?? null,
          titleSnippet,
          snippet: titleSnippet,
        } satisfies SearchEpisodeResult);
      } else if (group === 'transcripts') {
        const text = texts[i];
        const snippet = (text && windowSnippet(text, terms, re)) || titleSnippet || escapeHtml(doc.title);
        out.transcripts.push({
          feedSlug: doc.feedSlug ?? '',
          episodeId: doc.episodeId ?? '',
          title: doc.title,
          snippet,
          timestamp: null,
        } satisfies SearchTranscriptResult);
      } else if (group === 'patterns') {
        const sponsor = doc.sponsor ?? doc.title;
        out.patterns.push({
          id: String(hit.id).slice('pattern:'.length),
          scope: doc.scope ?? '',
          sponsor,
          snippet: mark(sponsor, re),
        } satisfies SearchPatternResult);
      } else {
        const name = doc.name ?? doc.title;
        out.sponsors.push({
          id: String(hit.id).slice('sponsor:'.length),
          name,
          snippet: mark(name, re),
        } satisfies SearchSponsorResult);
      }
    }
  }
  return out;
}

export async function searchHandler({ request }: { request: Request }) {
  const url = new URL(request.url);
  const q = url.searchParams.get('q') ?? '';
  if (!q.trim()) return HttpResponse.json({ error: 'q is required' }, { status: 400 });

  const limit = clampInt(url.searchParams.get('limit'), 50, 1, 100);

  const raw = url.searchParams.get('groups');
  let groups: string[] | null = null;
  if (raw !== null && raw.trim() !== '') {
    groups = [...new Set(raw.split(',').map(g => g.trim()).filter(Boolean))];
    const bad = groups.find(g => !(g in GROUPS));
    if (bad) return HttpResponse.json({ error: `Unknown group: ${bad}` }, { status: 400 });
  }
  try {
    return HttpResponse.json(await runSearch(q, limit, groups));
  } catch (err) {
    warnDev('[demo] search unavailable', err);
    return HttpResponse.json({ error: 'Could not load demo search. Try again.' }, { status: 503 });
  }
}

export async function searchStatsHandler() {
  let docs: Record<string, SearchDoc>;
  try {
    ({ docs } = await getIndex());
  } catch (err) {
    warnDev('[demo] search stats unavailable', err);
    return HttpResponse.json({ error: 'Could not load demo search. Try again.' }, { status: 503 });
  }
  const counts = { show: 0, pattern: 0, sponsor: 0 };
  const episodes = new Set<string>();
  for (const [id, doc] of Object.entries(docs)) {
    if (doc.type === 'episode') episodes.add(id.slice('episode:'.length));
    else if (doc.type === 'transcript') episodes.add(id.slice('transcript:'.length));
    else counts[doc.type]++;
  }
  const stats: SearchStats['stats'] = { total: 0 };
  const parts: [keyof SearchStats['stats'], number][] = [
    ['podcast', counts.show], ['episode', episodes.size], ['pattern', counts.pattern], ['sponsor', counts.sponsor],
  ];
  for (const [k, n] of parts) {
    if (n > 0) { stats[k] = n; stats.total += n; }
  }
  return HttpResponse.json({ stats });
}
