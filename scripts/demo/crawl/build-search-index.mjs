import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import MiniSearch from 'minisearch';
import { writeAtomic, readIndex } from './lib/store.mjs';
import { argValue } from './lib/args.mjs';

const FEEDS_KEY = '/feeds?episodesPerFeed=20&includeLatestEpisodes=true';
// processTerm is left at MiniSearch's default (lowercase): functions do not survive JSON.
export const OPTIONS = { fields: ['title', 'body', 'metadata'], storeFields: ['type'] };

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };

export function stripHtml(s) {
  return String(s ?? '')
    .replace(/<[^>]*>/g, ' ')
    .replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
      if (e[0] === '#') {
        const n = e[1].toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : Number(e.slice(1));
        return Number.isFinite(n) && n > 0 && n <= 0x10ffff ? String.fromCodePoint(n) : m;
      }
      return ENTITIES[e.toLowerCase()] ?? m;
    })
    .replace(/\s+/g, ' ')
    .trim();
}

const rows = (fx, field) => (fx?.status === 200 && Array.isArray(fx.body?.[field]) ? fx.body[field] : []);

export function buildDocuments(fixtures) {
  const documents = [];
  const docs = {};
  const add = (doc, meta) => {
    if (docs[doc.id]) return;
    documents.push(doc);
    docs[doc.id] = meta;
  };
  const epKey = (slug) => `/feeds/${encodeURIComponent(slug)}/episodes?limit=500&offset=0&sort_by=created_at&sort_dir=desc&status=all`;

  for (const feed of rows(fixtures.get(FEEDS_KEY), 'feeds')) {
    if (feed.feedType === 'recents') continue;
    add({ id: `show:${feed.slug}`, type: 'show', title: feed.title, body: stripHtml(feed.description) },
      { type: 'show', slug: feed.slug, title: feed.title });
    for (const ep of rows(fixtures.get(epKey(feed.slug)), 'episodes')) {
      const base = { feedSlug: feed.slug, feedTitle: feed.title, episodeId: ep.id, title: ep.title };
      add({ id: `episode:${feed.slug}/${ep.id}`, type: 'episode', title: ep.title, metadata: stripHtml(ep.description) },
        { type: 'episode', ...base, status: ep.status, publishDate: ep.published ?? null });
      const tr = fixtures.get(`/feeds/${encodeURIComponent(feed.slug)}/episodes/${encodeURIComponent(ep.id)}/original-transcript`);
      const text = tr?.status === 200 ? tr.body?.originalTranscript : null;
      if (typeof text === 'string' && text) {
        add({ id: `transcript:${feed.slug}/${ep.id}`, type: 'transcript', title: ep.title, body: text },
          { type: 'transcript', ...base });
      }
    }
  }
  for (const p of rows(fixtures.get('/patterns'), 'patterns')) {
    if (!p.is_active) continue;
    const sponsor = p.sponsor || 'Unknown';
    add({ id: `pattern:${p.id}`, type: 'pattern', title: sponsor, body: p.text_template ?? '' },
      { type: 'pattern', title: sponsor, scope: p.scope, sponsor });
  }
  for (const s of rows(fixtures.get('/sponsors?include_inactive=true'), 'sponsors')) {
    if (!s.is_active) continue;
    add({ id: `sponsor:${s.id}`, type: 'sponsor', title: s.name, body: (s.aliases ?? []).join(' ') },
      { type: 'sponsor', title: s.name, name: s.name });
  }
  return { documents, docs };
}

export function loadFixtures(dir) {
  const map = new Map();
  for (const [key, file] of Object.entries(readIndex(dir))) {
    map.set(key, JSON.parse(readFileSync(join(dir, file), 'utf8')));
  }
  return map;
}

export function buildIndexFile(fixtures) {
  const { documents, docs } = buildDocuments(fixtures);
  const ms = new MiniSearch(OPTIONS);
  ms.addAll(documents);
  return { file: { version: 1, options: OPTIONS, index: ms.toJSON(), docs }, documents };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const fixtures = argValue(process.argv, '--fixtures');
  const out = argValue(process.argv, '--out');
  if (!fixtures || !out) {
    console.error('usage: build-search-index.mjs --fixtures <dir> --out <search-index.json>');
    process.exit(2);
  }
  const { file, documents } = buildIndexFile(loadFixtures(resolve(fixtures)));
  const text = JSON.stringify(file);
  writeAtomic(resolve(out), text);
  const counts = {};
  for (const d of documents) counts[d.type] = (counts[d.type] ?? 0) + 1;
  console.log(`search index: ${documents.length} documents ${JSON.stringify(counts)}, ${text.length} bytes`);
}
