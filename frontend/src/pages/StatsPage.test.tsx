import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import StatsPage from './StatsPage';
import type { AddressingStats, DashboardStats, Feed, ReviewerStats } from '../api/types';

// vi.mock factories are hoisted above module-scope const declarations, so
// fixture data referenced inside them has to be built via vi.hoisted too.
const { DASHBOARD, REVIEWER_STATS, FEED, mockGetAddressingStats } = vi.hoisted(() => {
  const dashboard: DashboardStats = {
    totalEpisodesProcessed: 0,
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
  return {
    DASHBOARD: dashboard,
    REVIEWER_STATS: reviewerStats,
    FEED: feed,
    mockGetAddressingStats: vi.fn().mockResolvedValue(addressingStats),
  };
});

vi.mock('../api/stats', () => ({
  getDashboardStats: vi.fn().mockResolvedValue(DASHBOARD),
  getStatsByDay: vi.fn().mockResolvedValue({ days: [] }),
  getStatsByPodcast: vi.fn().mockResolvedValue({ podcasts: [] }),
  getReviewerStats: vi.fn().mockResolvedValue(REVIEWER_STATS),
  getAddressingStats: (...args: unknown[]) => mockGetAddressingStats(...args),
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
      <StatsPage />
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

    await user.selectOptions(screen.getByRole('combobox'), 'a-show');

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
