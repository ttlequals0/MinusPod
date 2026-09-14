// The Dashboard field is the mobile fix (#717): a real input the tap lands on
// directly, since iOS only raises the keyboard for focus inside the gesture.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, renderHook, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import Dashboard from './Dashboard';
import { useQuickSearchHotkey } from '../components/QuickSearch';
import type { Feed, EpisodeSummary } from '../api/types';

// A different title than the search fixture below, so grid and results
// panel text never collide in a getByText query.
const FEED: Feed = {
  slug: 'existing-feed',
  title: 'Existing Feed',
  sourceUrl: 'https://example.com/feed.xml',
  feedUrl: 'https://example.com/feed.xml',
  episodeCount: 1,
};

function episodeSummary(overrides: Partial<EpisodeSummary> & { id: string }): EpisodeSummary {
  return {
    title: `Episode ${overrides.id}`,
    published: '2026-09-01T00:00:00Z',
    createdAt: '2026-09-01T00:00:00Z',
    status: 'completed',
    ...overrides,
  };
}

// Two podcasts (title-sortable as Alpha < Zulu) plus a Recents pseudo-feed
// whose latestEpisodes is always empty (#721 known gap): the grouped view
// must exclude it rather than render a broken, empty card for it.
const ZULU_FEED: Feed = {
  slug: 'zulu-show', title: 'Zulu Show',
  sourceUrl: 'https://example.com/zulu.xml', feedUrl: 'https://example.com/zulu.xml',
  episodeCount: 5, artworkUrl: 'https://example.com/zulu.jpg',
  latestEpisodes: [
    episodeSummary({ id: 'z1', jobState: 'queued', status: 'processing' }),
    episodeSummary({ id: 'z2', jobState: 'idle' }),
    episodeSummary({ id: 'z3', jobState: 'idle' }),
    episodeSummary({ id: 'z4', jobState: 'idle' }),
    episodeSummary({ id: 'z5', jobState: 'idle' }),
  ],
};
const ALPHA_FEED: Feed = {
  slug: 'alpha-show', title: 'Alpha Show',
  sourceUrl: 'https://example.com/alpha.xml', feedUrl: 'https://example.com/alpha.xml',
  episodeCount: 0,
  latestEpisodes: [],
};
const RECENTS_FEED: Feed = {
  slug: 'recents', title: 'Recents', feedType: 'recents',
  sourceUrl: '', feedUrl: 'https://example.com/recents.xml',
  episodeCount: 10,
  latestEpisodes: [],
};

// Indirected through a mock so a test can leave the feeds query pending.
const mockFeedsQueryFn = vi.fn(async () => ({ feeds: [FEED], lastRefreshCompletedAt: null }));
const mockEpisodesQueryFn = vi.fn(async () => ({
  feeds: [ZULU_FEED, ALPHA_FEED, RECENTS_FEED], lastRefreshCompletedAt: null,
}));
const mockReprocessEpisode = vi.fn(async () => ({ message: 'ok', mode: 'reprocess' as const }));

vi.mock('../api/feeds', () => ({
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: () => mockFeedsQueryFn(),
  },
  feedsQueryOptionsFor: (params: unknown) => ({
    queryKey: ['feeds', params],
    queryFn: () => mockEpisodesQueryFn(),
  }),
  refreshFeed: vi.fn(),
  refreshAllFeeds: vi.fn(),
  deleteFeed: vi.fn(),
  reprocessEpisode: () => mockReprocessEpisode(),
}));

const mockSearch = vi.fn();
vi.mock('../api/search', () => ({
  search: (...a: unknown[]) => mockSearch(...a),
}));

const mockNavigate = vi.fn();
vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useNavigate: () => mockNavigate,
}));

