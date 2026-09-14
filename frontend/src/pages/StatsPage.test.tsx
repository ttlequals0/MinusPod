import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import StatsPage from './StatsPage';
import type {
  AddressingStats, DashboardStats, EpisodeCostResponse, Feed, ModelUsageResponse, ReviewerStats,
} from '../api/types';

// vi.mock factories are hoisted above module-scope const declarations, so
// fixture data referenced inside them has to be built via vi.hoisted too.
const {
  DASHBOARD, REVIEWER_STATS, FEED, EMPTY_MODEL_USAGE,
  mockGetAddressingStats, mockGetDashboardStats, mockGetStatsByDay,
  mockGetModelUsageStats, mockGetEpisodeCostStats,
} = vi.hoisted(() => {
  const dashboard: DashboardStats = {
    totalEpisodesProcessed: 0,
    totalRuns: 0,
    episodesWithTimeSaved: 0,
    avgTimeSavedSeconds: 0,
    minTimeSavedSeconds: 0,
    maxTimeSavedSeconds: 0,
    totalTimeSavedSeconds: 0,
    avgAdsRemoved: 0,
    minAdsRemoved: 0,
    maxAdsRemoved: 0,
    totalAdsRemoved: 0,
    avgCostPerEpisode: 0,
    minCostPerEpisode: 0,
    maxCostPerEpisode: 0,
    avgProcessingTimeSeconds: 0,
    minProcessingTimeSeconds: 0,
    maxProcessingTimeSeconds: 0,
    avgEpisodeLengthSeconds: 0,
    minEpisodeLengthSeconds: 0,
    maxEpisodeLengthSeconds: 0,
    totalInputTokens: 0,
    totalOutputTokens: 0,
    totalLlmCost: 0,
    avgInputTokens: 0,
    avgOutputTokens: 0,
    avgAudioCuesDetected: 0,
    minAudioCuesDetected: 0,
    maxAudioCuesDetected: 0,
    totalAudioCuesDetected: 0,
  };
  const reviewerStats: ReviewerStats = {
    totalReviews: 0,
    verdictCounts: { confirmed: 0, adjust: 0, reject: 0, resurrect: 0, failure: 0 },
    pass1AdjustmentCount: 0,
    pass2AdjustmentCount: 0,
    avgBoundaryShiftSeconds: 0,
    resurrectionCount: 0,
    failureCount: 0,
  };
  const addressingStats: AddressingStats = {
    modes: {
      timestamps: {
        runs: 4, windowsJudged: 20, windowsCompliant: 18, compliancePct: 90.0,
        yieldRuns: 0, adsProposed: 0, adsKept: 0, adsDroppedInvalidRef: 0,
        adsDroppedOutOfWindow: 0, adsDroppedTooLong: 0, keptPct: 0.0,
      },
      segment_ids: {
        runs: 2, windowsJudged: 10, windowsCompliant: 6, compliancePct: 60.0,
        yieldRuns: 2, adsProposed: 10, adsKept: 7, adsDroppedInvalidRef: 2,
        adsDroppedOutOfWindow: 1, adsDroppedTooLong: 0, keptPct: 70.0,
      },
    },
  };
  const feed: Feed = {
    slug: 'a-show', title: 'A Show', sourceUrl: 'https://example.com/feed.xml',
    feedUrl: 'https://example.com/feed.xml', episodeCount: 3,
  };
  const emptyModelUsage: ModelUsageResponse = { items: [], total: 0, totalPages: 1, page: 1, limit: 20 };
  const emptyEpisodeCosts: EpisodeCostResponse = { items: [], total: 0, totalPages: 1, page: 1, limit: 20 };
  return {
    DASHBOARD: dashboard,
    REVIEWER_STATS: reviewerStats,
    FEED: feed,
    EMPTY_MODEL_USAGE: emptyModelUsage,
    EMPTY_EPISODE_COSTS: emptyEpisodeCosts,
    mockGetAddressingStats: vi.fn().mockResolvedValue(addressingStats),
    mockGetDashboardStats: vi.fn().mockResolvedValue(dashboard),
    mockGetStatsByDay: vi.fn().mockResolvedValue({ days: [] }),
    mockGetModelUsageStats: vi.fn().mockResolvedValue(emptyModelUsage),
    mockGetEpisodeCostStats: vi.fn().mockResolvedValue(emptyEpisodeCosts),
  };
});

