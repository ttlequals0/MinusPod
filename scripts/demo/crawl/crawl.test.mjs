import test from 'node:test';
import assert from 'node:assert/strict';
import { expandRoutes, dismissDialogs, isCrawlError, CLOSED } from './crawl.mjs';

const routes = { static: ['/'], perFeed: ['/feeds/{slug}'], perEpisode: ['/feeds/{slug}/episodes/{id}'], episodesPerFeed: 1 };

test('expands per-feed and per-episode routes', () => {
  const r = expandRoutes(routes, [{ slug: 'a', feedType: 'rss', episodes: ['e1', 'e2'] }]);
  assert.deepEqual(r, ['/', '/feeds/a', '/feeds/a/episodes/e1']);
});

test('recents feeds get no per-episode routes', () => {
  const r = expandRoutes(routes, [{ slug: 'r', feedType: 'recents', episodes: ['e1'] }]);
  assert.deepEqual(r, ['/', '/feeds/r']);
});

test('encodes slugs and ids', () => {
  const r = expandRoutes(routes, [{ slug: 'a b', feedType: 'rss', episodes: ['x/y'] }]);
  assert.deepEqual(r, ['/', '/feeds/a%20b', '/feeds/a%20b/episodes/x%2Fy']);
});

test('expands the search type routes from routes.json', async () => {
  const { readFileSync } = await import('node:fs');
  const real = JSON.parse(readFileSync(new URL('./routes.json', import.meta.url), 'utf8'));
  const r = expandRoutes(real, []);
  for (const t of ['episode', 'podcast', 'transcript', 'pattern', 'sponsor']) {
    assert.ok(r.includes(`/search?q=the&type=${t}`), t);
  }
  assert.equal('settingsSections' in real, false);
});

test('429 and 5xx responses are crawl errors, other statuses are not', () => {
  for (const s of [429, 500, 502, 503]) assert.equal(isCrawlError(s), true, String(s));
  for (const s of [200, 204, 304, 404, 403]) assert.equal(isCrawlError(s), false, String(s));
});

test('the disclosure selector skips menu triggers', () => {
  assert.ok(CLOSED.includes(':not([aria-haspopup])'));
});

// Stub page: one modal that is visible until closed by the Close button or Escape.
function stubPage({ visible = true, hasClose = true, clickThrows = false, stuck = false, heading = "What's new in 2.0" } = {}) {
  const calls = [];
  let open = visible;
  const loc = (kind) => ({
    first() { return this; },
    isVisible: async () => (kind === 'dialog' ? open : false),
    count: async () => (kind === 'close' && hasClose ? 1 : 0),
    textContent: async () => heading,
    getAttribute: async () => null,
    click: async () => { calls.push('click'); if (clickThrows) throw new Error('blocked'); if (!stuck) open = false; },
    waitFor: async () => { calls.push('hidden'); if (stuck) throw new Error('timeout'); },
    locator(sel) { return loc(sel.includes('Close') ? 'close' : 'heading'); },
  });
  return {
    calls,
    locator: () => loc('dialog'),
    keyboard: { press: async (k) => { calls.push(k); if (!stuck) open = false; } },
  };
}

test('dismissDialogs clicks Close on a visible modal and returns its heading', async () => {
  const page = stubPage();
  assert.deepEqual(await dismissDialogs(page), ["What's new in 2.0"]);
  assert.deepEqual(page.calls, ['click', 'hidden']);
});

test('dismissDialogs falls back to Escape when the dialog has no Close button', async () => {
  const page = stubPage({ hasClose: false });
  assert.deepEqual(await dismissDialogs(page), ["What's new in 2.0"]);
  assert.deepEqual(page.calls, ['Escape', 'hidden']);
});

test('dismissDialogs does nothing when no dialog is visible', async () => {
  const page = stubPage({ visible: false });
  assert.deepEqual(await dismissDialogs(page), []);
  assert.deepEqual(page.calls, []);
});

test('dismissDialogs presses Escape in the same round when the Close click throws', async () => {
  const page = stubPage({ clickThrows: true });
  assert.deepEqual(await dismissDialogs(page), ["What's new in 2.0"]);
  assert.deepEqual(page.calls, ['click', 'Escape', 'hidden']);
});

test('dismissDialogs does not record a dialog that never closes', async () => {
  const page = stubPage({ stuck: true });
  assert.deepEqual(await dismissDialogs(page), []);
  assert.equal(page.calls.filter((c) => c === 'click').length, 3);
});
