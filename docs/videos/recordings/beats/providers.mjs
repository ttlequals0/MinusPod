// Providers and cost video. Targets: providers.episode (slug/id of a processed episode, for its spend line).
import { sleep, smoothScroll, jumpTo, openSection } from '../helpers.mjs';
export const BEATS = {
  provider: { dev: 'laptop', url: '/settings',
    prep: (p) => openSection(p, /^LLM Provider/, 130, { settings: true }),
    run: async (p) => { await sleep(2200); await smoothScroll(p, 1150, 6500); await sleep(1500); } },
  models: { dev: 'laptop', url: '/settings',
    prep: (p) => openSection(p, /^AI Models/, 130, { settings: true }),
    run: async (p) => { await sleep(6500); } },
  failover: { dev: 'laptop', url: '/settings',
    prep: (p) => openSection(p, /^Failover/, 130, { settings: true }),
    run: async (p) => { await sleep(2500); await smoothScroll(p, 420, 3000); await sleep(2000); } },
  spend: { dev: 'laptop', url: '/stats',
    prep: (p) => jumpTo(p, p.getByRole('heading', { name: /^Spend$/ }), 130),
    run: async (p) => { await sleep(2500); await smoothScroll(p, 900, 5000); await sleep(2000); } },
  llmcalls: { dev: 'phone', url: '/stats',
    prep: (p) => jumpTo(p, p.getByRole('heading', { name: /^LLM calls$/ }), 110),
    run: async (p) => { await sleep(1500); await smoothScroll(p, 800, 5000); await sleep(1200); } },
  episodecost: { dev: 'laptop', url: (T) => `/feeds/${T.providers.episode}`,
    prep: (p) => jumpTo(p, p.getByText(/^Total spend/).first(), 260),
    run: async (p) => { await sleep(6000); } },
};
