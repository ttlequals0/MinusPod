/**
 * Deleting a feed from its own detail page (#618).
 *
 * The dashboard confirms with a second click, which does not carry over
 * here: this delete destroys the page the reader is standing on and
 * navigates away, so it confirms through the modal the page already uses
 * for its other destructive action.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FeedDetail from './FeedDetail';
import type { Feed } from '../api/types';

const mockNavigate = vi.fn();

vi.mock('react-router', () => ({
  useParams: () => ({ slug: 'test-feed' }),
  useNavigate: () => mockNavigate,
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

/** The header trigger; the modal's confirm shares its name, and renders after. */
function trigger() {
  return screen.getAllByRole('button', { name: 'Delete feed' })[0];
}

/** The modal's confirm button, which only exists once the modal is open. */
function confirmButton() {
  const buttons = screen.getAllByRole('button', { name: 'Delete feed' });
  expect(buttons.length).toBe(2);
  return buttons[1];
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('FeedDetail: deleting the feed', () => {
  it('offers the action without a trip back to the dashboard', async () => {
    await renderFeedDetail();
    expect(trigger()).toBeDefined();
  });

  it('does not delete on the first click', async () => {
    await renderFeedDetail();

    await userEvent.click(trigger());

    expect(mockDeleteFeed).not.toHaveBeenCalled();
  });

  it('says what is lost before asking to confirm', async () => {
    await renderFeedDetail();

    await userEvent.click(trigger());

    expect(screen.getByText(/removes its episodes, processed audio/i)).toBeDefined();
    expect(screen.getByText(/subscription URL stops working/i)).toBeDefined();
    expect(screen.getByText(/cannot undo this/i)).toBeDefined();
  });

  it('backs out without deleting', async () => {
    await renderFeedDetail();

    await userEvent.click(trigger());
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(mockDeleteFeed).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.queryByText(/cannot undo this/i)).toBeNull();
    });
  });

  it('deletes the feed once confirmed', async () => {
    mockDeleteFeed.mockResolvedValue(undefined);
    await renderFeedDetail();

    await userEvent.click(trigger());
    await userEvent.click(confirmButton());

    await waitFor(() => expect(mockDeleteFeed).toHaveBeenCalledWith('test-feed'));
  });

  it('leaves the page it just destroyed', async () => {
    mockDeleteFeed.mockResolvedValue(undefined);
    await renderFeedDetail();

    await userEvent.click(trigger());
    await userEvent.click(confirmButton());

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/'));
  });

  it('keeps the reader on the page when the delete fails', async () => {
    mockDeleteFeed.mockRejectedValue(new Error('nope'));
    await renderFeedDetail();

    await userEvent.click(trigger());
    await userEvent.click(confirmButton());

    await waitFor(() => expect(mockDeleteFeed).toHaveBeenCalled());
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('says why when the delete fails, rather than appearing to do nothing', async () => {
    mockDeleteFeed.mockRejectedValue(new Error('feed is locked'));
    await renderFeedDetail();

    await userEvent.click(trigger());
    await userEvent.click(confirmButton());

    await waitFor(() => {
      expect(screen.getByText(/was not deleted: feed is locked/i)).toBeDefined();
    });
  });
});
