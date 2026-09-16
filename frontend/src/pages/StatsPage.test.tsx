import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import StatsPage from './StatsPage';
import type {
  AddressingStats, DashboardStats, EpisodeCostResponse, Feed, LedgerFilterOptions,
  ModelUsageResponse, ReviewerStats,
} from '../api/types';

// vi.mock factories are hoisted above module-scope const declarations, so
// fixture data referenced inside them has to be built via vi.hoisted too.
const {
  DASHBOARD, REVIEWER_STATS, FEED, FILTER_OPTIONS,
  mockGetAddressingStats, mockGetDashboardStats, mockGetStatsByDay,
  mockGetModelUsageStats, mockGetEpisodeCostStats, mockGetLedgerFilterOptions,
  mockGetEpisodeCostRuns, mockGetStatsByPodcast,
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
  const filterOptions: LedgerFilterOptions = {
    providers: ['anthropic', 'openrouter'],
    pairs: [
      { provider: 'anthropic', model: 'claude-sonnet' },
      { provider: 'openrouter', model: 'llama-3' },
    ],
  };
  return {
    DASHBOARD: dashboard,
    REVIEWER_STATS: reviewerStats,
    FEED: feed,
    FILTER_OPTIONS: filterOptions,
    mockGetAddressingStats: vi.fn().mockResolvedValue(addressingStats),
    mockGetDashboardStats: vi.fn().mockResolvedValue(dashboard),
    mockGetStatsByDay: vi.fn().mockResolvedValue({ days: [] }),
    mockGetModelUsageStats: vi.fn().mockResolvedValue(emptyModelUsage),
    mockGetEpisodeCostStats: vi.fn().mockResolvedValue(emptyEpisodeCosts),
    mockGetLedgerFilterOptions: vi.fn().mockResolvedValue(filterOptions),
    mockGetEpisodeCostRuns: vi.fn().mockResolvedValue({ runs: [] }),
    mockGetStatsByPodcast: vi.fn().mockResolvedValue({ podcasts: [] }),
  };
});

vi.mock('../api/stats', () => ({
  getDashboardStats: (...args: unknown[]) => mockGetDashboardStats(...args),
  getStatsByDay: (...args: unknown[]) => mockGetStatsByDay(...args),
  getStatsByPodcast: (...args: unknown[]) => mockGetStatsByPodcast(...args),
  getReviewerStats: vi.fn().mockResolvedValue(REVIEWER_STATS),
  getAddressingStats: (...args: unknown[]) => mockGetAddressingStats(...args),
  getModelUsageStats: (...args: unknown[]) => mockGetModelUsageStats(...args),
  getEpisodeCostStats: (...args: unknown[]) => mockGetEpisodeCostStats(...args),
  getEpisodeCostRuns: (...args: unknown[]) => mockGetEpisodeCostRuns(...args),
  getLedgerFilterOptions: (...args: unknown[]) => mockGetLedgerFilterOptions(...args),
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

    await user.selectOptions(screen.getByRole('combobox', { name: /Filter summary cards/ }), 'a-show');

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
      modelsUsed: ['claude-sonnet'], topModel: 'claude-sonnet', runCount: 2, latestRunCostUsd: '0.500000',
      latestRunUnknownCount: 0,
      cumulativeCostUsd: '0.900000', lastActivityAt: '2026-09-01T12:00:00Z',
      unknownCostCount: 0, hasUnknownCost: false,
    }],
    total: 1, totalPages: 1, page: 1, limit: 20,
  };

  function lastMainListParams() {
    const calls = mockGetModelUsageStats.mock.calls;
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
    mockGetModelUsageStats.mockResolvedValue(MODEL_USAGE_PAGE);
    mockGetEpisodeCostStats.mockReset();
    mockGetEpisodeCostStats.mockResolvedValue(EPISODE_COST_PAGE);
    mockGetLedgerFilterOptions.mockReset();
    mockGetLedgerFilterOptions.mockResolvedValue(FILTER_OPTIONS);
  });

  it('paginates and sorts the model-usage list server-side, keyed by its params', async () => {
    const user = userEvent.setup();
    renderPage();
    const table = await screen.findByRole('table', { name: 'Provider and model usage' });
    await within(table).findByText('claude-sonnet');

    await user.click(within(table).getByRole('button', { name: /Calls/ }));
    await waitFor(() => {
      expect(lastMainListParams()).toMatchObject({ sortBy: 'calls', sortDir: 'desc', page: 1 });
    });

    await user.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => {
      expect(lastMainListParams()).toMatchObject({ page: 2 });
    });
  });

  it('sorts model usage from mobile controls and resets pagination', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('combobox', { name: 'Sort model usage by' });
    await user.click(screen.getByRole('button', { name: 'Next' }));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Sort model usage by' }), 'calls');
    await waitFor(() => {
      expect(lastMainListParams()).toMatchObject({ page: 1, sortBy: 'calls', sortDir: 'desc' });
    });
    await user.click(screen.getByRole('button', { name: 'Sort model usage ascending' }));
    await waitFor(() => {
      expect(lastMainListParams()).toMatchObject({ sortBy: 'calls', sortDir: 'asc' });
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

    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter spend by podcast' }), 'a-show');
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

    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-01-01' } });
    await waitFor(() => {
      expect(screen.getByText(/Interval spend from 2026-01-01/)).toBeTruthy();
    });
  });
});

