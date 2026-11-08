import { afterEach, describe, expect, test, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import Dashboard from './Dashboard';
import { DEMO_WRITE_TOOLTIP } from '../demo/copy';
import type { Feed } from '../api/types';

vi.mock('../demo/flag', () => ({ IS_DEMO: true }));

const FEED: Feed = {
  slug: 'existing-feed',
  title: 'Existing Feed',
  sourceUrl: 'https://example.com/feed.xml',
  feedUrl: 'https://example.com/feed.xml',
  episodeCount: 1,
};

vi.mock('../api/feeds', () => ({
  feedsQueryOptionsFor: (params: Record<string, unknown>) => ({
    queryKey: ['feeds', params],
    queryFn: async () => ({ feeds: [FEED], lastRefreshCompletedAt: null, total: 1, totalPages: 1, page: 1, limit: 24 }),
  }),
  feedSortDirection: () => 'desc',
  refreshFeed: vi.fn(),
  refreshAllFeeds: vi.fn(),
  deleteFeed: vi.fn(),
  reprocessEpisode: vi.fn(),
}));
vi.mock('../api/settings', () => ({ exportOpml: vi.fn() }));
vi.mock('../api/search', () => ({ search: vi.fn() }));

afterEach(cleanup);

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

describe('Dashboard in demo', () => {
  test('disables refresh all and the feed delete and refresh controls', async () => {
    renderDashboard();
    const del = await screen.findByRole('button', { name: 'Delete feed' }) as HTMLButtonElement;
    expect(del.disabled).toBe(true);
    expect(del.parentElement?.getAttribute('title')).toBe(DEMO_WRITE_TOOLTIP);

    const refreshAll = screen.getByRole('button', { name: 'Refresh all feeds' }) as HTMLButtonElement;
    expect(refreshAll.disabled).toBe(true);
    expect(refreshAll.parentElement?.parentElement?.getAttribute('title')).toBe(DEMO_WRITE_TOOLTIP);
  });
});
