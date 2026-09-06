import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import Search from './Search';

const mockSearch = vi.fn();
vi.mock('../api/search', () => ({
  search: (...a: unknown[]) => mockSearch(...a),
  rebuildSearchIndex: vi.fn(),
  getSearchStats: vi.fn().mockResolvedValue({ stats: { total: 0 } }),
}));

function renderSearch(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Search />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Search page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSearch.mockResolvedValue({
      query: 'xy', shows: [{ slug: 'example-podcast', title: 'The Daily Tech Show', snippet: null }],
      episodes: [], transcripts: [], patterns: [], sponsors: [],
    });
  });

  it('falls back to the All tab for an unknown ?type= without throwing', async () => {
    renderSearch('/search?type=bogus&q=xy');
    await waitFor(() => screen.getByText('The Daily Tech Show'));
    const allTab = screen.getByRole('button', { name: 'All' });
    expect(allTab.className).toContain('bg-primary');
  });
});
