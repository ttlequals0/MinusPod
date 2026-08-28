/**
 * Render test for the podping coverage metadata line on FeedDetail. Heavy
 * feature panels (settings, stats, distribution, cue templates) are stubbed
 * since they are not under test here; only the feed header metadata is
 * exercised.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FeedDetail from './FeedDetail';
import type { Feed } from '../api/types';

vi.mock('react-router', () => ({
  useParams: () => ({ slug: 'test-feed' }),
  useNavigate: () => vi.fn(),
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

vi.mock('./feeds/FeedSettingsPanel', () => ({ default: () => <div data-testid="feed-settings-panel" /> }));
vi.mock('./feeds/FeedStatsCards', () => ({ default: () => <div data-testid="feed-stats-cards" /> }));
vi.mock('./feeds/PodcastAdDistributionPanel', () => ({ default: () => <div data-testid="ad-distribution-panel" /> }));
vi.mock('./feeds/CueTemplatesPanel', () => ({ default: () => <div data-testid="cue-templates-panel" /> }));
vi.mock('../components/Artwork', () => ({ default: ({ alt }: { alt: string }) => <img alt={alt} /> }));
vi.mock('../components/LoadingSpinner', () => ({ default: () => <div data-testid="spinner" /> }));

const mockGetFeed = vi.fn();
const mockGetFeedsResponse = vi.fn();
const mockGetEpisodes = vi.fn();

vi.mock('../api/feeds', () => ({
  getFeed: (...a: unknown[]) => mockGetFeed(...a),
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: (...a: unknown[]) => mockGetFeedsResponse(...a),
  },
  getEpisodes: (...a: unknown[]) => mockGetEpisodes(...a),
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
    episodeCount: 0,
    ...overrides,
  };
}

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderFeedDetail(feed: Feed) {
  mockGetFeed.mockResolvedValue(feed);
  mockGetFeedsResponse.mockResolvedValue({ feeds: [feed], lastRefreshCompletedAt: null });
  mockGetEpisodes.mockResolvedValue({ episodes: [], total: 0 });
  return render(
    <QueryClientProvider client={makeClient()}>
      <FeedDetail />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('FeedDetail: podping metadata line', () => {
  it('shows the last ping time when one arrived', async () => {
    renderFeedDetail(makeFeed({
      podpingCoverage: 'received',
      lastPodpingAt: '2026-07-20T12:00:00Z',
    }));
    await waitFor(() => {
      expect(screen.getByText('Test Feed')).toBeDefined();
    });
    expect(screen.getByText(/^Podping: last ping at /)).toBeDefined();
  });

  it('says never when no podping has arrived', async () => {
    renderFeedDetail(makeFeed({ podpingCoverage: 'unseen', lastPodpingAt: null }));
    await waitFor(() => {
      expect(screen.getByText('Test Feed')).toBeDefined();
    });
    expect(screen.getByText('Podping: never')).toBeDefined();
  });

  it('shows the opt-in when the feed declares podping', async () => {
    renderFeedDetail(makeFeed({ podpingCoverage: 'declared', lastPodpingAt: null }));
    await waitFor(() => {
      expect(screen.getByText('Test Feed')).toBeDefined();
    });
    expect(screen.getByText('Podping: enabled, none received yet')).toBeDefined();
  });

  it('hides the line when the listener is disabled', async () => {
    renderFeedDetail(makeFeed({ podpingCoverage: null, lastPodpingAt: null }));
    await waitFor(() => {
      expect(screen.getByText('Test Feed')).toBeDefined();
    });
    expect(screen.queryByText(/Podping/)).toBeNull();
  });

  it('hides the line when coverage is absent', async () => {
    renderFeedDetail(makeFeed());
    await waitFor(() => {
      expect(screen.getByText('Test Feed')).toBeDefined();
    });
    expect(screen.queryByText(/Podping/)).toBeNull();
  });
});

describe('FeedDetail: local feed visibility matrix', () => {
  it('hides Refresh Feed and shows the Local badge for a local feed', async () => {
    renderFeedDetail(makeFeed({ feedType: 'local' }));
    await waitFor(() => {
      expect(screen.getByText('Test Feed')).toBeDefined();
    });
    expect(screen.getByText('Local')).toBeDefined();
    expect(screen.queryByTitle('Refresh feed')).toBeNull();
  });

  it('still shows Refresh Feed and no Local badge for a subscribed feed', async () => {
    renderFeedDetail(makeFeed({}));
    await waitFor(() => {
      expect(screen.getByText('Test Feed')).toBeDefined();
    });
    expect(screen.queryByText('Local')).toBeNull();
    expect(screen.getByTitle('Refresh feed')).toBeDefined();
  });
});
