import { apiRequest } from './client';

export interface AdPattern {
  id: number;
  scope: string;
  network_id: string | null;
  podcast_id: string | null;
  dai_platform: string | null;
  text_template: string | null;
  intro_variants: string;
  outro_variants: string;
  sponsor: string | null;
  confirmation_count: number;
  false_positive_count: number;
  last_matched_at: string | null;
  created_at: string;
  created_from_episode_id: string | null;
  is_active: boolean;
  disabled_at: string | null;
  disabled_reason: string | null;
}

export interface PatternCorrection {
  type: 'confirm' | 'reject' | 'adjust';
  original_ad: {
    start: number;
    end: number;
    pattern_id?: number;
    confidence?: number;
    reason?: string;
    sponsor?: string;
  };
  adjusted_start?: number;
  adjusted_end?: number;
  notes?: string;
}

// Pattern API

export async function getPatterns(params?: {
  scope?: string;
  podcast_id?: string;
  network_id?: string;
  active?: boolean;
}): Promise<AdPattern[]> {
  const searchParams = new URLSearchParams();
  if (params?.scope) searchParams.set('scope', params.scope);
  if (params?.podcast_id) searchParams.set('podcast_id', params.podcast_id);
  if (params?.network_id) searchParams.set('network_id', params.network_id);
  if (params?.active !== undefined) searchParams.set('active', String(params.active));

  const queryString = searchParams.toString();
  const url = queryString ? `/patterns?${queryString}` : '/patterns';

  const response = await apiRequest<{ patterns: AdPattern[] }>(url);
  return response.patterns;
}

export async function getPattern(id: number): Promise<AdPattern> {
  return apiRequest<AdPattern>(`/patterns/${id}`);
}

export async function updatePattern(
  id: number,
  updates: {
    text_template?: string;
    sponsor?: string;
    intro_variants?: string[];
    outro_variants?: string[];
    is_active?: boolean;
    disabled_reason?: string;
    scope?: string;
  }
): Promise<void> {
  await apiRequest(`/patterns/${id}`, {
    method: 'PUT',
    body: updates,
  });
}

// Correction API

export async function submitCorrection(
  slug: string,
  episodeId: string,
  correction: PatternCorrection
): Promise<void> {
  await apiRequest(`/episodes/${slug}/${episodeId}/corrections`, {
    method: 'POST',
    body: correction,
  });
}
