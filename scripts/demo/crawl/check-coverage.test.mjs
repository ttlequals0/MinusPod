import test from 'node:test';
import assert from 'node:assert/strict';
import { extractGetTemplates, uncovered } from './check-coverage.mjs';

test('extracts GET templates and reports misses', () => {
  const src = "apiRequest<Feed>(`/feeds/${slug}`); apiRequest(`/settings`, { method: 'PUT', body }); apiRequest<X>(`/history${qs}`);";
  assert.deepEqual(extractGetTemplates(src), ['/feeds/*', '/history']);
  assert.deepEqual(uncovered(['/feeds/*', '/history', '/system/backup'], ['/feeds/a', '/feeds/a/episodes?limit=1'], ['/system/backup']), ['/history']);
});

test('keeps GET calls with options, drops query strings and nested-brace bodies', () => {
  const src = "apiRequest<A>('/a?x=1'); apiFileRequest(`/b/${id}/export`, { method: 'GET' }); apiRequest(`/c/${id}`, { method: 'POST', body: JSON.stringify({ a: 1 }) });";
  assert.deepEqual(extractGetTemplates(src), ['/a', '/b/*/export']);
});

test('wildcards match one segment and allow list may use wildcards', () => {
  assert.deepEqual(uncovered(['/feeds/*/tags'], ['/feeds/a/b/tags']), ['/feeds/*/tags']);
  assert.deepEqual(uncovered(['/cue-templates/*/export'], []), []);
});
