// YouTube feeds video. Targets: youtube.feed (slug of a YouTube feed), youtube.episode (a completed episode in it), youtube.searchTerm (the show name typed into Add feed).
// Every beat is read-only on the instance; writes (rating, submit, generate) are answered by in-page mocks and the GETs that gate the UI are patched in the browser.
import { sleep, smoothScroll, scrollTo, jumpTo, typeSlow, expandSection, openSection } from '../helpers.mjs';
const ytFeed = (T) => `/feeds/${T.youtube.feed}`;
const ytEp = (T) => `/feeds/${T.youtube.feed}/episodes/${T.youtube.episode}`;
const sbLine = (p) => p.getByText(/SponsorBlock/).first();
const api = (path) => new RegExp(`/api/v1${path}(\\?.*)?$`);
const reply = (route, body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
const FAKE_ID = '0123456789abcdef'.repeat(3);  // 48 hex characters, obviously not a real ID
const SEGMENTS = [
  { start: 1675, end: 1744, category: 'sponsor', sbCategory: 'sponsor', evidence: ['reviewer confirmed', 'fingerprint'] },
  { start: 1990, end: 2048, category: 'sponsor', sbCategory: 'sponsor', evidence: ['verified span', 'pattern'] },
];
const SB_STATE = { state: 'done', attempts: 1, nextCheckAt: null, segmentCount: 2, note: null, lastError: null };
const NOW = () => new Date().toISOString();

// Patches the episode payload so the rating card is empty and the submit button is allowed; `st` holds the mocked writes.
const mockEpisode = (p, st) => p.route(api('/feeds/[^/]+/episodes/[^/?]+'), async (route) => {
  if (route.request().method() !== 'GET') return route.fallback();
  const res = await route.fetch(); const body = await res.json();
  body.sponsorBlock = { ...SB_STATE, ...(body.sponsorBlock || {}), submitAllowed: true, submissions: st.submissions };
  body.rating = st.rating;
  return reply(route, body);
});

export const BEATS = {
  // Typing only searches; Look up previews the source and adds nothing, and the RSS matches are not added.
  add: { dev: 'phone', url: '/add',
    prep: async (p) => { await p.getByPlaceholder(/Podcast name, RSS URL or YouTube URL/).waitFor(); },
    run: async (p, T) => {
      await sleep(1000); await typeSlow(p.getByPlaceholder(/Podcast name, RSS URL or YouTube URL/), T.youtube.searchTerm, 90);
      const yt = p.getByRole('heading', { name: 'YouTube', exact: true }); await yt.waitFor({ timeout: 20000 });
      const badge = p.getByText('Podcast', { exact: true }).first(); await badge.waitFor({ timeout: 20000 }); await sleep(800);
      await smoothScroll(p, await yt.evaluate((el) => el.getBoundingClientRect().top - 120), 1400); await sleep(1500);
      await badge.locator('xpath=ancestor::div[contains(@class,"rounded-lg")][1]').getByRole('button', { name: /^Look up/ }).click();
      const rss = p.getByRole('heading', { name: /Or add by RSS feed/ }); await rss.waitFor({ timeout: 30000 }).catch(() => {}); await sleep(1000);
      if (await rss.count()) await smoothScroll(p, await rss.evaluate((el) => el.getBoundingClientRect().top - 260), 1200);
      await sleep(2500);
    } },
  feedpage: { dev: 'laptop', url: ytFeed,
    prep: async (p) => { await p.evaluate(() => window.scrollTo(0, 0)); },
    run: async (p) => {
      await sleep(1800); await smoothScroll(p, 340, 1800); await sleep(800);
      const h = await expandSection(p, /^YouTube/); await smoothScroll(p, await h.evaluate((el) => el.getBoundingClientRect().top - 130), 1400); await sleep(2200);
    } },
  episode: { dev: 'laptop', url: ytEp,
    prep: async (p) => { await p.evaluate(() => window.scrollTo(0, 0)); },
    run: async (p) => {
      await sleep(1200); await smoothScroll(p, await sbLine(p).evaluate((el) => el.getBoundingClientRect().top - 220), 1500); await sleep(2000);
      await scrollTo(p, p.getByRole('heading', { name: /^Detected ads \(/ }).first(), 130, 1800); await sleep(2800);
    } },
  // Mocked: PUT /feeds/{slug}/episodes/{id}/rating, plus the episode GET (rating null, submitAllowed true).
  rate: { dev: 'laptop', url: ytEp,
    mock: async (p) => {
      const st = { rating: null, submissions: [] };
      await mockEpisode(p, st);
      await p.route(api('/feeds/[^/]+/episodes/[^/]+/rating'), (route) => {
        if (route.request().method() !== 'PUT') return route.fallback();
        const { rating, notes } = route.request().postDataJSON();
        st.rating = { value: rating, notes: rating < 5 ? notes ?? null : null, ratedAt: NOW() };
        return reply(route, { rating: st.rating, confirmedMarkers: 1 });
      });
    },
    prep: (p) => jumpTo(p, p.getByRole('heading', { name: 'Rate this episode' }), 300),
    run: async (p) => {
      await sleep(1200); await p.getByRole('radio', { name: '5 stars' }).click(); await sleep(1200);
      await p.getByRole('button', { name: 'Save', exact: true }).click();
      await p.getByText(/^Saved\./).waitFor({ timeout: 10000 }); await sleep(3200);
    } },
  // Mocked: GET .../sponsorblock/candidates, POST .../sponsorblock/submit, plus the episode GET (submitAllowed true, submissions from the mock).
  submit: { dev: 'laptop', url: ytEp,
    mock: async (p) => {
      const st = { rating: null, submissions: [] };
      await mockEpisode(p, st);
      await p.route(api('/feeds/[^/]+/episodes/[^/]+/sponsorblock/candidates'), (route) => {
        if (route.request().method() !== 'GET') return route.fallback();
        const done = (s) => st.submissions.find((x) => x.start === s.start && x.end === s.end) || null;
        return reply(route, { allowed: true, reason: null, segments: SEGMENTS.map((s) => ({ ...s, submission: done(s) })) });
      });
      await p.route(api('/feeds/[^/]+/episodes/[^/]+/sponsorblock/submit'), (route) => {
        if (route.request().method() !== 'POST') return route.fallback();
        const results = route.request().postDataJSON().segments.map(({ start, end }) => ({
          start, end, category: 'sponsor', status: 'submitted', at: NOW(), uuid: `mock-${start}-${end}`,
        }));
        st.submissions.push(...results);
        return reply(route, { results });
      });
    },
    prep: (p) => jumpTo(p, p.getByRole('button', { name: 'Submit to SponsorBlock' }), 500),
    run: async (p) => {
      await sleep(1200); await p.getByRole('button', { name: 'Submit to SponsorBlock' }).click();
      const dlg = p.getByRole('dialog'); await dlg.waitFor(); await p.locator('label[for="sb-seg-1675-1744"]').waitFor({ timeout: 10000 }); await sleep(2200);
      await dlg.getByRole('button', { name: /^Play segment/ }).first().click({ timeout: 5000 }).catch(() => {}); await p.mouse.move(2, 2); await sleep(2000);
      await dlg.getByRole('button', { name: /^Pause segment/ }).first().click({ timeout: 2000 }).catch(() => {}); await p.mouse.move(2, 2); await sleep(600);
      await p.locator('label[for="sb-seg-1675-1744"]').click(); await sleep(1200);
      await dlg.getByRole('button', { name: /^Submit 1 segment$/ }).click();
      await dlg.waitFor({ state: 'hidden', timeout: 10000 });
      const out = p.getByText(/Submitted to SponsorBlock/).first(); await out.waitFor({ timeout: 10000 });
      await scrollTo(p, out, 220, 1500); await sleep(3000);
    } },
  // Mocked: GET /status with a SponsorBlock offline service, unreachable with 2 episodes waiting.
  status: { dev: 'phone', url: '/',
    mock: async (p) => {
      await p.route(api('/status'), (route) => reply(route, {
        currentJob: null, jobs: [], queueLength: 0, queuedEpisodes: [], feedRefreshes: [], lastUpdated: Date.now() / 1000, revision: 1,
        hold: { queuePaused: false, holdUntil: null, holdSince: null, offlineHeld: 0,
          offlineServices: [{ service: 'sponsorblock', held: 2, reachable: false, checkedAt: NOW() }] },
      }));
    },
    run: async (p) => {
      await p.getByRole('button', { name: 'Expand status bar' }).waitFor({ timeout: 15000 }); await sleep(2500);
      await p.getByRole('button', { name: 'Expand status bar' }).click(); await sleep(3500);
    } },
  // Mocked: GET /settings/youtube (submissions on, no ID until Generate), POST /settings/youtube/sb-user-id/generate, GET /settings/youtube/sb-user-id.
  settings: { dev: 'laptop', url: '/settings',
    mock: async (p) => {
      let generated = false;
      await p.route(api('/settings/youtube'), async (route) => {
        if (route.request().method() !== 'GET') return route.fallback();
        const res = await route.fetch(); const body = await res.json();
        return reply(route, { ...body, sbSubmitEnabled: true, sbUserIdConfigured: generated, sbUserIdGenerated: generated });
      });
      await p.route(api('/settings/youtube/sb-user-id/generate'), (route) => {
        if (route.request().method() !== 'POST') return route.fallback();
        generated = true; return reply(route, { sbUserId: FAKE_ID });
      });
      await p.route(api('/settings/youtube/sb-user-id'), (route) => (generated && route.request().method() === 'GET' ? reply(route, { sbUserId: FAKE_ID }) : route.fallback()));
    },
    prep: async (p) => { await openSection(p, /^YouTube/, 100, { settings: true }); await p.evaluate(() => document.getElementById('yt-sb-user-id')?.setAttribute('type', 'text')); await jumpTo(p, p.getByRole('heading', { name: 'SponsorBlock submissions' }), 100); },
    run: async (p) => {
      await sleep(2800); await await p.getByRole('button', { name: 'Generate', exact: true }).click();
      await p.getByTestId('sb-user-id-value').waitFor({ timeout: 10000 }); await sleep(2400);
      await p.getByRole('button', { name: 'Hide' }).click(); await sleep(1800);
      await p.getByRole('button', { name: 'Show' }).click(); await sleep(2800);
    } },
};