function renderDashboard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Dashboard search field', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSearch.mockResolvedValue({
      query: 'ba',
      shows: [{ slug: 'example-podcast', title: 'The Daily Tech Show', snippet: null }],
      episodes: [], transcripts: [], patterns: [], sponsors: [],
    });
  });

  it('renders a real, focusable input above the feed grid', async () => {
    renderDashboard();
    const input = await screen.findByRole('combobox', { name: /search shows, episodes and transcripts/i });
    expect(input.tagName).toBe('INPUT');
    await userEvent.click(input);
    expect(document.activeElement).toBe(input);
  });

  it('queries the unified endpoint at 2+ characters and shows grouped results', async () => {
    renderDashboard();
    const input = await screen.findByRole('combobox');
    await userEvent.type(input, 'ba');
    await waitFor(() => expect(mockSearch).toHaveBeenCalled());
    await waitFor(() => screen.getByText('The Daily Tech Show'));
    expect(screen.getByText('Shows')).toBeTruthy();
  });

  it('does not query below the 2-character minimum', async () => {
    renderDashboard();
    const input = await screen.findByRole('combobox');
    await userEvent.type(input, 'b');
    await waitFor(() => screen.getByText(/type two or more characters/i));
    expect(mockSearch).not.toHaveBeenCalled();
  });

  it('Advanced search link carries the typed query', async () => {
    renderDashboard();
    const input = await screen.findByRole('combobox');
    await userEvent.type(input, 'ba');
    await waitFor(() => screen.getByText('The Daily Tech Show'));
    const link = screen.getByRole('link', { name: 'Advanced search' });
    expect(link.getAttribute('href')).toBe('/search?q=ba');
  });

  it('leaves the global palette trigger inert while the field has focus', async () => {
    const onOpen = vi.fn();
    renderDashboard();
    renderHook(() => useQuickSearchHotkey(onOpen));
    const input = await screen.findByRole('combobox');
    await userEvent.type(input, 'b');
    expect(onOpen).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe('b');
  });

  // Rows are not focusable, so a click blurs the input: without the panel's
  // mousedown guard the container onBlur unmounts the row before its click fires.
  it('clicking a result row navigates', async () => {
    renderDashboard();
    const input = await screen.findByRole('combobox');
    await userEvent.type(input, 'ba');
    await waitFor(() => screen.getByText('The Daily Tech Show'));
    await userEvent.click(screen.getByText('The Daily Tech Show'));
    expect(mockNavigate).toHaveBeenCalledWith('/feeds/example-podcast');
  });

  it('Enter navigates to the active row', async () => {
    renderDashboard();
    const input = await screen.findByRole('combobox');
    await userEvent.type(input, 'ba');
    await waitFor(() => screen.getByText('The Daily Tech Show'));
    await userEvent.keyboard('{Enter}');
    expect(mockNavigate).toHaveBeenCalledWith('/feeds/example-podcast');
  });

  // Escape never blurs the input, so onFocus won't refire on its own: typing
  // must reopen the panel, and a stale Enter must not act on hidden rows.
  it('Escape closes the panel; Enter is inert until typing reopens it', async () => {
    renderDashboard();
    const input = await screen.findByRole('combobox');
    await userEvent.type(input, 'ba');
    await waitFor(() => screen.getByText('The Daily Tech Show'));
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByText('The Daily Tech Show')).toBeNull();
    await userEvent.keyboard('{ArrowDown}{Enter}');
    expect(mockNavigate).not.toHaveBeenCalled();
    await userEvent.type(input, 't');
    await waitFor(() => screen.getByText('The Daily Tech Show'));
  });

  // A mousedown outside the root must close the panel even without a blur:
  // iOS Safari does not reliably blur a focused input for a tap that lands
  // on a non-focusable element, so onBlur alone would miss this.
  it('a mousedown outside the search root closes the panel', async () => {
    renderDashboard();
    const input = await screen.findByRole('combobox');
    await userEvent.type(input, 'ba');
    await waitFor(() => screen.getByText('The Daily Tech Show'));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByText('The Daily Tech Show')).toBeNull();
  });
});

describe('Dashboard delete confirmation', () => {
  it('warns that deleting stops the job when an episode is processing', async () => {
    mockFeedsQueryFn.mockResolvedValueOnce({
      feeds: [{
        ...FEED,
        statusCounts: { discovered: 0, pending: 0, processing: 1, completed: 0, failed: 0, permanently_failed: 0, deferred: 0 },
      }],
      lastRefreshCompletedAt: null,
    });
    renderDashboard();
    await screen.findByText('Existing Feed');

    await userEvent.click(screen.getByRole('button', { name: 'Delete feed' }));

    expect(screen.getByText('An episode is processing right now. Deleting this podcast will stop it.')).toBeDefined();
  });

  it('does not warn about stopping a job when nothing is processing', async () => {
    renderDashboard();
    await screen.findByText('Existing Feed');

    await userEvent.click(screen.getByRole('button', { name: 'Delete feed' }));

    expect(screen.getByText('Click delete again to confirm')).toBeDefined();
    expect(screen.queryByText(/Deleting this podcast will stop/)).toBeNull();
  });
});

describe('Dashboard loading state', () => {
  afterEach(() => localStorage.removeItem('dashboardViewMode'));

  it('shows a card-grid skeleton in grid view, not a page spinner', () => {
    localStorage.setItem('dashboardViewMode', JSON.stringify('grid'));
    mockFeedsQueryFn.mockReturnValueOnce(new Promise<never>(() => {}));
    renderDashboard();
    expect(screen.getByTestId('skeleton-stat-cards')).toBeDefined();
    expect(screen.queryByTestId('skeleton-page-header')).toBeNull();
  });

  it('shows a list skeleton when the persisted view mode is list', () => {
    localStorage.setItem('dashboardViewMode', JSON.stringify('list'));
    mockFeedsQueryFn.mockReturnValueOnce(new Promise<never>(() => {}));
    renderDashboard();
    expect(screen.getByTestId('skeleton-rows')).toBeDefined();
    expect(screen.queryByTestId('skeleton-page-header')).toBeNull();
  });
});

