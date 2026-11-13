import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
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

vi.mock('../api/sponsors', () => ({
  getSponsors: vi.fn().mockResolvedValue([]),
  deleteSponsor: vi.fn(), addSponsor: vi.fn(), updateSponsor: vi.fn(),
}));
vi.mock('../api/community', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/community')>()),
  getCommunitySyncStatus: vi.fn().mockResolvedValue({
    enabled: false, cron: '', lastRun: null, lastError: null,
    manifestVersion: null, lastSummary: null,
  }),
  getTagVocabulary: vi.fn().mockResolvedValue({
    vocabulary_version: 1, all_tags: [], podcast_genres: [],
    sponsor_industries: [], special_tags: [],
  }),
  updateSponsorTags: vi.fn(),
}));

const mockCleanupStatus = vi.fn().mockResolvedValue({ pending: { total: 0, byKind: {} } });
const mockCleanupSuggestions = vi.fn().mockResolvedValue([]);
vi.mock('../api/patternCleanup', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patternCleanup')>()),
  getPatternCleanupStatus: (...a: unknown[]) => mockCleanupStatus(...a),
  getPatternCleanupSuggestions: (...a: unknown[]) => mockCleanupSuggestions(...a),
}));

function LocationProbe() {
  return <div data-testid="loc">{useLocation().search}</div>;
}

function renderPage(initialEntry = '/patterns') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <PatternsPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('PatternsPage tabs', () => {
  it('has Sponsors as the last tab and renders it in the matching panel', async () => {
    renderPage('/patterns?tab=sponsors&q=acme');
    const tabs = await screen.findAllByRole('tab');
    const last = tabs[tabs.length - 1];
    expect(last.textContent).toBe('Sponsors');
    expect(last.getAttribute('aria-selected')).toBe('true');
    const panel = document.getElementById(last.getAttribute('aria-controls')!);
    expect(panel?.getAttribute('role')).toBe('tabpanel');
    const search = await screen.findByPlaceholderText('Search by name, alias, category...');
    expect(panel?.contains(search)).toBe(true);
    expect((search as HTMLInputElement).value).toBe('acme');
  });

  it('does not push the sponsors ?q= into the patterns search', async () => {
    const user = userEvent.setup();
    renderPage('/patterns?tab=sponsors&q=acme');
    await user.click(await screen.findByRole('tab', { name: 'Patterns' }));
    const box = await screen.findByPlaceholderText(/search/i);
    expect((box as HTMLInputElement).value).toBe('');
  });

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
    await user.click(screen.getByRole('tab', { name: 'Ad review' }));
    expect(mockGetDetections).toHaveBeenCalled();
  });

  it('opens the ad review tab from the URL', async () => {
    renderPage('/patterns?tab=ad-review');
    const tab = await screen.findByRole('tab', { name: 'Ad review' });
    expect(tab.getAttribute('aria-selected')).toBe('true');
  });

  it('selects Cleanup from its deep link', async () => {
    renderPage('/patterns?tab=cleanup');
    expect(await screen.findByRole('tab', { name: 'Cleanup', selected: true })).toBeDefined();
  });
});

