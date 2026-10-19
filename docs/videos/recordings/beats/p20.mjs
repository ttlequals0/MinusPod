// Podcasting 2.0 video. Targets: p20.episode (slug/id of a processed episode with chapters), p20.searchTerm (a word in its transcript).
// The feedxml clip is not recorded: see docs/videos/README.md for how it is made from the served feed.
import { sleep, smoothScroll, jumpTo, typeSlow, openSection } from '../helpers.mjs';
const ep = (T) => `/feeds/${T.p20.episode}`;
export const BEATS = {
  chapters: { dev: 'laptop', url: ep,
    prep: (p) => jumpTo(p, p.getByText(/^Chapters$/).first(), 420),
    run: async (p) => { await sleep(6500); } },
  transcript: { dev: 'laptop', url: ep,
    prep: (p) => jumpTo(p, p.getByRole('button', { name: /View transcript/ }), 500),
    run: async (p, T) => { await sleep(1200); await p.getByRole('button', { name: /View transcript/ }).click(); await p.getByRole('dialog').waitFor(); await sleep(2500); await p.getByLabel(/Highlight ads/).check(); await sleep(2500); await typeSlow(p.getByRole('dialog').getByPlaceholder(/Word or phrase/), T.p20.searchTerm, 110); await sleep(3500); } },
  p20settings: { dev: 'phone', url: '/settings',
    prep: (p) => openSection(p, /^Transcripts & Chapters/, 90, { settings: true }),
    run: async (p) => { await sleep(1500); await smoothScroll(p, 1000, 5600); await sleep(1000); } },
};
