import { apiRequest, buildQueryString } from './client';
import {
  AddressingStats, DashboardStats, DayStats, EpisodeCostResponse, EpisodeCostSortField,
  ModelUsageResponse, ModelUsageSortField, PodcastStats, ReviewerStats,
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