describe('PatternsPage cleanup tab', () => {
  it('lists the tabs in order with Sponsors last', async () => {
    renderPage();
    await screen.findByRole('tab', { name: 'Patterns' });
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'Patterns', 'Detected ads', 'Ad review', 'Cleanup', 'Sponsors',
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
    await user.selectOptions(screen.getByLabelText('Category'), 'cross_promo');
    expect(await screen.findAllByText('Beta Co')).not.toHaveLength(0);
    expect(screen.queryByText('Acme')).toBeNull();
    expect(screen.queryByText('Gamma Co')).toBeNull();
  });

  it('uncategorized shows only patterns with no category', async () => {
    seed();
    renderPage();
    const user = userEvent.setup();
    await screen.findAllByText('Gamma Co');
    await user.selectOptions(screen.getByLabelText('Category'), 'none');
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

describe('PatternsPage audio column', () => {
  function audioPattern(id: number, audio?: Record<string, unknown>) {
    return {
      id, scope: 'global', network_id: null, podcast_id: null,
      dai_platform: null, text_template: 'x'.repeat(60),
      intro_variants: '[]', outro_variants: '[]', sponsor: `Sponsor ${id}`,
      confirmation_count: 0, false_positive_count: 0, last_matched_at: null,
      created_at: '2026-01-01T00:00:00Z', created_from_episode_id: null,
      is_active: true, disabled_at: null, disabled_reason: null,
      ...(audio ? { audio } : {}),
    };
  }

  it('shows the four audio states in the table', async () => {
    mockGetPatterns.mockResolvedValue([
      audioPattern(1, { state: 'active', available: true, matchCount: 4, meanSimilarity: 0.912 }),
      audioPattern(2, { state: 'off', available: true, matchCount: 4, meanSimilarity: 0.9 }),
      audioPattern(3, { state: 'active', available: false, matchCount: 4, meanSimilarity: 0.9 }),
      audioPattern(4, { state: 'none', available: false, matchCount: 0, meanSimilarity: null }),
    ]);
    renderPage();
    const table = await screen.findByRole('table');
    await within(table).findByText('91% match');
    const rows = within(table).getAllByRole('row').slice(1);
    const cell = (row: number) => within(rows[row]).getAllByRole('cell')[4].textContent;
    expect(cell(0)).toBe('91% match');
    expect(cell(1)).toBe('Audio off');
    expect(cell(2)).toBe('No source audio');
    expect(cell(3)).toBe('-');
    expect(within(table).getByRole('columnheader', { name: 'Audio' })).toBeDefined();
  });

  afterEach(() => vi.unstubAllGlobals());

  it('renders every column, Created included, in the table', async () => {
    mockGetPatterns.mockResolvedValue([audioPattern(1)]);
    renderPage();
    const table = await screen.findByRole('table');
    await within(table).findAllByText('Sponsor 1');
    expect(within(table).getAllByRole('columnheader')).toHaveLength(11);
    expect(within(table).getByRole('columnheader', { name: 'Created' })).toBeDefined();
    expect(within(table).getAllByRole('row')[1].querySelectorAll('td')).toHaveLength(11);
    expect(table.querySelectorAll('col')).toHaveLength(11);
  });

  it('leaves the Sponsor column unsized so it takes the spare width', async () => {
    mockGetPatterns.mockResolvedValue([audioPattern(1)]);
    renderPage();
    const table = await screen.findByRole('table');
    await within(table).findAllByText('Sponsor 1');
    const cols = [...table.querySelectorAll('col')];
    expect(cols[2].className).toBe('');
    expect(cols.filter((c) => c.className === '')).toHaveLength(1);
  });

  it('spans the empty-state cell over every column', async () => {
    mockGetPatterns.mockResolvedValue([]);
    renderPage();
    const table = await screen.findByRole('table');
    expect((await within(table).findByText('No patterns yet.')).getAttribute('colspan')).toBe('11');
  });

  it('sorts the card layout by Created through the Sort by control', async () => {
    mockGetPatterns.mockResolvedValue([
      { ...audioPattern(1), created_at: '2026-01-01T00:00:00Z' },
      { ...audioPattern(2), created_at: '2026-03-01T00:00:00Z' },
    ]);
    renderPage('/patterns?sort=created_at&dir=asc');
    const table = await screen.findByRole('table');
    await within(table).findAllByText('Sponsor 1');
    expect(screen.getByLabelText('Sort by')).toHaveProperty('value', 'created_at');
    expect(screen.getByRole('button', { name: 'Ascending' })).toBeDefined();
    const ids = within(table).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[0].textContent);
    expect(ids).toEqual(['#1', '#2']);
  });

  it('shows a dash for active audio with no matches and for a pattern without the field', async () => {
    mockGetPatterns.mockResolvedValue([
      audioPattern(1, { state: 'active', available: true, matchCount: 0, meanSimilarity: null }),
      audioPattern(2),
    ]);
    renderPage();
    const table = await screen.findByRole('table');
    await within(table).findAllByText('Sponsor 1');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(within(rows[0]).getAllByRole('cell')[4].textContent).toBe('-');
    expect(within(rows[1]).getAllByRole('cell')[4].textContent).toBe('-');
  });

  it('shows the audio state on the phone card', async () => {
    mockGetPatterns.mockResolvedValue([
      audioPattern(1, { state: 'active', available: true, matchCount: 4, meanSimilarity: 0.912 }),
    ]);
    renderPage();
    expect(await screen.findByTestId('pattern-card-audio-1')).toHaveProperty('textContent', 'Audio: 91% match');
  });

  it('puts the category select and the created date on the phone card without opening the dialog', async () => {
    mockGetPatterns.mockResolvedValue([audioPattern(1, { state: 'none', available: false, matchCount: 0, meanSimilarity: null })]);
    renderPage();
    const card = (await screen.findByTestId('pattern-card-audio-1')).parentElement as HTMLElement;
    const select = within(card).getByLabelText('Category for pattern 1');
    expect(within(card).getByText(/^Created:/)).toBeDefined();
    fireEvent.click(select);
    expect(screen.queryByRole('dialog', { name: 'Pattern details' })).toBeNull();
  });
});

describe('PatternsPage detected ads tab', () => {
  it('switches to the detected ads tab and requests cut detections', async () => {
    renderPage();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Detected ads' }));
    await waitFor(() => expect(mockGetDetections).toHaveBeenCalled());
    expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({ status: 'accepted' });
  });

  it('opens the detected ads tab from the url', async () => {
    renderPage('/patterns?tab=detected-ads');
    const tab = await screen.findByRole('tab', { name: 'Detected ads' });
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

describe('PatternsPage list state in the URL', () => {
  const many = (count: number) => Array.from({ length: count }, (_, i) => {
    const id = i + 1;
    return {
      id, scope: 'global', network_id: null, podcast_id: null,
      dai_platform: null, text_template: 'x'.repeat(60),
      intro_variants: '[]', outro_variants: '[]', sponsor: `Sponsor ${String(id).padStart(2, '0')}`,
      confirmation_count: 0, false_positive_count: 0, last_matched_at: null,
      created_at: `2026-01-${String(id).padStart(2, '0')}T00:00:00Z`, created_from_episode_id: null,
      is_active: true, disabled_at: null, disabled_reason: null, category: null,
    };
  });

  it('shows the second page for ?page=2', async () => {
    mockGetPatterns.mockResolvedValue(many(25));
    renderPage('/patterns?page=2&sort=id&dir=asc');
    expect((await screen.findAllByText('Sponsor 25')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Sponsor 01')).toBeNull();
  });

  it('returns to the first page when a filter changes', async () => {
    mockGetPatterns.mockResolvedValue(many(25));
    renderPage('/patterns?page=2&sort=id&dir=asc');
    await screen.findAllByText('Sponsor 25');
    await userEvent.selectOptions(screen.getByLabelText('Origin'), 'auto');
    expect((await screen.findAllByText('Sponsor 01')).length).toBeGreaterThan(0);
  });
});

describe('PatternsPage search across tabs', () => {
  afterEach(() => vi.useRealTimers());

  it('does not carry the patterns search into another tab after a tab switch', async () => {
    renderPage('/patterns');
    const box = await screen.findByPlaceholderText(/search by sponsor/i);
    vi.useFakeTimers();
    fireEvent.change(box, { target: { value: 'foo' } });
    fireEvent.click(screen.getByRole('tab', { name: 'Ad review' }));
    mockGetDetections.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.getByTestId('loc').textContent).toBe('?tab=ad-review');
    for (const [params] of mockGetDetections.mock.calls) {
      expect(JSON.stringify(params)).not.toContain('foo');
    }
  });
});
