import { apiRequest, buildQueryString } from './client';

export interface QuickSearchFeed { slug: string; title: string }
export interface QuickSearchEpisode {
  feedSlug: string; feedTitle: string; episodeId: string;
  title: string; status: string; publishDate: string | null;
}
export interface QuickSearchResponse {
  query: string; feeds: QuickSearchFeed[]; episodes: QuickSearchEpisode[];
}

export async function quickSearch(q: string, signal?: AbortSignal): Promise<QuickSearchResponse> {
  return apiRequest<QuickSearchResponse>(`/quick-search${buildQueryString({ q })}`, { signal });
}
