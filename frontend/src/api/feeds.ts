import { apiRequest } from './client';
import { Feed, Episode, EpisodeDetail } from './types';

export async function getFeeds(): Promise<Feed[]> {
  const response = await apiRequest<{ feeds: Feed[] }>('/feeds');
  return response.feeds;
}

export async function getFeed(slug: string): Promise<Feed> {
  return apiRequest<Feed>(`/feeds/${slug}`);
}

export async function addFeed(sourceUrl: string, slug?: string): Promise<Feed> {
  return apiRequest<Feed>('/feeds', {
    method: 'POST',
    body: { sourceUrl, slug },
  });
}

export async function deleteFeed(slug: string): Promise<void> {
  await apiRequest(`/feeds/${slug}`, { method: 'DELETE' });
}

export async function refreshFeed(slug: string): Promise<{ message: string }> {
  return apiRequest<{ message: string }>(`/feeds/${slug}/refresh`, {
    method: 'POST',
  });
}

export async function refreshAllFeeds(): Promise<{ message: string }> {
  return apiRequest<{ message: string }>('/feeds/refresh', {
    method: 'POST',
  });
}

export async function getEpisodes(slug: string): Promise<Episode[]> {
  const response = await apiRequest<{ episodes: Episode[] }>(`/feeds/${slug}/episodes`);
  return response.episodes;
}

export async function getEpisode(slug: string, episodeId: string): Promise<EpisodeDetail> {
  return apiRequest<EpisodeDetail>(`/feeds/${slug}/episodes/${episodeId}`);
}

export async function getArtwork(slug: string): Promise<string> {
  return `/api/v1/feeds/${slug}/artwork`;
}

export async function reprocessEpisode(
  slug: string,
  episodeId: string,
  mode: 'reprocess' | 'full' = 'reprocess'
): Promise<{ message: string; mode: string }> {
  return apiRequest<{ message: string; mode: string }>(`/feeds/${slug}/episodes/${episodeId}/reprocess`, {
    method: 'POST',
    body: { mode },
  });
}

export interface UpdateFeedPayload {
  networkId?: string;
  daiPlatform?: string;
  networkIdOverride?: string | null;  // Network ID override, or null to clear
}

export interface Network {
  id: string;
  name: string;
}

export async function getNetworks(): Promise<Network[]> {
  const response = await apiRequest<{ networks: Network[] }>('/networks');
  return response.networks;
}

export async function updateFeed(slug: string, data: UpdateFeedPayload): Promise<Feed> {
  return apiRequest<Feed>(`/feeds/${slug}`, {
    method: 'PATCH',
    body: data,
  });
}