describe('Dashboard Episodes view', () => {
  afterEach(() => {
    localStorage.removeItem('dashboardView');
    localStorage.removeItem('dashboardSortBy');
    localStorage.removeItem('dashboardEpisodesPerPodcast');
  });

  it('groups by podcast at the default cap and excludes the Recents pseudo-feed', async () => {
    renderDashboard();
    await userEvent.click(await screen.findByRole('button', { name: 'Episodes view' }));

    await screen.findByRole('heading', { name: 'Zulu Show' });
    screen.getByRole('heading', { name: 'Alpha Show' });
    expect(screen.queryByRole('heading', { name: 'Recents' })).toBeNull();

    // Default N=3: only the first 3 of Zulu's 5 latestEpisodes render.
    expect(screen.getByText('Episode z1')).toBeTruthy();
    expect(screen.getByText('Episode z2')).toBeTruthy();
    expect(screen.getByText('Episode z3')).toBeTruthy();
    expect(screen.queryByText('Episode z4')).toBeNull();
    expect(screen.queryByText('Episode z5')).toBeNull();
  });

  it('group header links to the podcast', async () => {
    renderDashboard();
    await userEvent.click(await screen.findByRole('button', { name: 'Episodes view' }));
    const heading = await screen.findByRole('heading', { name: 'Zulu Show' });
    const link = heading.querySelector('a');
    expect(link?.getAttribute('href')).toBe('/feeds/zulu-show');
  });

  it('disables a queued row action while a sibling row stays actionable', async () => {
    renderDashboard();
    await userEvent.click(await screen.findByRole('button', { name: 'Episodes view' }));
    await screen.findByText('Episode z1');
    const queuedButton = screen.getByText('Queued').closest('button') as HTMLButtonElement;
    expect(queuedButton.disabled).toBe(true);
    const idleButton = screen.getAllByText('Reprocess')[0].closest('button') as HTMLButtonElement;
    expect(idleButton.disabled).toBe(false);
  });

  it('renders a feed with no episodes cleanly instead of a broken card', async () => {
    renderDashboard();
    await userEvent.click(await screen.findByRole('button', { name: 'Episodes view' }));
    await screen.findByRole('heading', { name: 'Alpha Show' });
    expect(screen.getByText('No episodes yet')).toBeTruthy();
  });

  it('preserves sort when switching between Podcasts and Episodes views', async () => {
    renderDashboard();
    await userEvent.click(await screen.findByRole('button', { name: 'Sort by title' }));
    await userEvent.click(screen.getByRole('button', { name: 'Episodes view' }));

    const headings = await screen.findAllByRole('heading', { level: 2 });
    expect(headings.map((h) => h.textContent)).toEqual(['Alpha Show', 'Zulu Show']);

    await userEvent.click(screen.getByRole('button', { name: 'Podcasts view' }));
    expect(JSON.parse(localStorage.getItem('dashboardSortBy') ?? '""')).toBe('title');
  });
});

describe('Dashboard toolbar heights', () => {
  afterEach(() => {
    localStorage.removeItem('dashboardView');
    localStorage.removeItem('dashboardEpisodesPerPodcast');
  });

  it('gives the Podcasts/Episodes switch a 44px outer height', async () => {
    renderDashboard();
    const group = await screen.findByRole('group', { name: 'Dashboard view' });
    expect(group.className).toContain('h-11');
    const podcastsButton = screen.getByRole('button', { name: 'Podcasts view' });
    expect(podcastsButton.className).toContain('inline-flex');
    expect(podcastsButton.className).toContain('items-center');
    expect(podcastsButton.className).toContain('justify-center');
  });

  it('gives the grid/list icon buttons a 44px wrapper height and matching min-width', async () => {
    renderDashboard();
    const gridButton = await screen.findByRole('button', { name: 'Grid view' });
    const wrapper = gridButton.closest('div');
    expect(wrapper?.className).toContain('h-11');
    expect(gridButton.className).toContain('min-w-11');
    expect(gridButton.className).toContain('inline-flex');
    expect(gridButton.className).toContain('items-center');
    expect(gridButton.className).toContain('justify-center');
  });

  it('gives the sort icon buttons a 44px wrapper height and matching min-width', async () => {
    renderDashboard();
    const sortButton = await screen.findByRole('button', { name: 'Sort by recent' });
    const wrapper = sortButton.closest('div');
    expect(wrapper?.className).toContain('h-11');
    expect(sortButton.className).toContain('min-w-11');
  });

  it('gives Refresh All and Add Feed a 44px height and matching min-width', async () => {
    renderDashboard();
    const refreshAll = await screen.findByRole('button', { name: 'Refresh all feeds' });
    expect(refreshAll.className).toContain('h-11');
    expect(refreshAll.className).toContain('min-w-11');

    const addFeed = screen.getByRole('link', { name: 'Add Feed' });
    expect(addFeed.className).toContain('h-11');
    expect(addFeed.className).toContain('min-w-11');
  });

  it('gives the episodes-per-podcast select a matching 44px height', async () => {
    renderDashboard();
    await userEvent.click(await screen.findByRole('button', { name: 'Episodes view' }));
    const select = await screen.findByRole('combobox', { name: 'Episodes per podcast' });
    expect(select.className).toContain('h-11');
  });
});
