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
  audioAnalysisOverride?: boolean | null;  // Audio analysis override: true=enable, false=disable, null=use global
  autoProcessOverride?: boolean | null;  // Auto-process override: true=enable, false=disable, null=use global
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

export interface OpmlImportResult {
  imported: number;
  skipped: number;
  failed: number;
  feeds: {
    imported: Array<{ url: string; slug: string }>;
    skipped: Array<{ url: string; slug: string; reason: string }>;
    failed: Array<{ url: string; error: string }>;
  };
}

export async function importOpml(file: File): Promise<OpmlImportResult> {
  const formData = new FormData();
  formData.append('opml', file);

  const response = await fetch('/api/v1/feeds/import-opml', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Import failed: ${response.status}`);
  }

  return response.json();
}

export interface ReprocessAllResult {
  message: string;
  queued: number;
  skipped: number;
  mode: string;
  episodes: {
    queued: Array<{ episodeId: string; title: string }>;
    skipped: Array<{ episodeId: string; reason: string }>;
  };
}

export async function reprocessAllEpisodes(
  slug: string,
  mode: 'reprocess' | 'full' = 'reprocess'
): Promise<ReprocessAllResult> {
  return apiRequest<ReprocessAllResult>(`/feeds/${slug}/reprocess-all`, {
    method: 'POST',
    body: { mode },
  });
}

export interface RegenerateChaptersResult {
  message: string;
  chapterCount: number;
  chapters: Array<{
    title: string;
    startTime: number;
    endTime?: number;
  }>;
}

export async function regenerateChapters(
  slug: string,
  episodeId: string
): Promise<RegenerateChaptersResult> {
  return apiRequest<RegenerateChaptersResult>(
    `/feeds/${slug}/episodes/${episodeId}/regenerate-chapters`,
    { method: 'POST' }
  );
}
