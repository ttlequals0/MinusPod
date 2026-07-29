import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import PatternsPage from './PatternsPage';

const mockGetPatterns = vi.fn().mockResolvedValue([]);
const mockGetDetections = vi.fn().mockResolvedValue({
  detections: [], total: 0, page: 1, totalPages: 1, limit: 20,
});

vi.mock('../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patterns')>()),
  getPatterns: (...a: unknown[]) => mockGetPatterns(...a),
  getPatternStats: vi.fn().mockResolvedValue({
    total: 0, active: 0, inactive: 0,
    by_scope: { global: 0, network: 0, podcast: 0 },
    no_sponsor: 0, never_matched: 0, stale_count: 0,
    high_false_positive_count: 0,
    stale_patterns: [], no_sponsor_patterns: [], high_false_positive_patterns: [],
  }),
  getMergeSuggestions: vi.fn().mockResolvedValue([]),
}));
vi.mock('../api/detections', () => ({
  getDetections: (...a: unknown[]) => mockGetDetections(...a),
}));
vi.mock('../api/community', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/community')>()),
  getCommunitySyncStatus: vi.fn().mockResolvedValue({
    enabled: false, cron: '', lastRun: null, lastError: null,
    manifestVersion: null, lastSummary: null,
  }),
}));

function renderPage(initialEntry = '/patterns') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <PatternsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('PatternsPage tabs', () => {
  it('shows the patterns tab by default', async () => {
    renderPage();
    const tab = await screen.findByRole('tab', { name: 'Patterns' });
    expect(tab.getAttribute('aria-selected')).toBe('true');
  });

  it('renders a segment category badge on a pattern row', async () => {
    mockGetPatterns.mockResolvedValueOnce([
      {
        id: 1, scope: 'global', network_id: null, podcast_id: null,
        dai_platform: null, text_template: 'x'.repeat(60),
        intro_variants: '[]', outro_variants: '[]', sponsor: 'Acme',
        confirmation_count: 0, false_positive_count: 0, last_matched_at: null,
        created_at: '2026-01-01T00:00:00Z', created_from_episode_id: null,
        is_active: true, disabled_at: null, disabled_reason: null,
        category: 'cross_promo',
      },
    ]);
    renderPage();
    expect(await screen.findAllByText('Cross-promo')).not.toHaveLength(0);
  });

  it('switches to the ad review tab on click', async () => {
    renderPage();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Ad Review' }));
    expect(mockGetDetections).toHaveBeenCalled();
  });

  it('opens the ad review tab from the URL', async () => {
    renderPage('/patterns?tab=ad-review');
    const tab = await screen.findByRole('tab', { name: 'Ad Review' });
    expect(tab.getAttribute('aria-selected')).toBe('true');
  });
});

describe('PatternsPage category filter', () => {
  function pattern(id: number, category: string | null, sponsor: string) {
    return {
      id, scope: 'global', network_id: null, podcast_id: null,
      dai_platform: null, text_template: 'x'.repeat(60),
      intro_variants: '[]', outro_variants: '[]', sponsor,
      confirmation_count: 0, false_positive_count: 0, last_matched_at: null,
      created_at: '2026-01-01T00:00:00Z', created_from_episode_id: null,
      is_active: true, disabled_at: null, disabled_reason: null,
      category,
    };
  }

  function seed() {
    mockGetPatterns.mockResolvedValue([
      pattern(1, 'sponsor', 'Acme'),
      pattern(2, 'cross_promo', 'Beta Co'),
      pattern(3, null, 'Gamma Co'),
    ]);
  }

  it('narrows the list to one category', async () => {
    seed();
    renderPage();
    const user = userEvent.setup();
    await screen.findAllByText('Beta Co');
    await user.selectOptions(screen.getByLabelText('Category:'), 'cross_promo');
    expect(await screen.findAllByText('Beta Co')).not.toHaveLength(0);
    expect(screen.queryByText('Acme')).toBeNull();
    expect(screen.queryByText('Gamma Co')).toBeNull();
  });

  it('uncategorized shows only patterns with no category', async () => {
    seed();
    renderPage();
    const user = userEvent.setup();
    await screen.findAllByText('Gamma Co');
    await user.selectOptions(screen.getByLabelText('Category:'), 'none');
    expect(await screen.findAllByText('Gamma Co')).not.toHaveLength(0);
    expect(screen.queryByText('Acme')).toBeNull();
    expect(screen.queryByText('Beta Co')).toBeNull();
  });

  it('all categories keeps every pattern', async () => {
    seed();
    renderPage();
    await screen.findAllByText('Acme');
    expect(screen.queryAllByText('Beta Co')).not.toHaveLength(0);
    expect(screen.queryAllByText('Gamma Co')).not.toHaveLength(0);
  });
});
