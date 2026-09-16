import { apiRequest, buildQueryString } from './client';
import {
  AddressingStats, DashboardStats, DayStats, EpisodeCostResponse, EpisodeCostSortField,
  EpisodeProcessingRun, LedgerFilterOptions, ModelUsageResponse, ModelUsageSortField,
  PodcastStats, ReviewerStats, SpendAttemptsResponse,
} from './types';

// Shared page/filter params for the ledger list endpoints below. Mirrors
// their backend contract: sortBy/sortDir/from/to/podcastSlug/provider/model
// are all sent camelCase, unlike /history's snake_case sort params.
interface LedgerListParams<SortField extends string> {
  page?: number;
  limit?: number;
  sortBy?: SortField;
  sortDir?: 'asc' | 'desc';
  from?: string;
  to?: string;
  podcastSlug?: string;
  provider?: string;
  model?: string;
}

export type ModelUsageQueryParams = LedgerListParams<ModelUsageSortField>;
export type EpisodeCostQueryParams = LedgerListParams<EpisodeCostSortField>;

export async function getDashboardStats(
  podcastSlug?: string
): Promise<DashboardStats> {
  const qs = buildQueryString({ podcast_slug: podcastSlug });
  return apiRequest<DashboardStats>(`/stats/dashboard${qs}`);
}

export async function getStatsByDay(
  podcastSlug?: string
): Promise<{ days: DayStats[] }> {
  const qs = buildQueryString({ podcast_slug: podcastSlug });
  return apiRequest<{ days: DayStats[] }>(`/stats/by-day${qs}`);
}

export async function getStatsByPodcast(): Promise<{ podcasts: PodcastStats[] }> {
  return apiRequest<{ podcasts: PodcastStats[] }>('/stats/by-podcast');
}

export async function getReviewerStats(
  podcastSlug?: string,
  episodeId?: string,
): Promise<ReviewerStats> {
  const qs = buildQueryString({ podcast_slug: podcastSlug, episode_id: episodeId });
  return apiRequest<ReviewerStats>(`/stats/reviewer${qs}`);
}

export async function getAddressingStats(
  podcastSlug?: string,
): Promise<AddressingStats> {
  const qs = buildQueryString({ podcast_slug: podcastSlug });
  return apiRequest<AddressingStats>(`/stats/addressing${qs}`);
}

export async function getModelUsageStats(
  params: ModelUsageQueryParams = {}
): Promise<ModelUsageResponse> {
  const qs = buildQueryString({ ...params });
  return apiRequest<ModelUsageResponse>(`/stats/model-usage${qs}`);
}

export async function getEpisodeCostStats(
  params: EpisodeCostQueryParams = {}
): Promise<EpisodeCostResponse> {
  const qs = buildQueryString({ ...params });
  return apiRequest<EpisodeCostResponse>(`/stats/episode-costs${qs}`);
}

// Per-run, per-phase breakdown for one episode-costs row when expanded.
export async function getEpisodeCostRuns(
  slug: string, episodeId: string
): Promise<{ runs: EpisodeProcessingRun[] }> {
  const qs = buildQueryString({ slug, episodeId });
  return apiRequest<{ runs: EpisodeProcessingRun[] }>(`/stats/episode-costs/runs${qs}`);
}

export interface LedgerFilterScope {
  from?: string;
  to?: string;
  podcastSlug?: string;
}

// Complete provider/model options for the ledger filters: unpaginated, so a
// value that sits past the first list page stays selectable.
export async function getLedgerFilterOptions(
  scope: LedgerFilterScope = {}
): Promise<LedgerFilterOptions> {
  const qs = buildQueryString({ ...scope });
  return apiRequest<LedgerFilterOptions>(`/stats/ledger-filter-options${qs}`);
}

// Scope: one run, or one episode across all its runs.
export interface SpendAttemptsScope {
  runId?: string;
  slug?: string;
  episodeId?: string;
  provider?: string;
}

// The ledger rows a spend total was summed from, so an unknown cost points
// at the attempt carrying no price rather than at a bare total.
export async function getSpendAttempts(
  scope: SpendAttemptsScope,
): Promise<SpendAttemptsResponse> {
  const qs = buildQueryString({ ...scope });
  return apiRequest<SpendAttemptsResponse>(`/stats/spend/attempts${qs}`);
}
