import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import FeedDetail from './FeedDetail';
import type { Feed } from '../api/types';

const mockNavigate = vi.fn();

vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useParams: () => ({ slug: 'test-feed' }),
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: '/feeds/test-feed', state: null }),
}));

vi.mock('./feeds/FeedSettingsPanel', () => ({ default: () => <div /> }));
vi.mock('./feeds/FeedStatsCards', () => ({ default: () => <div /> }));
vi.mock('./feeds/PodcastAdDistributionPanel', () => ({ default: () => <div /> }));
vi.mock('./feeds/CueTemplatesPanel', () => ({ default: () => <div /> }));
vi.mock('../components/Artwork', () => ({ default: ({ alt }: { alt: string }) => <img alt={alt} /> }));
vi.mock('../components/LoadingSpinner', () => ({ default: () => <div /> }));

const mockGetFeed = vi.fn();
const mockGetFeedsResponse = vi.fn();
const mockGetEpisodes = vi.fn();
const mockDeleteFeed = vi.fn();

vi.mock('../api/feeds', () => ({
  getFeed: (...a: unknown[]) => mockGetFeed(...a),
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: (...a: unknown[]) => mockGetFeedsResponse(...a),
  },
  getEpisodes: (...a: unknown[]) => mockGetEpisodes(...a),
  deleteFeed: (...a: unknown[]) => mockDeleteFeed(...a),
  refreshFeed: vi.fn(),
  updateFeed: vi.fn(),
  reprocessAllEpisodes: vi.fn(),
  bulkEpisodeAction: vi.fn(),
}));

function makeFeed(overrides: Partial<Feed> = {}): Feed {
  return {
    slug: 'test-feed',
    title: 'Test Feed',
    sourceUrl: 'https://example.com/feed.xml',
    feedUrl: 'https://minuspod.example.com/feeds/test-feed.xml',
    episodeCount: 3,
    ...overrides,
  };
}

async function renderFeedDetail(feedOverrides: Partial<Feed> = {}) {
  const feed = makeFeed(feedOverrides);
  mockGetFeed.mockResolvedValue(feed);
  mockGetFeedsResponse.mockResolvedValue({ feeds: [feed], lastRefreshCompletedAt: null });
  mockGetEpisodes.mockResolvedValue({ episodes: [], total: 0 });
  render(
    <QueryClientProvider client={new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })}>
      <MemoryRouter><FeedDetail /></MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText('Test Feed');
}

const deleteButton = () => screen.queryByRole('dialog')
  ? within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete feed' })
  : screen.getByRole('button', { name: 'Delete feed' });

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('FeedDetail: deleting the feed', () => {
  it('offers the action without a trip back to the dashboard', async () => {
    await renderFeedDetail();
    expect(deleteButton()).toBeDefined();
  });

  it('arms rather than deleting on the first click', async () => {
    await renderFeedDetail();

    await userEvent.click(deleteButton());

    expect(mockDeleteFeed).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'Delete Test Feed?' })).toBeDefined();
  });

  it('warns that deleting stops the job when an episode is processing', async () => {
    await renderFeedDetail({ statusCounts: { discovered: 0, pending: 0, processing: 1, completed: 0, failed: 0, permanently_failed: 0, deferred: 0 } });

    await userEvent.click(deleteButton());

    expect(screen.getByText('An episode is processing right now. Deleting this podcast will stop it.')).toBeDefined();
  });

  it('warns with a plural count when several episodes are processing', async () => {
    await renderFeedDetail({ statusCounts: { discovered: 0, pending: 0, processing: 3, completed: 0, failed: 0, permanently_failed: 0, deferred: 0 } });

    await userEvent.click(deleteButton());

    expect(screen.getByText('3 episodes are processing right now. Deleting this podcast will stop them.')).toBeDefined();
  });

  it('does not warn about stopping a job when nothing is processing', async () => {
    await renderFeedDetail({ statusCounts: { discovered: 0, pending: 2, processing: 0, completed: 1, failed: 0, permanently_failed: 0, deferred: 0 } });

    await userEvent.click(deleteButton());

    expect(screen.getByRole('dialog', { name: 'Delete Test Feed?' })).toBeDefined();
    expect(screen.queryByText(/Deleting this podcast will stop/)).toBeNull();
  });

  it('deletes on the second click', async () => {
    mockDeleteFeed.mockResolvedValue(undefined);
    await renderFeedDetail();

    await userEvent.click(deleteButton());
    await userEvent.click(deleteButton());

    await waitFor(() => expect(mockDeleteFeed).toHaveBeenCalledWith('test-feed'));
  });

  it('leaves the page it just destroyed', async () => {
    mockDeleteFeed.mockResolvedValue(undefined);
    await renderFeedDetail();

    await userEvent.click(deleteButton());
    await userEvent.click(deleteButton());

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/'));
  });

  it('stays on the feed while a running job drains before deletion', async () => {
    mockDeleteFeed.mockResolvedValue({
      message: 'Deletion will finish after active processing stops.',
      slug: 'test-feed',
      pending: true,
    });
    await renderFeedDetail();

    await userEvent.click(deleteButton());
    await userEvent.click(deleteButton());

    await waitFor(() => {
      expect(screen.getByText('Deletion will finish after active processing stops.')).toBeDefined();
    });
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('keeps confirmation open until cancelled', async () => {
    await renderFeedDetail();
    await userEvent.click(deleteButton());
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(mockDeleteFeed).not.toHaveBeenCalled();
  });

  it('says why when the delete fails, and stays on the page', async () => {
    mockDeleteFeed.mockRejectedValue(new Error('feed is locked'));
    await renderFeedDetail();

    await userEvent.click(deleteButton());
    await userEvent.click(deleteButton());

    await waitFor(() => expect(screen.getByText('feed is locked')).toBeDefined());
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeDefined();
  });

  it('closes the failed confirmation', async () => {
    mockDeleteFeed.mockRejectedValue(new Error('feed is locked'));
    await renderFeedDetail();

    await userEvent.click(deleteButton());
    await userEvent.click(deleteButton());
    await waitFor(() => expect(screen.getByText('feed is locked')).toBeDefined());

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByText('feed is locked')).toBeNull();
  });
});