describe('StatsPage ledger: incomplete cost display', () => {
  const UNPRICED_MODEL_USAGE: ModelUsageResponse = {
    items: [{
      provider: 'openrouter', model: 'llama-3', calls: 12, distinctEpisodes: 5,
      inputTokens: 40000, outputTokens: 8000, knownCostUsd: '1.234500', unknownCostCount: 3,
    }],
    total: 1, totalPages: 1, page: 1, limit: 20,
  };
  const UNPRICED_EPISODE_COSTS: EpisodeCostResponse = {
    items: [{
      podcastSlug: 'a-show', podcastTitle: 'A Show', episodeId: 'ep-1', episodeTitle: 'Episode One',
      modelsUsed: ['llama-3'], topModel: 'llama-3', runCount: 2, latestRunCostUsd: '0.000000',
      latestRunUnknownCount: 2,
      cumulativeCostUsd: '0.900000', lastActivityAt: '2026-09-01T12:00:00Z',
      unknownCostCount: 4, hasUnknownCost: true,
    }],
    total: 1, totalPages: 1, page: 1, limit: 20,
  };

  beforeEach(() => {
    mockGetDashboardStats.mockReset();
    mockGetDashboardStats.mockResolvedValue(DASHBOARD);
    mockGetStatsByDay.mockReset();
    mockGetStatsByDay.mockResolvedValue({ days: [] });
    mockGetModelUsageStats.mockReset();
    mockGetModelUsageStats.mockResolvedValue(UNPRICED_MODEL_USAGE);
    mockGetEpisodeCostStats.mockReset();
    mockGetEpisodeCostStats.mockResolvedValue(UNPRICED_EPISODE_COSTS);
    mockGetLedgerFilterOptions.mockReset();
    mockGetLedgerFilterOptions.mockResolvedValue(FILTER_OPTIONS);
  });

  it('labels a partly unpriced model row as known spend, not as the total', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'Provider and model usage' });
    expect(within(table).getByText(/Known \$1\.2345/)).toBeTruthy();
    expect(within(table).getAllByText('Incomplete').length).toBeGreaterThan(0);
    expect(within(table).getByText('3 of 12 calls unpriced')).toBeTruthy();
  });

  it('says the breakdown is unavailable when an episode has no priced spend', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'Episode costs' });
    expect(within(table).getByText('Breakdown unavailable')).toBeTruthy();
    expect(within(table).getByText(/Known \$0\.9000/)).toBeTruthy();
    expect(within(table).getAllByText('Incomplete').length).toBe(1);
  });

  it('counts the unpriced calls in the badge title', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'Episode costs' });
    expect(within(table).getByText('Incomplete').getAttribute('title')).toMatch(/4 unpriced calls/);
  });
});

