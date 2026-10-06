import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import PatternsPage from './PatternsPage';

const mockGetPatterns = vi.fn().mockResolvedValue([]);
const mockGetDetections = vi.fn().mockResolvedValue({
  detections: [], total: 0, page: 1, totalPages: 1, limit: 20,
  counts: {
    total: 0, needsReview: 0, pending: 0, rejected: 0,
    accepted: 0, confirmed: 0, dismissed: 0,
  },
  cutSummary: {
    count: 0, durationSeconds: 0, byCategory: {},
    distinctSponsors: 0, distinctPodcasts: 0,
  },
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

const mockCleanupStatus = vi.fn().mockResolvedValue({ pending: { total: 0, byKind: {} } });
const mockCleanupSuggestions = vi.fn().mockResolvedValue([]);
vi.mock('../api/patternCleanup', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patternCleanup')>()),
  getPatternCleanupStatus: (...a: unknown[]) => mockCleanupStatus(...a),
  getPatternCleanupSuggestions: (...a: unknown[]) => mockCleanupSuggestions(...a),
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

  it('shows a Stale badge on a stale community pattern', async () => {
    mockGetPatterns.mockResolvedValueOnce([
      {
        id: 2, scope: 'global', network_id: null, podcast_id: null,
        dai_platform: null, text_template: 'x'.repeat(60),
        intro_variants: '[]', outro_variants: '[]', sponsor: 'Acme',
        confirmation_count: 0, false_positive_count: 0, last_matched_at: null,
        created_at: '2020-01-01T00:00:00Z', created_from_episode_id: null,
        is_active: true, disabled_at: null, disabled_reason: null,
        source: 'community', community_id: 'abc12345', version: 1,
        trust: 'stale',
      },
    ]);
    renderPage();
    expect(await screen.findAllByText('Stale')).not.toHaveLength(0);
  });

  it('does not show a trust badge on an active local pattern', async () => {
    mockGetPatterns.mockResolvedValueOnce([
      {
        id: 3, scope: 'global', network_id: null, podcast_id: null,
        dai_platform: null, text_template: 'x'.repeat(60),
        intro_variants: '[]', outro_variants: '[]', sponsor: 'Acme',
        confirmation_count: 0, false_positive_count: 0,
        last_matched_at: '2026-08-01T00:00:00Z',
        created_at: '2026-01-01T00:00:00Z', created_from_episode_id: null,
        is_active: true, disabled_at: null, disabled_reason: null,
        source: 'local', trust: 'active',
      },
    ]);
    renderPage();
    await screen.findAllByText('Acme');
    expect(screen.queryByText('Stale')).toBeNull();
    expect(screen.queryByText('Unproven')).toBeNull();
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

  it('scrolls the selected tab into view inside the tab list on a cleanup deep link', async () => {
    const rect = (left: number, right: number) => ({
      left, right, top: 0, bottom: 44, width: right - left, height: 44,
      x: left, y: 0, toJSON: () => ({}),
    } as DOMRect);
    const bounds = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute('role') === 'tablist') return rect(0, 320);
      if (this.getAttribute('role') === 'tab' && this.getAttribute('aria-selected') === 'true') {
        return rect(330, 400);
      }
      return rect(0, 100);
    });
    try {
      renderPage('/patterns?tab=cleanup');
      const tablist = screen.getByRole('tablist');
      await screen.findByRole('tab', { name: 'Cleanup', selected: true });
      expect(tablist.scrollLeft).toBe(80);
    } finally {
      bounds.mockRestore();
    }
  });
});

describe('PatternsPage cleanup tab', () => {
  it('lists the tabs in order with Cleanup last', async () => {
    renderPage();
    await screen.findByRole('tab', { name: 'Patterns' });
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'Patterns', 'Detected Ads', 'Ad Review', 'Cleanup',
    ]);
  });

  it('shows the pending count on the Cleanup tab', async () => {
    mockCleanupStatus.mockResolvedValueOnce({ pending: { total: 3, byKind: { trim: 3 } } });
    renderPage();
    const tab = await screen.findByRole('tab', { name: /Cleanup/ });
    await waitFor(() => expect(tab.textContent).toBe('Cleanup3'));
    expect(tab.getAttribute('aria-label')).toBe('Cleanup, 3 pending');
  });

  it('does not refetch the cleanup badge status on every visit to the page', async () => {
    mockCleanupStatus.mockClear();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const mount = () => render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/patterns']}>
          <PatternsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const first = mount();
    await screen.findByRole('tab', { name: 'Patterns' });
    expect(mockCleanupStatus).toHaveBeenCalledTimes(1);
    first.unmount();

    mount();
    await screen.findByRole('tab', { name: 'Patterns' });
    expect(mockCleanupStatus).toHaveBeenCalledTimes(1);
  });

  it('opens the cleanup tab from the url and loads suggestions', async () => {
    renderPage('/patterns?tab=cleanup');
    const tab = await screen.findByRole('tab', { name: /Cleanup/ });
    expect(tab.getAttribute('aria-selected')).toBe('true');
    await waitFor(() => expect(mockCleanupSuggestions).toHaveBeenCalled());
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

describe('PatternsPage detected ads tab', () => {
  it('switches to the detected ads tab and requests cut detections', async () => {
    renderPage();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Detected Ads' }));
    await waitFor(() => expect(mockGetDetections).toHaveBeenCalled());
    expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({ status: 'accepted' });
  });

  it('opens the detected ads tab from the url', async () => {
    renderPage('/patterns?tab=detected-ads');
    const tab = await screen.findByRole('tab', { name: 'Detected Ads' });
    expect(tab.getAttribute('aria-selected')).toBe('true');
  });

  it('falls back to patterns for an unknown tab param', async () => {
    renderPage('/patterns?tab=banana');
    const tab = await screen.findByRole('tab', { name: 'Patterns' });
    expect(tab.getAttribute('aria-selected')).toBe('true');
  });
});

describe('PatternsPage loading state', () => {
  it('shows a layout skeleton while loading, not a page spinner', () => {
    mockGetPatterns.mockReturnValueOnce(new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId('skeleton-rows')).toBeDefined();
  });
});
