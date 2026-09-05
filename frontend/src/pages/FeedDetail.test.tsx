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

const mockNavigate = vi.fn();
let mockLocationState: { notice?: string } | null = null;

vi.mock('react-router', () => ({
  useParams: () => ({ slug: 'test-feed' }),
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: '/feeds/test-feed', state: mockLocationState }),
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
  mockLocationState = null;
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

// #718: the copy control shrinks to an icon below sm so it stays in the left
// group instead of wrapping onto its own row above Reprocess/Refresh/Delete.
// Label is hidden directly (not via CopyButton's hideLabelOnMobile) because
// that prop's baseClass carries its own sm:px-2 and unprefixed h-8, which tie
// with this call site's sm:px-0/sm:p-1.5 desktop classes and can win,
// regressing desktop sizing (see task-8 fix report for the computed-style proof).
describe('FeedDetail: copy control mobile sizing', () => {
  it('does not carry the unprefixed px-4 py-2 padding that fights icon-only sizing', async () => {
    renderFeedDetail(makeFeed());
    await waitFor(() => {
      expect(screen.getByText('Test Feed')).toBeDefined();
    });
    const copyButton = screen.getByTitle('Copy Feed URL');
    expect(copyButton.className).not.toContain('px-4 py-2');
    expect(copyButton.className).not.toContain('sm:px-2');
    expect(copyButton.className).not.toContain('h-8 w-8');
  });

  it('hides the label with a plain responsive class instead of hideLabelOnMobile', async () => {
    renderFeedDetail(makeFeed());
    await waitFor(() => {
      expect(screen.getByText('Test Feed')).toBeDefined();
    });
    const label = screen.getByTitle('Copy Feed URL').querySelector('span');
    expect(label?.className).toContain('hidden');
    expect(label?.className).toContain('sm:inline');
  });
});

describe('FeedDetail: notice from router state', () => {
  it('surfaces a notice left in router state (e.g. from AddFeed local mode) as a toast', async () => {
    mockLocationState = { notice: 'Feed created. Artwork upload failed. Retry from the feed page.' };
    renderFeedDetail(makeFeed());
    await waitFor(() => {
      expect(screen.getByText('Feed created. Artwork upload failed. Retry from the feed page.')).toBeDefined();
    });
    // Consumed once: history state is cleared so a refresh/back doesn't replay it.
    expect(mockNavigate).toHaveBeenCalledWith('/feeds/test-feed', { replace: true, state: null });
  });

  it('shows no toast when router state carries no notice', async () => {
    renderFeedDetail(makeFeed());
    await waitFor(() => {
      expect(screen.getByText('Test Feed')).toBeDefined();
    });
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});
