/**
 * Deleting a feed from its own detail page (#618).
 *
 * Uses the dashboard's delete button and confirm flow: click once to arm,
 * again within 3s to delete, with a toast in between. Success navigates back
 * to the dashboard, since the page being viewed no longer exists.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FeedDetail from './FeedDetail';
import type { Feed } from '../api/types';

const mockNavigate = vi.fn();

vi.mock('react-router', () => ({
  useParams: () => ({ slug: 'test-feed' }),
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: '/feeds/test-feed', state: null }),
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
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

function makeFeed(): Feed {
  return {
    slug: 'test-feed',
    title: 'Test Feed',
    sourceUrl: 'https://example.com/feed.xml',
    feedUrl: 'https://minuspod.example.com/feeds/test-feed.xml',
    episodeCount: 3,
  };
}

async function renderFeedDetail() {
  const feed = makeFeed();
  mockGetFeed.mockResolvedValue(feed);
  mockGetFeedsResponse.mockResolvedValue({ feeds: [feed], lastRefreshCompletedAt: null });
  mockGetEpisodes.mockResolvedValue({ episodes: [], total: 0 });
  render(
    <QueryClientProvider client={new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })}>
      <FeedDetail />
    </QueryClientProvider>,
  );
  await screen.findByText('Test Feed');
}

const deleteButton = () => screen.getByRole('button', { name: 'Delete feed' });

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
    expect(screen.getByText('Click delete again to confirm')).toBeDefined();
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

  it('disarms after 3s so a stale click cannot delete the feed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await renderFeedDetail();

    await user.click(deleteButton());
    expect(screen.getByText('Click delete again to confirm')).toBeDefined();

    await vi.advanceTimersByTimeAsync(3100);
    await waitFor(() => {
      expect(screen.queryByText('Click delete again to confirm')).toBeNull();
    });

    // The next click re-arms instead of deleting.
    await user.click(deleteButton());
    expect(mockDeleteFeed).not.toHaveBeenCalled();
  });

  it('says why when the delete fails, and stays on the page', async () => {
    mockDeleteFeed.mockRejectedValue(new Error('feed is locked'));
    await renderFeedDetail();

    await userEvent.click(deleteButton());
    await userEvent.click(deleteButton());

    await waitFor(() => expect(screen.getByText('feed is locked')).toBeDefined());
    expect(mockNavigate).not.toHaveBeenCalled();
    // Armed state cleared, so the next click starts the confirm over.
    expect(screen.queryByText('Click delete again to confirm')).toBeNull();
  });

  it('dismisses the error toast', async () => {
    mockDeleteFeed.mockRejectedValue(new Error('feed is locked'));
    await renderFeedDetail();

    await userEvent.click(deleteButton());
    await userEvent.click(deleteButton());
    await waitFor(() => expect(screen.getByText('feed is locked')).toBeDefined());

    await userEvent.click(screen.getByRole('button', { name: 'Dismiss error' }));

    expect(screen.queryByText('feed is locked')).toBeNull();
  });
});
