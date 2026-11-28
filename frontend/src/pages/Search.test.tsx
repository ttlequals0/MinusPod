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
    expect(screen.getByRole('tab', { name: 'All' }).getAttribute('aria-selected')).toBe('true');
  });
});

describe('Search page loading placeholder', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows result skeletons while the search query is pending', async () => {
    mockSearch.mockReturnValueOnce(new Promise(() => {}));
    renderSearch('/search?q=xy');
    expect(await screen.findByTestId('skeleton-rows')).toBeTruthy();
  });

  it('drops the skeletons once results land', async () => {
    mockSearch.mockResolvedValue({
      query: 'xy', shows: [{ slug: 'example-podcast', title: 'The Daily Tech Show', snippet: null }],
      episodes: [], transcripts: [], patterns: [], sponsors: [],
    });
    renderSearch('/search?q=xy');
    await waitFor(() => screen.getByText('The Daily Tech Show'));
    expect(screen.queryByTestId('skeleton-rows')).toBeNull();
  });
});

describe('Search page episode results', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSearch.mockResolvedValue({
      query: 'quartz', shows: [], transcripts: [], patterns: [], sponsors: [],
      episodes: [
        { feedSlug: 'example-podcast', feedTitle: 'The Daily Tech Show', episodeId: 'a1b2c3', title: 'Quartz Canyon',
          status: 'discovered', publishDate: '2024-12-16T08:00:00Z', titleSnippet: '<mark>Quartz</mark> Canyon', snippet: null },
        { feedSlug: 'example-podcast', feedTitle: 'The Daily Tech Show', episodeId: 'd4e5f6', title: 'Rerun',
          status: 'completed', publishDate: '2026-06-19T07:00:00Z', titleSnippet: null, snippet: 'about <mark>quartz</mark> mines' },
      ],
    });
  });

  it('shows each episode with its status and publish date', async () => {
    renderSearch('/search?q=quartz');
    await waitFor(() => screen.getByText('Rerun'));
    expect(screen.getByText('Discovered')).toBeTruthy();
    expect(screen.getByText('Completed')).toBeTruthy();
    const expected = new Date('2024-12-16T08:00:00Z').toLocaleDateString();
    expect(screen.getByText(expected)).toBeTruthy();
  });

  it('highlights a title hit in the title and keeps description hits as the snippet', async () => {
    renderSearch('/search?q=quartz');
    await waitFor(() => screen.getByText('Rerun'));
    const heading = screen.getByRole('heading', { level: 3, name: 'Quartz Canyon' });
    expect(heading.querySelector('mark')?.textContent).toBe('Quartz');
    expect(screen.getByText((_, el) => el?.tagName === 'P' && el.textContent === 'about quartz mines')).toBeTruthy();
  });
});

describe('Search page pattern results', () => {
  it('links a pattern hit to that pattern with ?id=', async () => {
    mockSearch.mockResolvedValue({
      query: 'acme', shows: [], episodes: [], transcripts: [], sponsors: [],
      patterns: [{ id: 42, sponsor: 'Acme', scope: 'global', snippet: null }],
    });
    renderSearch('/search?q=acme');
    const link = (await screen.findByText('Acme')).closest('a');
    expect(link?.getAttribute('href')).toBe('/patterns?id=42');
  });
});

describe('Search page transcript results', () => {
  it('opens the Transcript tab at the matched second, and without ?t= when there is no timestamp', async () => {
    mockSearch.mockResolvedValue({
      query: 'acme', shows: [], episodes: [], patterns: [], sponsors: [],
      transcripts: [
        { feedSlug: 'example-podcast', episodeId: 'a1b2c3', title: 'Quartz Canyon', snippet: 'about <mark>acme</mark>', timestamp: 754.5 },
        { feedSlug: 'example-podcast', episodeId: 'd4e5f6', title: 'Rerun', snippet: 'acme again', timestamp: null },
      ],
    });
    renderSearch('/search?q=acme');
    const first = (await screen.findByText('Quartz Canyon')).closest('a');
    expect(first?.getAttribute('href')).toBe('/feeds/example-podcast/episodes/a1b2c3?section=transcript&t=754.5');
    expect(screen.getByText('Rerun').closest('a')?.getAttribute('href')).toBe('/feeds/example-podcast/episodes/d4e5f6?section=transcript');
  });
});

describe('Search page sponsor results', () => {
  it('links a sponsor hit to the Patterns Sponsors tab with the name as ?q=', async () => {
    mockSearch.mockResolvedValue({
      query: 'acme', shows: [], episodes: [], transcripts: [], patterns: [],
      sponsors: [{ id: 7, name: 'Acme & Co', snippet: null }],
    });
    renderSearch('/search?q=acme');
    const link = (await screen.findByText('Acme & Co')).closest('a');
    expect(link?.getAttribute('href')).toBe('/patterns?tab=sponsors&q=Acme%20%26%20Co');
  });
});