vi.mock('../api/stats', () => ({
  getDashboardStats: (...args: unknown[]) => mockGetDashboardStats(...args),
  getStatsByDay: (...args: unknown[]) => mockGetStatsByDay(...args),
  getStatsByPodcast: vi.fn().mockResolvedValue({ podcasts: [] }),
  getReviewerStats: vi.fn().mockResolvedValue(REVIEWER_STATS),
  getAddressingStats: (...args: unknown[]) => mockGetAddressingStats(...args),
  getModelUsageStats: (...args: unknown[]) => mockGetModelUsageStats(...args),
  getEpisodeCostStats: (...args: unknown[]) => mockGetEpisodeCostStats(...args),
}));
vi.mock('../api/cueDetections', () => ({
  getCueAggregateStats: vi.fn().mockResolvedValue({
    total: 0, snapped: 0, paired: 0, unused: 0, confirmed: 0, rejected: 0,
    pending: 0, avgScore: null, minScore: null, maxScore: null, confirmRate: null,
    scoreHistogram: [], nearMissHistogram: [], nearMissTotal: 0, unusedReasons: {},
  }),
}));
vi.mock('../api/feeds', () => ({
  feedsQueryOptions: { queryKey: ['feeds'], queryFn: () => Promise.resolve({ feeds: [FEED] }) },
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <StatsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('StatsPage addressing modes section', () => {
  it('renders both mode cards with fetched runs/judged/compliance values', async () => {
    renderPage();

    expect(await screen.findByText('Addressing modes')).toBeTruthy();
    expect(screen.getByText('Timestamps')).toBeTruthy();
    expect(screen.getByText('Segment IDs')).toBeTruthy();
    expect(screen.getByText('90.0%')).toBeTruthy();
    expect(screen.getByText('60.0%')).toBeTruthy();
    // Runs and windows-judged values per mode. '2' also appears as the
    // segment_ids Yield runs metric, so count rather than assert-single.
    expect(screen.getByText('4')).toBeTruthy();
    expect(screen.getByText('20')).toBeTruthy();
    expect(screen.getAllByText('2').length).toBeGreaterThan(0);
    expect(screen.getByText('10')).toBeTruthy();
  });

  it('passes the podcast filter through to getAddressingStats', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Addressing modes');
    // Initial mount: no filter selected yet.
    expect(mockGetAddressingStats).toHaveBeenCalledWith(undefined);

    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter page by podcast' }), 'a-show');

    await waitFor(() => {
      expect(mockGetAddressingStats).toHaveBeenCalledWith('a-show');
    });
  });
});

describe('StatsPage addressing yield', () => {
  it('shows kept rate and drop counts once yield data exists', async () => {
    renderPage();
    expect(await screen.findByText('70.0%')).toBeTruthy();
    expect(screen.getByText('7 / 10')).toBeTruthy();
    expect(screen.getByText(/2 invalid ref/)).toBeTruthy();
  });

  it('says yield has no data yet instead of showing a fake zero', async () => {
    renderPage();
    // The timestamps card in the fixture has yieldRuns 0.
    expect(await screen.findByText('No yield data yet')).toBeTruthy();
  });
});

describe('StatsPage loading placeholders', () => {
  beforeEach(() => {
    mockGetDashboardStats.mockReset();
    mockGetDashboardStats.mockResolvedValue(DASHBOARD);
    mockGetStatsByDay.mockReset();
    mockGetStatsByDay.mockResolvedValue({ days: [] });
  });

  it('shows summary card skeletons while the dashboard query is pending, not a page spinner', async () => {
    mockGetDashboardStats.mockReturnValue(new Promise(() => {}));
    renderPage();
    // The by-day chart heading proves the rest of the page rendered rather
    // than the whole page waiting behind one spinner.
    expect(await screen.findByText('Episodes Processed by Day')).toBeTruthy();
    expect(screen.getAllByTestId('skeleton-stat-cards').length).toBeGreaterThanOrEqual(1);
  });

  it('shows a chart skeleton while the by-day query is pending', async () => {
    mockGetStatsByDay.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(await screen.findByText('Total Episodes')).toBeTruthy();
    expect(screen.getAllByTestId('skeleton-chart').length).toBeGreaterThanOrEqual(1);
  });

  it('points at AI & Processing for enabling the reviewer', async () => {
    renderPage();
    expect(await screen.findByText(/Enable Ad Reviewer in Settings, AI & Processing section/)).toBeTruthy();
  });
});

describe('StatsPage LLM cost ledger', () => {
  const MODEL_USAGE_PAGE: ModelUsageResponse = {
    items: [{
      provider: 'anthropic', model: 'claude-sonnet', calls: 12, distinctEpisodes: 5,
      inputTokens: 40000, outputTokens: 8000, knownCostUsd: '1.234500', unknownCostCount: 0,
    }],
    total: 40, totalPages: 2, page: 1, limit: 20,
  };
  const EPISODE_COST_PAGE: EpisodeCostResponse = {
    items: [{
      podcastSlug: 'a-show', podcastTitle: 'A Show', episodeId: 'ep-1', episodeTitle: 'Episode One',
      modelsUsed: ['claude-sonnet'], runCount: 2, latestRunCostUsd: '0.500000',
      cumulativeCostUsd: '0.900000', lastActivityAt: '2026-09-01T12:00:00Z',
    }],
    total: 1, totalPages: 1, page: 1, limit: 20,
  };

  // The options query (limit 100) and the main list query (limit 20) both
  // go through getModelUsageStats; branch on limit to tell them apart.
  function lastMainListParams() {
    const calls = mockGetModelUsageStats.mock.calls.filter(([params]) => params.limit === 20);
    return calls[calls.length - 1][0];
  }

  beforeEach(() => {
    // The "loading placeholders" describe above leaves a never-resolving
    // promise on these two mocks after its last test; restore them here so
    // this describe isn't left waiting on a query that never settles.
    mockGetDashboardStats.mockReset();
    mockGetDashboardStats.mockResolvedValue(DASHBOARD);
    mockGetStatsByDay.mockReset();
    mockGetStatsByDay.mockResolvedValue({ days: [] });
    mockGetModelUsageStats.mockReset();
    mockGetModelUsageStats.mockImplementation((params: { limit?: number }) =>
      Promise.resolve(params.limit === 100 ? EMPTY_MODEL_USAGE : MODEL_USAGE_PAGE));
    mockGetEpisodeCostStats.mockReset();
    mockGetEpisodeCostStats.mockResolvedValue(EPISODE_COST_PAGE);
  });

  it('paginates and sorts the model-usage list server-side, keyed by its params', async () => {
    const user = userEvent.setup();
    renderPage();
    const table = await screen.findByRole('table', { name: 'Provider and model usage' });
    await within(table).findByText('claude-sonnet');

    await user.click(within(table).getByRole('columnheader', { name: /Calls/ }));
    await waitFor(() => {
      expect(lastMainListParams()).toMatchObject({ sortBy: 'calls', sortDir: 'desc', page: 1 });
    });

    await user.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => {
      expect(lastMainListParams()).toMatchObject({ page: 2 });
    });
  });

  it('resets both ledger lists to page 1 when a filter changes', async () => {
    const user = userEvent.setup();
    renderPage();
    const table = await screen.findByRole('table', { name: 'Provider and model usage' });
    await within(table).findByText('claude-sonnet');

    await user.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => {
      expect(lastMainListParams()).toMatchObject({ page: 2 });
    });

    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter ledger by podcast' }), 'a-show');
    await waitFor(() => {
      expect(lastMainListParams()).toMatchObject({ page: 1, podcastSlug: 'a-show' });
    });
  });

  it('links each episode-cost row to the episode detail page', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'Episode costs' });
    const link = within(table).getByRole('link', { name: 'Episode One' });
    expect(link.getAttribute('href')).toBe('/feeds/a-show/episodes/ep-1');
  });

  it('labels spend as lifetime by default and interval once a date filter is set', async () => {
    renderPage();
    expect(await screen.findByText('Lifetime spend (all recorded runs)')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-01-01' } });
    await waitFor(() => {
      expect(screen.getByText(/Interval spend from 2026-01-01/)).toBeTruthy();
    });
  });
});