describe('StatsPage ledger: failed queries', () => {
  beforeEach(() => {
    mockGetDashboardStats.mockReset();
    mockGetDashboardStats.mockResolvedValue(DASHBOARD);
    mockGetStatsByDay.mockReset();
    mockGetStatsByDay.mockResolvedValue({ days: [] });
    mockGetModelUsageStats.mockReset();
    mockGetEpisodeCostStats.mockReset();
    mockGetEpisodeCostStats.mockResolvedValue({ items: [], total: 0, totalPages: 1, page: 1, limit: 20 });
    mockGetLedgerFilterOptions.mockReset();
    mockGetLedgerFilterOptions.mockResolvedValue(FILTER_OPTIONS);
  });

  it('tells a failed usage request apart from an absence of spend, and retries', async () => {
    const user = userEvent.setup();
    mockGetModelUsageStats.mockRejectedValueOnce(new Error('HTTP 500'));
    mockGetModelUsageStats.mockResolvedValue({ items: [], total: 0, totalPages: 1, page: 1, limit: 20 });
    renderPage();

    const alert = await screen.findByText(/Could not load provider and model usage/);
    expect(alert.textContent).toMatch(/not an absence of spend/);
    expect(screen.queryByRole('table', { name: 'Provider and model usage' })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('table', { name: 'Provider and model usage' })).toBeTruthy();
  });

  it('says the filter options are incomplete when their request fails', async () => {
    mockGetModelUsageStats.mockResolvedValue({ items: [], total: 0, totalPages: 1, page: 1, limit: 20 });
    mockGetLedgerFilterOptions.mockRejectedValue(new Error('HTTP 503'));
    renderPage();

    expect(await screen.findByText(/Could not load the provider and model filter options/)).toBeTruthy();
  });
});

