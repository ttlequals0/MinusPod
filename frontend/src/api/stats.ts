import { apiRequest, buildQueryString } from './client';
import { DashboardStats, DayStats, PodcastStats } from './types';

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
