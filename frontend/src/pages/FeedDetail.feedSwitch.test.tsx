// Switching between cached feeds keeps FeedDetail mounted; the settings panel must still reset.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router';
import FeedDetail from './FeedDetail';
import type { Feed } from '../api/types';

const mockGetEpisodes = vi.fn();

vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useNavigate: () => vi.fn(),
}));

vi.mock('./feeds/FeedStatsCards', () => ({ default: () => null }));
vi.mock('./feeds/PodcastAdDistributionPanel', () => ({ default: () => null }));
vi.mock('./feeds/CueTemplatesPanel', () => ({ default: () => null }));
vi.mock('./patterns/PendingRecutsBar', () => ({ PendingRecutsBar: () => null }));
vi.mock('../components/Artwork', () => ({ default: ({ alt }: { alt: string }) => <img alt={alt} /> }));
vi.mock('../components/FeedTagsEditor', () => ({ FeedTagsEditor: () => null }));

const FEEDS: Record<string, Feed> = {
  'feed-a': { slug: 'feed-a', title: 'Feed A', sourceUrl: 'https://example.com/a.xml', feedUrl: 'https://example.com/a', episodeCount: 0 },
  'feed-b': { slug: 'feed-b', title: 'Feed B', sourceUrl: 'https://example.com/b.xml', feedUrl: 'https://example.com/b', episodeCount: 0 },
};

vi.mock('../api/feeds', () => ({
  getFeed: (slug: string) => Promise.resolve(FEEDS[slug]),
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: () => Promise.resolve({ feeds: Object.values(FEEDS), lastRefreshCompletedAt: null }),
  },
  getEpisodes: (...args: unknown[]) => mockGetEpisodes(...args),
  getNetworks: () => Promise.resolve([]),
  refreshFeed: vi.fn(),
  updateFeed: vi.fn(),
  reprocessAllEpisodes: vi.fn(),
  bulkEpisodeAction: vi.fn(),
  setEpisodesPassthrough: vi.fn(),
  rerenderSegments: vi.fn(),
  deleteFeed: vi.fn(),
  CUE_SCORE_MIN: 0.30,
  CUE_SCORE_MAX: 0.99,
}));

vi.mock('../api/cueTemplates', () => ({ listCueTemplates: () => Promise.resolve([]) }));

vi.mock('../api/settings', () => ({
  getSettings: () => Promise.resolve({}),
  getAudioSettings: () => Promise.resolve({ keepOriginalAudio: true }),
}));

function card(title: string) {
  return screen.getByRole('heading', { name: title }).closest('[data-search-key]') as HTMLElement;
}

function toggle(title: string) {
  return screen.getByRole('heading', { name: title }).closest('button') as HTMLElement;
}

function renderFeed(client: QueryClient) {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/feeds/feed-a']}>
        <Routes><Route path="/feeds/:slug" element={<FeedDetail />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const feedLink = (title: string) => screen.findByRole('link', { name: new RegExp(`feed: ${title}`) });

describe('FeedDetail: switching feeds', () => {
  beforeEach(() => {
    localStorage.clear();
    mockGetEpisodes.mockResolvedValue({ episodes: [], total: 0 });
  });

  it('resets the settings search and keeps the next feed stored collapse state', async () => {
    for (const s of Object.keys(FEEDS)) localStorage.setItem(`feed-settings-${s}`, 'true');
    localStorage.setItem('feed-chapters-feed-b', 'false');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    for (const s of Object.keys(FEEDS)) client.setQueryData(['feed', s], FEEDS[s]);
    const user = userEvent.setup();
    renderFeed(client);

    expect(toggle('Chapters').getAttribute('aria-expanded')).toBe('true');
    await user.type(screen.getByRole('textbox', { name: 'Search feed settings' }), 'queue priority');
    expect(card('Chapters').className).toContain('hidden');

    await user.click(await feedLink('Feed B'));

    expect(card('Chapters').getAttribute('data-search-key')).toBe('feed-chapters-feed-b');
    expect((screen.getByRole('textbox', { name: 'Search feed settings' }) as HTMLInputElement).value).toBe('');
    for (const el of document.querySelectorAll('[data-search-key^="feed-"]')) {
      expect(el.className).not.toContain('hidden');
    }
    expect(toggle('Chapters').getAttribute('aria-expanded')).toBe('false');
    expect(localStorage.getItem('feed-chapters-feed-b')).toBe('false');
  });

  it('clears the episode title search when switching feeds', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    for (const s of Object.keys(FEEDS)) client.setQueryData(['feed', s], FEEDS[s]);
    const user = userEvent.setup();
    renderFeed(client);
    const box = screen.getByLabelText('Search episode titles') as HTMLInputElement;
    await user.type(box, 'rain');
    await waitFor(() => expect(mockGetEpisodes).toHaveBeenLastCalledWith('feed-a', expect.objectContaining({ search: 'rain' })));

    await user.click(await feedLink('Feed B'));

    expect((screen.getByLabelText('Search episode titles') as HTMLInputElement).value).toBe('');
    await waitFor(() => expect(mockGetEpisodes).toHaveBeenLastCalledWith('feed-b', expect.objectContaining({ search: '' })));
    await new Promise((r) => setTimeout(r, 400));
    expect(mockGetEpisodes).not.toHaveBeenCalledWith('feed-b', expect.objectContaining({ search: 'rain' }));
  });
});

it('clears selections on feed changes without restoring them when returning', async () => {
  mockGetEpisodes.mockResolvedValue({ episodes: [{
    id: 'shared-id', title: 'Episode', published: '2026-10-10T00:00:00Z',
    status: 'completed', jobState: 'idle',
  }], total: 1 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  for (const slug of Object.keys(FEEDS)) client.setQueryData(['feed', slug], FEEDS[slug]);
  const user = userEvent.setup();
  renderFeed(client);
  await user.click(await screen.findByRole('button', { name: 'Select episode' }));
  expect(screen.getByText('1 selected')).toBeTruthy();
  await user.click(await feedLink('Feed B'));
  await screen.findByRole('button', { name: 'Select episode' });
  expect(screen.queryByText('1 selected')).toBeNull();
  await user.click(await feedLink('Feed A'));
  await screen.findByRole('button', { name: 'Select episode' });
  expect(screen.queryByText('1 selected')).toBeNull();
});