describe('StatsPage ledger filters', () => {
  beforeEach(() => {
    mockGetDashboardStats.mockReset();
    mockGetDashboardStats.mockResolvedValue(DASHBOARD);
    mockGetStatsByDay.mockReset();
    mockGetStatsByDay.mockResolvedValue({ days: [] });
    mockGetModelUsageStats.mockReset();
    mockGetModelUsageStats.mockResolvedValue({ items: [], total: 0, totalPages: 1, page: 1, limit: 20 });
    mockGetEpisodeCostStats.mockReset();
    mockGetEpisodeCostStats.mockResolvedValue({ items: [], total: 0, totalPages: 1, page: 1, limit: 20 });
    mockGetLedgerFilterOptions.mockReset();
    mockGetLedgerFilterOptions.mockResolvedValue(FILTER_OPTIONS);
  });

  it('fills the filter selects from the dedicated options endpoint', async () => {
    renderPage();
    const providers = await screen.findByRole('combobox', { name: 'Filter spend by provider' });
    await waitFor(() => {
      expect(within(providers).getByRole('option', { name: 'openrouter' })).toBeTruthy();
    });
    const models = screen.getByRole('combobox', { name: 'Filter spend by model' });
    expect(within(models).getByRole('option', { name: 'llama-3' })).toBeTruthy();
  });

  it('narrows the model list to the selected provider', async () => {
    const user = userEvent.setup();
    renderPage();
    const providers = await screen.findByRole('combobox', { name: 'Filter spend by provider' });
    await waitFor(() => {
      expect(within(providers).getByRole('option', { name: 'anthropic' })).toBeTruthy();
    });

    await user.selectOptions(providers, 'anthropic');

    const models = screen.getByRole('combobox', { name: 'Filter spend by model' });
    expect(within(models).getByRole('option', { name: 'claude-sonnet' })).toBeTruthy();
    expect(within(models).queryByRole('option', { name: 'llama-3' })).toBeNull();
  });

  it('sends the plain date the backend reads as a whole UTC day', async () => {
    renderPage();
    await waitFor(() => expect(mockGetLedgerFilterOptions).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-01-01' } });
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-01-31' } });

    await waitFor(() => {
      const calls = mockGetEpisodeCostStats.mock.calls;
      expect(calls[calls.length - 1][0]).toMatchObject({ from: '2026-01-01', to: '2026-01-31' });
    });
    expect(screen.getByText(/UTC days, both included/)).toBeTruthy();
  });
});

describe('StatsPage episode costs: expandable run/phase breakdown', () => {
  const MULTI_MODEL_COSTS: EpisodeCostResponse = {
    items: [{
      podcastSlug: 'a-show', podcastTitle: 'A Show', episodeId: 'ep-1',
      episodeTitle: 'Episode One',
      modelsUsed: ['claude-opus-4', 'claude-sonnet-5', 'gpt-4o'],
      topModel: 'claude-opus-4', runCount: 1, latestRunCostUsd: '2.980000',
      latestRunUnknownCount: 0,
      cumulativeCostUsd: '2.980000', lastActivityAt: '2026-09-01T12:00:00Z',
      unknownCostCount: 0, hasUnknownCost: false,
    }],
    total: 1, totalPages: 1, page: 1, limit: 20,
  };
  const RUNS = {
    runs: [{
      runNumber: 1, processedAt: '2026-09-01T12:00:00Z', status: 'completed' as const,
      adsDetected: 4, processingDurationSeconds: 120, errorMessage: null,
      inputTokens: 530000, outputTokens: 16000, llmCost: 2.98, hasLog: false,
      stats: null, breakdownAvailable: true,
      phases: [{
        phaseKey: 'detection', invokingPass: 1, provider: 'anthropic',
        configuredModel: 'claude-opus-4', returnedModel: 'claude-opus-4',
        inputTokens: 125000, outputTokens: 3400, cacheReadTokens: 0,
        cacheWriteTokens: 0, reasoningTokens: 0, costUsd: '2.130000',
        costSource: 'estimated' as const,
      }],
    }],
  };

  beforeEach(() => {
    mockGetDashboardStats.mockReset();
    mockGetDashboardStats.mockResolvedValue(DASHBOARD);
    mockGetStatsByDay.mockReset();
    mockGetStatsByDay.mockResolvedValue({ days: [] });
    mockGetModelUsageStats.mockReset();
    mockGetModelUsageStats.mockResolvedValue({ items: [], total: 0, totalPages: 1, page: 1, limit: 20 });
    mockGetEpisodeCostStats.mockReset();
    mockGetEpisodeCostStats.mockResolvedValue(MULTI_MODEL_COSTS);
    mockGetLedgerFilterOptions.mockReset();
    mockGetLedgerFilterOptions.mockResolvedValue(FILTER_OPTIONS);
    mockGetEpisodeCostRuns.mockReset();
    mockGetEpisodeCostRuns.mockResolvedValue(RUNS);
  });

  it('shows the most-expensive model first with a +N count', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'Episode costs' });
    expect(within(table).getByText('claude-opus-4')).toBeTruthy();
    expect(within(table).getByText('+2')).toBeTruthy();
  });

  it('fetches and renders the run/phase breakdown when expanded', async () => {
    const user = userEvent.setup();
    renderPage();
    const table = await screen.findByRole('table', { name: 'Episode costs' });
    await user.click(within(table).getByRole('button', { name: /Show run breakdown for Episode One/ }));
    await waitFor(() => expect(mockGetEpisodeCostRuns).toHaveBeenCalledWith('a-show', 'ep-1'));
    // The episode row expands to the run table (grouped by run); expanding the
    // run reveals its per-phase, per-model breakdown.
    const runToggles = await screen.findAllByRole('button', { name: /Show phase breakdown for run/ });
    await user.click(runToggles[0]);
    await waitFor(() => expect(screen.getAllByText(/Detection/).length).toBeGreaterThan(0));
    expect(screen.getAllByText('claude-opus-4').length).toBeGreaterThan(1);
  });
});

