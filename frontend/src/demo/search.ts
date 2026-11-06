import MiniSearch, { type Options, type SearchOptions } from 'minisearch';
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
import { loadFixture } from './fixtures';

type DocType = 'show' | 'episode' | 'transcript' | 'pattern' | 'sponsor';

export type SearchDoc = {
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

export type SearchIndexFile = {
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

type Loaded = { ms: MiniSearch; docs: Record<string, SearchDoc> };
let loaded: Loaded | null = null;

function install(file: SearchIndexFile): Loaded {
  return { ms: MiniSearch.loadJSON(JSON.stringify(file.index), file.options), docs: file.docs };
}

export function __setIndexForTests(file: SearchIndexFile): void {
  loaded = install(file);
}

async function getIndex(): Promise<Loaded> {
  if (loaded) return loaded;
  const res = await fetch('/demo/search-index.json');
  if (!res.ok) throw new Error(`[demo] search index load failed: /demo/search-index.json (${res.status})`);
  loaded = install((await res.json()) as SearchIndexFile);
  return loaded;
}

const escapeHtml = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const reEscape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// Wrap matched terms in <mark>; null when nothing matches.
function mark(text: string, terms: string[]): string | null {
  const sorted = [...new Set(terms)].filter(Boolean).sort((a, b) => b.length - a.length);
  if (!text || sorted.length === 0) return null;
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(?:${sorted.map(reEscape).join('|')})`, 'giu');
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

function windowSnippet(text: string, terms: string[]): string | null {
  const words = text.split(/\s+/).filter(Boolean);
  const set = new Set(terms.map(t => t.toLowerCase()));
  const hit = words.findIndex(w => set.has(w.toLowerCase().replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '')));
  if (hit === -1) return null;
  const start = Math.max(0, hit - WINDOW_WORDS / 2);
  const slice = words.slice(start, start + WINDOW_WORDS).join(' ');
  return mark(slice, terms) ?? escapeHtml(slice);
}

async function transcriptText(feedSlug: string, episodeId: string): Promise<string | null> {
  const fx = await loadFixture(`/feeds/${feedSlug}/episodes/${episodeId}/original-transcript`);
  const t = (fx?.body as { originalTranscript?: unknown } | undefined)?.originalTranscript;
  return typeof t === 'string' ? t : null;
}

const emptyResponse = (query: string): SearchResponse => (
  { query, shows: [], episodes: [], transcripts: [], patterns: [], sponsors: [] }
);

function searchGroup(ms: MiniSearch, q: string, group: string, limit: number) {
  const { type, fields } = GROUPS[group];
  const base: SearchOptions = { fields, filter: r => r.type === type, prefix: true };
  let hits = ms.search(q, { ...base, combineWith: 'AND' });
  if (hits.length === 0) hits = ms.search(q, { ...base, combineWith: 'OR' });
  return hits.slice(0, limit);
}

export async function runSearch(q: string, limit: number, groups: string[] | null): Promise<SearchResponse> {
  const out = emptyResponse(q);
  if (q.trim().length < 2) return out;
  const { ms, docs } = await getIndex();
  const wanted = groups ?? Object.keys(GROUPS);

  for (const group of wanted) {
    for (const hit of searchGroup(ms, q, group, limit)) {
      const doc = docs[String(hit.id)];
      if (!doc) continue;
      const terms = hit.terms;
      const titleSnippet = mark(doc.title, terms);
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
        const text = await transcriptText(doc.feedSlug ?? '', doc.episodeId ?? '');
        const snippet = (text && windowSnippet(text, terms)) || titleSnippet || escapeHtml(doc.title);
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
          snippet: mark(sponsor, terms),
        } satisfies SearchPatternResult);
      } else {
        const name = doc.name ?? doc.title;
        out.sponsors.push({
          id: String(hit.id).slice('sponsor:'.length),
          name,
          snippet: mark(name, terms),
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

  const rawLimit = parseInt(url.searchParams.get('limit') ?? '', 10);
  const limit = Number.isFinite(rawLimit) ? Math.min(100, Math.max(1, rawLimit)) : 50;

  const raw = url.searchParams.get('groups');
  let groups: string[] | null = null;
  if (raw !== null && raw.trim() !== '') {
    groups = [...new Set(raw.split(',').map(g => g.trim()).filter(Boolean))];
    const bad = groups.find(g => !(g in GROUPS));
    if (bad) return HttpResponse.json({ error: `Unknown group: ${bad}` }, { status: 400 });
  }
  return HttpResponse.json(await runSearch(q, limit, groups));
}

export async function searchStatsHandler() {
  const { docs } = await getIndex();
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
