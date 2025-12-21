import { apiRequest } from './client';

export interface SearchResult {
  type: 'episode' | 'podcast' | 'pattern' | 'sponsor';
  id: string;
  podcastSlug: string;
  title: string;
  snippet: string;
  score: number;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  total: number;
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
  type?: 'episode' | 'podcast' | 'pattern' | 'sponsor',
  limit?: number
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query });
  if (type) params.append('type', type);
  if (limit) params.append('limit', limit.toString());

  return apiRequest<SearchResponse>(`/search?${params.toString()}`);
}

export async function rebuildSearchIndex(): Promise<{ message: string; indexedCount: number }> {
  return apiRequest('/search/rebuild', { method: 'POST' });
}

export async function getSearchStats(): Promise<SearchStats> {
  return apiRequest<SearchStats>('/search/stats');
}
