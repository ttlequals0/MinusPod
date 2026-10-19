// Review queue video: a held detection is checked on the waveform, confirmed, and the episode is recut.
// Targets: review.feed (slug with a held marker in its queue), review.heldEpisode (episode id with one held marker).
import { sleep, smoothScroll, jumpTo, waitForWaveform } from '../helpers.mjs';
const ep = (T) => `/feeds/${T.review.feed}/episodes/${T.review.heldEpisode}`;
const prepHeld = (p) => jumpTo(p, p.getByText(/^Held for Review \(/).first(), 150);
const openModal = async (p) => { await p.getByTestId('open-editor-held-0').click(); await p.getByRole('dialog').waitFor(); await waitForWaveform(p); await sleep(400); };
export const BEATS = {
  queue: { dev: 'phone', url: '/patterns?tab=ad-review',
    prep: async (p, T) => { const f = p.locator('#ad-review-feed'); await f.waitFor(); await sleep(800); await f.selectOption(T.review.feed); await sleep(1800); await p.evaluate(() => window.scrollTo(0, 0)); },
    run: async (p) => { await sleep(900); await smoothScroll(p, 1700, 5200); await sleep(700); } },
  held: { dev: 'laptop', url: ep,
    prep: prepHeld,
    run: async (p) => { await sleep(6000); } },
  modal: { dev: 'laptop', url: ep,
    prep: prepHeld,
    run: async (p) => { await sleep(900); await openModal(p); await sleep(1800); await p.keyboard.press('Space'); await sleep(7500); await p.keyboard.press('Space'); await sleep(600); } },
  // Save confirms the held marker as detected; when it is the last held marker the recut starts at once.
  confirm: { dev: 'laptop', url: ep,
    prep: async (p) => { await prepHeld(p); await openModal(p); await sleep(800); },
    run: async (p) => { await sleep(1200); await p.getByRole('button', { name: /^Save/ }).click(); await sleep(12000); } },
  after: { dev: 'laptop', url: ep,
    prep: (p) => jumpTo(p, p.getByText(/^Detected Ads \(/).first(), 150),
    run: async (p) => { await sleep(6500); } },
};