describe('StatsPage spend section: copy, labels and table chrome', () => {
  const PODCASTS = {
    podcasts: [{
      podcastSlug: 'a-show', podcastTitle: 'A Show', episodeCount: 3, runCount: 4,
      totalAds: 9, avgAds: 3, avgEpisodeLengthSeconds: 1800, avgTimeSavedSeconds: 120,
      totalCost: 1.5, totalInputTokens: 1000, totalOutputTokens: 200, avgTokensPerEpisode: 400,
    }],
  };

  beforeEach(() => {
    mockGetDashboardStats.mockReset();
    mockGetDashboardStats.mockResolvedValue(DASHBOARD);
    mockGetStatsByDay.mockReset();
    mockGetStatsByDay.mockResolvedValue({ days: [] });
    mockGetModelUsageStats.mockReset();
    mockGetModelUsageStats.mockResolvedValue({ items: [], total: 0, totalPages: 1, page: 1, limit: 20 });
    mockGetEpisodeCostStats.mockReset();
    mockGetEpisodeCostStats.mockResolvedValue({ items: [], total: 0, totalPages: 1, page: 1, limit: 20 });
    mockGetStatsByPodcast.mockReset();
    mockGetStatsByPodcast.mockResolvedValue({ podcasts: [] });
  });

  it('names the section for spend rather than for the table it is stored in', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: 'LLM spend' })).toBeTruthy();
    expect(screen.queryByText(/ledger/i)).toBeNull();
  });

  it('gives the two date fields visible labels', async () => {
    renderPage();
    const from = await screen.findByLabelText('From');
    expect(from.getAttribute('type')).toBe('date');
    expect(screen.getByLabelText('To').getAttribute('type')).toBe('date');
    expect(from.id).toBeTruthy();
    expect(document.querySelector(`label[for="${from.id}"]`)?.textContent).toBe('From');
  });

  it('sizes the spend-table placeholders to rows, not to a chart', async () => {
    mockGetModelUsageStats.mockReturnValue(new Promise(() => {}));
    mockGetEpisodeCostStats.mockReturnValue(new Promise(() => {}));
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId('skeleton-rows').length).toBe(2));
  });

  it('keeps the previous page on screen while the next one loads', async () => {
    const user = userEvent.setup();
    mockGetModelUsageStats.mockResolvedValue({
      items: [{
        provider: 'anthropic', model: 'claude-sonnet', calls: 12, distinctEpisodes: 5,
        inputTokens: 40000, outputTokens: 8000, knownCostUsd: '1.234500', unknownCostCount: 0,
      }],
      total: 40, totalPages: 2, page: 1, limit: 20,
    });
    renderPage();
    const table = await screen.findByRole('table', { name: 'Provider and model usage' });
    await within(table).findByText('claude-sonnet');

    mockGetModelUsageStats.mockReturnValue(new Promise(() => {}));
    await user.click(screen.getAllByRole('button', { name: 'Next' })[0]);

    await waitFor(() => expect(mockGetModelUsageStats).toHaveBeenCalledTimes(2));
    const live = screen.getByRole('table', { name: 'Provider and model usage' });
    expect(within(live).getByText('claude-sonnet')).toBeTruthy();
    expect(screen.queryAllByTestId('skeleton-rows')).toHaveLength(0);
  });

  it('gives the by-podcast table the same accessible sort headers as the spend tables', async () => {
    mockGetStatsByPodcast.mockResolvedValue(PODCASTS);
    renderPage();
    const table = await screen.findByRole('table', { name: 'Podcast totals' });
    const header = within(table).getByRole('columnheader', { name: 'Episodes' });
    expect(header.getAttribute('aria-sort')).toBe('none');
    expect(within(header).getByRole('button', { name: 'Episodes' })).toBeTruthy();
  });
});
