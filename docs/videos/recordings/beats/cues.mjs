// Audio cues video. Targets: cues.feed (has cue templates), cues.candidateEpisode (episode to scan for cues),
// cues.snappedEpisode (episode whose ads carry Cue snapped badges), cues.scanEpisodes (three title fragments to pick in the cross-episode scan).
import { sleep, smoothScroll, scrollTo, jumpTo, waitForWaveform, expandSection, openSection } from '../helpers.mjs';
const feed = (T) => `/feeds/${T.cues.feed}`;
export const BEATS = {
  candidates: { dev: 'laptop', url: (T) => `${feed(T)}/episodes/${T.cues.candidateEpisode}`,
    // The modal is opened once off camera so the audio is cached and the waveform draws at once on camera.
    prep: async (p) => { const h = await expandSection(p, /^Audio cues/i); const b = p.getByRole('button', { name: /Find audio cues/ }); if (await b.count()) await b.first().click(); await p.getByText(/Scanning audio/).waitFor({ state: 'hidden', timeout: 150000 }).catch(() => {}); await sleep(600);
      await p.getByRole('button', { name: /Make template/ }).first().click(); await p.getByRole('dialog').waitFor(); await waitForWaveform(p); await p.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click(); await sleep(800); await jumpTo(p, h, 130); },
    run: async (p) => { await sleep(3200); await p.getByRole('button', { name: /Make template/ }).first().click(); await p.getByRole('dialog').waitFor(); await waitForWaveform(p); await sleep(1500); await p.getByRole('dialog').getByRole('button', { name: 'Play selection' }).click(); await p.mouse.move(2, 2); await sleep(4500); } },
  templates: { dev: 'laptop', url: feed,
    prep: (p) => openSection(p, /^Audio cue templates/i),
    run: async (p) => { await sleep(2200); await scrollTo(p, p.getByText(/^Cue health/).first(), 520, 3600); await sleep(1500); } },
  // The scan itself runs off camera; the clip shows its result list.
  crossscan: { dev: 'laptop', url: feed,
    prep: async (p, T) => {
      await expandSection(p, /^Audio cue templates/i); await p.getByRole('button', { name: /Find across episodes/ }).click(); const d = p.getByRole('dialog'); await d.waitFor(); await sleep(800);
      for (const t of T.cues.scanEpisodes) await d.getByText(t).first().click();
      await sleep(600); await d.getByRole('button', { name: /^Scan$/ }).click();
      await d.getByText(/Make template|No recurring|candidates?/i).first().waitFor({ timeout: 300000 }).catch(() => {}); await sleep(800);
    },
    run: async (p) => { await sleep(7000); } },
  snapped: { dev: 'laptop', url: (T) => `${feed(T)}/episodes/${T.cues.snappedEpisode}`,
    prep: (p) => jumpTo(p, p.getByText(/^Detected ads \(/i).first(), 130),
    run: async (p) => { await sleep(2500); await smoothScroll(p, 760, 4200); await sleep(3000); } },
  cuesettings: { dev: 'phone', url: '/settings',
    prep: (p) => openSection(p, /^Audio cue detection/i, 90, { settings: true }),
    run: async (p) => { await sleep(1200); await smoothScroll(p, 900, 5200); await sleep(800); } },
};
