// The Dashboard field is the mobile fix (#717): a real input the tap lands on
// directly, since iOS only raises the keyboard for focus inside the gesture.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import Dashboard from './Dashboard';
import { useQuickSearchHotkey } from '../components/QuickSearch';
import type { Feed } from '../api/types';

// A different title than the search fixture below, so grid and results
// panel text never collide in a getByText query.
const FEED: Feed = {
  slug: 'existing-feed',
  title: 'Existing Feed',
  sourceUrl: 'https://example.com/feed.xml',
  feedUrl: 'https://example.com/feed.xml',
  episodeCount: 1,
};

vi.mock('../api/feeds', () => ({
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: async () => ({ feeds: [FEED], lastRefreshCompletedAt: null }),
  },
  refreshFeed: vi.fn(),
  refreshAllFeeds: vi.fn(),
  deleteFeed: vi.fn(),
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
});
