// UI tour video. Every beat is read-only on the instance: navigation, scrolling, opening dialogs and typing into search boxes only.
// Nothing is saved, confirmed, rated, recut, submitted, revealed or generated; the theme toggle only changes this browser's localStorage.
// Targets: demo.adsEpisode and demo.heldEpisode ("slug/id" of a processed episode with detected ads, and one with a held marker),
// demo.searchTerm (a word in a transcript), youtube.feed (slug of a YouTube feed), youtube.episode (episode id in it, with SponsorBlock marks),
// youtube.searchTerm (the show name typed into Add feed).
import { sleep, smoothScroll, jumpTo, typeSlow, waitForWaveform, expandSection, openSection } from '../helpers.mjs';
const epUrl = (se) => { const [slug, id] = se.split('/'); return `/feeds/${slug}/episodes/${id}`; };
const adsEp = (T) => epUrl(T.demo.adsEpisode);
const heldEp = (T) => epUrl(T.demo.heldEpisode);
const ytFeed = (T) => `/feeds/${T.youtube.feed}`;
const ytEp = (T) => `/feeds/${T.youtube.feed}/episodes/${T.youtube.episode}`;
const sbLine = (p) => p.getByText(/SponsorBlock/).first();
export const BEATS = {
  dashboard: { dev: 'phone', url: '/',
    run: async (p) => { await sleep(1500); await smoothScroll(p, 900, 4200); await sleep(1000); } },
  queue: { dev: 'phone', url: '/queue',
    run: async (p) => { await sleep(1200); await smoothScroll(p, 500, 3000); await sleep(1800); } },
  ads: { dev: 'laptop', url: adsEp,
    prep: (p) => jumpTo(p, p.getByText(/^Detected ads \(/i).first(), 150),
    run: async (p) => { await sleep(2500); await smoothScroll(p, 520, 3200); await sleep(1500); } },
  // The inline editor opens with Edit ads and is closed again without saving.
  editor: { dev: 'laptop', url: adsEp,
    prep: (p) => jumpTo(p, p.getByText(/^Detected ads \(/i).first(), 150),
    run: async (p) => {
      await sleep(1200); await p.getByRole('button', { name: 'Edit ads' }).click(); await waitForWaveform(p); await sleep(2500);
      await p.keyboard.press('Space'); await sleep(3000); await p.keyboard.press('Space'); await sleep(800);
    } },
  // Opens the held marker in the review dialog and closes it with Escape: no Save, so nothing is confirmed or recut.
  review: { dev: 'laptop', url: heldEp,
    prep: (p) => jumpTo(p, p.getByText(/^Held for review \(/i).first(), 150),
    run: async (p) => {
      await sleep(2000); await p.getByTestId('open-editor-held-0').click(); await p.getByRole('dialog').waitFor(); await waitForWaveform(p); await sleep(2500);
      await p.keyboard.press('Space'); await sleep(2500); await p.keyboard.press('Space'); await sleep(500); await p.keyboard.press('Escape'); await sleep(800);
    } },
  patterns: { dev: 'laptop', url: '/patterns',
    prep: (p) => jumpTo(p, p.locator('label[for="patterns-scope"]'), 130),
    run: async (p) => { await sleep(2000); await smoothScroll(p, 520, 3200); await sleep(800); } },
  search: { dev: 'phone', url: '/search',
    prep: async (p) => { await p.locator('#library-search').waitFor(); },
    run: async (p, T) => { await sleep(1000); await typeSlow(p.locator('#library-search'), T.demo.searchTerm, 110); await sleep(2000); await smoothScroll(p, 500, 2400); await sleep(800); } },
  stats: { dev: 'laptop', url: '/stats',
    run: async (p) => { await sleep(1800); await smoothScroll(p, 800, 3600); await sleep(600); } },
  settings: { dev: 'laptop', url: '/settings',
    prep: (p) => openSection(p, /^AI models/, 130, { settings: true }),
    run: async (p) => { await sleep(2500); await smoothScroll(p, 500, 3000); await sleep(500); } },
  // Picks theme buttons in Settings, then toggles light and dark; the choice lives in localStorage only.
  themes: { dev: 'laptop', url: '/settings',
    prep: (p) => openSection(p, /^Appearance/, 130, { settings: true }),
    run: async (p) => {
      await sleep(1200);
      for (const n of ['Mocha', 'Nord', 'Pink', 'Gruvbox', 'Solarized']) { await p.getByRole('button', { name: new RegExp(`^${n}(\\s*\\(dark only\\))?$`) }).click(); await sleep(900); }
      const t = p.getByRole('button', { name: 'Toggle theme' });
      await t.click(); await sleep(1300); await t.click(); await sleep(1300);
      await p.getByRole('button', { name: /^Default(\s*\(dark only\))?$/ }).click(); await sleep(1200);
    } },
  // Typing only searches; Look up previews the source and adds nothing, and the preview is not added.
  ytadd: { dev: 'phone', url: '/add',
    prep: async (p) => { await p.getByPlaceholder(/Podcast name, RSS URL or YouTube URL/).waitFor(); },
    run: async (p, T) => {
      await sleep(1000); await typeSlow(p.getByPlaceholder(/Podcast name, RSS URL or YouTube URL/), T.youtube.searchTerm, 90);
      const yt = p.getByRole('heading', { name: 'YouTube', exact: true }); await yt.waitFor({ timeout: 20000 }); await sleep(1200);
      await smoothScroll(p, await yt.evaluate((el) => el.getBoundingClientRect().top - 120), 1600); await sleep(2000);
      await p.getByRole('button', { name: /^Look up/ }).first().click(); await sleep(3500);
    } },
  ytfeed: { dev: 'laptop', url: ytFeed,
    prep: async (p) => { await p.evaluate(() => window.scrollTo(0, 0)); },
    run: async (p) => {
      await sleep(2500); await smoothScroll(p, 360, 2200); await sleep(1200);
      const h = await expandSection(p, /^YouTube/); await smoothScroll(p, await h.evaluate((el) => el.getBoundingClientRect().top - 130), 1400); await sleep(2500);
    } },
  // Scrolls to the SponsorBlock line, then the Rate this episode card (stars are not clicked).
  ytepisode: { dev: 'laptop', url: ytEp,
    prep: async (p) => { await p.evaluate(() => window.scrollTo(0, 0)); },
    run: async (p) => {
      await sleep(1500); await smoothScroll(p, await sbLine(p).evaluate((el) => el.getBoundingClientRect().top - 220), 1600); await sleep(2500);
      const rate = p.getByRole('heading', { name: 'Rate this episode' }); await smoothScroll(p, await rate.evaluate((el) => el.getBoundingClientRect().top - 160), 2200); await sleep(2500);
    } },
  // The user ID input is a password field: hideSensitive blanks it, and it is never revealed, copied or generated.
  ytsettings: { dev: 'laptop', url: '/settings',
    prep: (p) => openSection(p, /^YouTube/, 130, { settings: true }),
    run: async (p) => { await sleep(2500); await smoothScroll(p, 560, 3600); await sleep(1500); } },
};
