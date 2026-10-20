// Patterns video: the learned-pattern list, a pattern created while confirming a held detection, its detail and edit
// forms, merge suggestions, and Pattern Cleanup from run to approval. Targets: patterns.feed and patterns.heldEpisode
// (an episode with one held marker; the create beat confirms it with a sponsor name, which learns a pattern and recuts),
// patterns.sponsor (the name to give it), patterns.patternId (the pattern the detail beat opens after searching for that name), patterns.editSearch
// (a search term whose first result is an uncategorized pattern; the edit beat sets its category to Sponsor).
import { sleep, smoothScroll, jumpTo, typeSlow, waitForWaveform, openSection } from '../helpers.mjs';
const findPattern = async (p, T, term = T.patterns.sponsor) => { const s = p.locator('#patterns-search'); await s.waitFor(); await s.fill(term); await sleep(1500); await jumpTo(p, p.getByRole('table').first(), 140); };
// Opens one pattern's detail dialog: the row carrying `#<id>`, or the first row when no id is given.
const openDetail = async (p, id) => { const row = id ? p.getByRole('row').filter({ hasText: `#${id}` }).first() : p.getByRole('row').nth(1); await row.click(); await p.getByRole('dialog').waitFor(); await sleep(600); };
export const BEATS = {
  list: { dev: 'laptop', url: '/patterns',
    prep: async (p) => { await jumpTo(p, p.getByText(/^Scope:$/).first(), 130); },
    run: async (p) => { await sleep(2500); await smoothScroll(p, 520, 3200); await sleep(2500); } },
  merge: { dev: 'laptop', url: '/patterns',
    prep: async (p) => { await jumpTo(p, p.getByText(/^Merge suggestions$/).first(), 130); },
    run: async (p) => { await sleep(2500); await smoothScroll(p, 420, 3000); await sleep(2000); } },
  // Confirming a held marker with a sponsor name records the correction, learns a pattern for the feed and recuts.
  create: { dev: 'laptop', url: (T) => `/feeds/${T.patterns.feed}/episodes/${T.patterns.heldEpisode}`,
    prep: async (p) => { await jumpTo(p, p.getByText(/^Held for Review \(/).first(), 150); },
    run: async (p, T) => {
      await sleep(1500); await p.getByTestId('open-editor-held-0').click(); await p.getByRole('dialog').waitFor(); await waitForWaveform(p); await sleep(1500);
      // A marker matched to a known sponsor shows the name as text with an edit link; open the field, then type the name.
      const dlg = p.getByRole('dialog'), editLink = dlg.getByRole('button', { name: /^edit$/ });
      if (await editLink.count()) { await editLink.click(); await sleep(600); }
      const sponsor = dlg.getByPlaceholder(/BetterHelp/); await sponsor.fill(''); await typeSlow(sponsor, T.patterns.sponsor, 90); await sleep(1200);
      await p.getByRole('dialog').getByRole('button', { name: /^Save/ }).click(); await sleep(5000);
    } },
  detail: { dev: 'laptop', url: '/patterns',
    prep: async (p, T) => { await findPattern(p, T); },
    run: async (p, T) => { await sleep(1500); await openDetail(p, T.patterns.patternId); await sleep(6000); } },
  // An uncategorized pattern gets its category set; uncategorized already counts as sponsor, so nothing is recut.
  edit: { dev: 'laptop', url: '/patterns',
    prep: async (p, T) => { await findPattern(p, T, T.patterns.editSearch); await openDetail(p); },
    run: async (p) => {
      await sleep(1200); await p.getByRole('dialog').getByRole('button', { name: /^Edit$/ }).click(); await sleep(1800);
      await p.getByRole('dialog').locator('select').first().selectOption('sponsor'); await sleep(1500);
      await p.getByRole('dialog').getByRole('button', { name: /^Save Changes$/ }).click(); await sleep(3500);
    } },
  cleanuprun: { dev: 'laptop', url: '/settings',
    prep: (p) => openSection(p, /^Pattern Cleanup/, 110, { settings: true }),
    run: async (p) => { await sleep(2500); await p.getByRole('button', { name: /^Run now$/ }).click(); await sleep(5500); } },
  // Record once the run has finished and the Cleanup tab lists pending suggestions. Approve the first one, then find it
  // under the Approved filter and undo it, so the pattern ends unchanged.
  cleanup: { dev: 'laptop', url: '/patterns?tab=cleanup',
    prep: async (p) => { await p.getByRole('list', { name: 'Cleanup suggestions' }).waitFor({ timeout: 20000 }); await sleep(800); },
    run: async (p) => {
      await sleep(3000); await smoothScroll(p, 260, 2000); await sleep(1500);
      const first = p.getByRole('listitem').first(), pid = (await first.innerText()).match(/#(\d+)/)[1];  // the card names its pattern
      await first.getByRole('button', { name: /^Approve/ }).click(); await sleep(3000);
      await p.locator('#cleanup-status').selectOption('approved'); await sleep(2500);
      await p.getByRole('listitem').filter({ hasText: `#${pid}` }).first().getByRole('button', { name: /^Undo/ }).click(); await sleep(3500);
    } },
  cleanupstats: { dev: 'phone', url: '/stats',
    prep: (p) => jumpTo(p, p.getByRole('heading', { name: /^Pattern cleanup activity$/ }), 110),
    run: async (p) => { await sleep(1500); await smoothScroll(p, 900, 5200); await sleep(1200); } },
};
