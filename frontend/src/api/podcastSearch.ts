import { apiRequest } from './client';

export interface PodcastSearchResult {
  id: number;
  title: string;
  description: string;
  artworkUrl: string;
  feedUrl: string;
  author: string;
  link: string;
}

interface PodcastSearchResponse {
  results: PodcastSearchResult[];
}

export async function searchPodcasts(query: string, signal?: AbortSignal): Promise<PodcastSearchResult[]> {
  const resp = await apiRequest<PodcastSearchResponse>(
    `/podcast-search?q=${encodeURIComponent(query)}`,
    { signal },
  );
  return resp.results;
}
