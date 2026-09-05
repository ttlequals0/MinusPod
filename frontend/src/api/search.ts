import { apiRequest, buildQueryString } from './client';

export interface SearchShowResult {
  slug: string;
  title: string;
  snippet: string | null;
}

export interface SearchEpisodeResult {
  feedSlug: string;
  feedTitle: string;
  episodeId: string;
  title: string;
  status: string;
  publishDate: string | null;
  snippet: string | null;
}

export interface SearchTranscriptResult {
  feedSlug: string;
  episodeId: string;
  title: string;
  snippet: string;
  timestamp: number | null;
}

export interface SearchPatternResult {
  id: string;
  scope: string;
  sponsor: string;
  snippet: string | null;
}

export interface SearchSponsorResult {
  id: string;
  name: string;
  snippet: string | null;
}

export interface SearchResponse {
  query: string;
  shows: SearchShowResult[];
  episodes: SearchEpisodeResult[];
  transcripts: SearchTranscriptResult[];
  patterns: SearchPatternResult[];
  sponsors: SearchSponsorResult[];
}

export interface SearchStats {
  stats: {
    episode?: number;
    podcast?: number;
    pattern?: number;
    sponsor?: number;
    total: number;
  };
}

export async function search(
  query: string,
  limit?: number,
  signal?: AbortSignal,
  groups?: string
): Promise<SearchResponse> {
  const qs = buildQueryString({ q: query, limit, groups });
  return apiRequest<SearchResponse>(`/search${qs}`, { signal });
}

export async function rebuildSearchIndex(): Promise<{ message: string; indexedCount: number }> {
  return apiRequest('/search/rebuild', { method: 'POST' });
}

export async function getSearchStats(): Promise<SearchStats> {
  return apiRequest<SearchStats>('/search/stats');
}
