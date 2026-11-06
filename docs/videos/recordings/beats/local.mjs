// Local feeds video. Targets: local.slug, local.title, local.description (the feed `create` makes), local.audio (an mp3 to upload),
// local.artwork (a square png for the create form), local.episodeTitle, local.episodeId (the id the upload gets, s01e01 for the first). Record in order; `create` and `upload` change the instance.
import { sleep, smoothScroll, scrollTo, jumpTo, typeSlow } from '../helpers.mjs';
const feed = (T) => `/feeds/${T.local.slug}`;
export const BEATS = {
  create: { dev: 'phone', url: '/add',
    run: async (p, T) => {
      await sleep(900); await p.getByRole('button', { name: /Create local feed/ }).click(); await sleep(900);
      await typeSlow(p.locator('#localTitle'), T.local.title, 85); await sleep(500);
      await typeSlow(p.locator('#localDescription'), T.local.description, 45); await sleep(900);
      await p.locator('#localArtwork').setInputFiles(T.local.artwork); await sleep(900);
      await scrollTo(p, p.getByRole('button', { name: /^Create feed$/ }), 500, 1600); await sleep(900);
      await p.getByRole('button', { name: /^Create feed$/ }).click();
      await p.waitForURL(new RegExp(`/feeds/${T.local.slug}`), { timeout: 20000 }); await sleep(3500);
    } },
  feedpage: { dev: 'laptop', url: feed,
    prep: async (p) => { await p.evaluate(() => window.scrollTo(0, 0)); },
    run: async (p) => { await sleep(2500); await smoothScroll(p, 380, 3000); await sleep(2000); } },
  upload: { dev: 'laptop', url: feed,
    prep: (p) => jumpTo(p, p.getByRole('button', { name: /^Add episode$/ }).first(), 420),
    run: async (p, T) => {
      await sleep(1500); await p.getByRole('button', { name: /^Add episode$/ }).first().click(); await p.locator('#add-ep-audio').waitFor(); await sleep(1200);
      await p.locator('#add-ep-audio').setInputFiles(T.local.audio); await sleep(1000);
      await typeSlow(p.locator('#add-ep-title'), T.local.episodeTitle, 70); await sleep(1000);
      await p.getByRole('button', { name: /^Add episode$/ }).last().click();
      await p.getByText(/Uploading/).waitFor({ state: 'hidden', timeout: 180000 }).catch(() => {}); await sleep(4000);
    } },
  // The first upload into an empty feed is not queued automatically, so processing is started from the episode page.
  process: { dev: 'laptop', url: (T) => `${feed(T)}/episodes/${T.local.episodeId}`,
    run: async (p) => { await sleep(2000); await p.getByRole('button', { name: /^(Re)?[Pp]rocess/ }).first().click(); await sleep(1800); await p.getByRole('menuitem').first().click(); await sleep(5500); } },
  queue: { dev: 'phone', url: '/queue', run: async (p) => { await sleep(1000); await smoothScroll(p, 300, 1500); await sleep(6000); } },
  result: { dev: 'laptop', url: feed,
    prep: async (p, T) => { await p.getByRole('link', { name: T.local.episodeTitle }).first().click(); await p.waitForURL(/episodes\//); await sleep(1500); await p.evaluate(() => window.scrollTo(0, 0)); },
    run: async (p) => { await sleep(2500); await scrollTo(p, p.getByText(/^Detected ads \(/i).first(), 130, 3200); await sleep(3000); } },
  dashboard: { dev: 'phone', url: '/', run: async (p, T) => { await sleep(1200); const card = p.getByText(new RegExp(`^${T.local.title}$`)).first(); await scrollTo(p, card, 260, 2200); await sleep(3500); } },
};
