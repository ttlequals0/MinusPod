import test from 'node:test';
import assert from 'node:assert/strict';
import MiniSearch from 'minisearch';
import { buildDocuments, buildIndexFile, stripHtml } from './build-search-index.mjs';

const fx = () => new Map([
  ['/feeds?episodesPerFeed=20&includeLatestEpisodes=true', { status: 200, body: { feeds: [{ slug: 'a', title: 'A show', description: '<p>About &amp; more</p>' }] } }],
  ['/feeds/a/episodes?limit=500&offset=0&sort_by=created_at&sort_dir=desc&status=all', { status: 200, body: { episodes: [{ id: 'e1', title: 'Ep one', description: 'desc', status: 'processed', published: '2026-11-01T00:00:00Z' }] } }],
  ['/feeds/a/episodes/e1/original-transcript', { status: 200, body: { originalTranscript: 'hello world' } }],
  ['/patterns', { status: 200, body: { patterns: [
    { id: 7, scope: 'global', sponsor: 'Acme', text_template: 'by acme', is_active: true },
    { id: 8, scope: 'global', sponsor: 'Off', text_template: 'off', is_active: false },
    { id: 9, scope: 'global', sponsor: null, text_template: 'anon', is_active: true },
  ] } }],
  ['/sponsors?include_inactive=true', { status: 200, body: { sponsors: [
    { id: 3, name: 'Acme', aliases: ['ACME Inc'], is_active: true },
    { id: 4, name: 'Dead', aliases: [], is_active: false },
  ] } }],
]);

test('builds typed documents from fixtures', () => {
  const { documents, docs } = buildDocuments(fx());
  assert.deepEqual(documents.map(d => d.id).sort(), ['episode:a/e1', 'pattern:7', 'pattern:9', 'show:a', 'sponsor:3', 'transcript:a/e1']);
  assert.equal(documents.find(d => d.id === 'show:a').body, 'About & more');
  assert.equal(documents.find(d => d.id === 'pattern:9').title, 'Unknown');
  assert.equal(documents.find(d => d.id === 'sponsor:3').body, 'ACME Inc');
  assert.equal(docs['episode:a/e1'].feedTitle, 'A show');
  assert.equal(docs['episode:a/e1'].publishDate, '2026-11-01T00:00:00Z');
});

test('index file round-trips through JSON and searches case-insensitively', () => {
  const { file } = buildIndexFile(fx());
  const back = JSON.parse(JSON.stringify(file));
  const ms = MiniSearch.loadJS(back.index, back.options);
  assert.deepEqual(ms.search('HELLO').map(r => r.id), ['transcript:a/e1']);
});

test('stripHtml removes tags and decodes entities', () => {
  assert.equal(stripHtml('<b>a</b>&nbsp;&#39;b&#x41;'), "a 'bA");
});
