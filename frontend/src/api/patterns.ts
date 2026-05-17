import { apiRequest, apiFileRequest, buildQueryString } from './client';
import { downloadBlob } from './history';

// Mirrors src/utils/community_tags.py:PATTERN_SOURCES so the frontend
// and backend can't drift on the source-discriminator string spellings.
export const PATTERN_SOURCE_LOCAL = 'local';
export const PATTERN_SOURCE_COMMUNITY = 'community';
export const PATTERN_SOURCE_IMPORTED = 'imported';
export const PATTERN_SOURCES = [
  PATTERN_SOURCE_LOCAL,
  PATTERN_SOURCE_COMMUNITY,
  PATTERN_SOURCE_IMPORTED,
] as const;
export type PatternSource = typeof PATTERN_SOURCES[number];

// Mirrors the scope discriminator stored on ad_patterns rows. UI filters
// (PatternsPage) and backend get_ad_patterns accept the same three values.
// The 'network' scope is omitted from PatternCorrection.scope below because
// user-driven pattern creation only exposes podcast vs global; network-scoped
// patterns are only produced server-side.
export type PatternScope = 'podcast' | 'network' | 'global';

export interface AdPattern {
  id: number;
  scope: PatternScope;
  network_id: string | null;
  podcast_id: string | null;
  podcast_name?: string | null;
  podcast_slug?: string | null;
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
  created_by?: string | null;
  source?: PatternSource;
  community_id?: string | null;
  version?: number;
  submitted_app_version?: string | null;
  protected_from_sync?: number;
}

export interface PatternCorrection {
  type: 'confirm' | 'reject' | 'adjust' | 'create';
  original_ad?: {
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
  // 'create' type fields
  start?: number;
  end?: number;
  sponsor?: string;
  text_template?: string;
  scope?: 'podcast' | 'global';
  reason?: string;
}

// Pattern Stats

export interface PatternStats {
  total: number;
  active: number;
  inactive: number;
  by_scope: {
    global: number;
    network: number;
    podcast: number;
  };
  no_sponsor: number;
  never_matched: number;
  stale_count: number;
  high_false_positive_count: number;
  stale_patterns: Array<{
    id: number;
    sponsor: string | null;
    last_matched_at: string;
    confirmation_count: number;
  }>;
  no_sponsor_patterns: Array<{
    id: number;
    scope: PatternScope;
    podcast_name: string | null;
    created_at: string;
    text_preview: string;
  }>;
  high_false_positive_patterns: Array<{
    id: number;
    sponsor: string | null;
    confirmation_count: number;
    false_positive_count: number;
  }>;
}

export async function getPatternStats(): Promise<PatternStats> {
  return apiRequest<PatternStats>('/patterns/stats');
}

// Pattern API

export async function getPatterns(params?: {
  scope?: PatternScope;
  podcast_id?: string;
  network_id?: string;
  active?: boolean;
  source?: PatternSource;
}): Promise<AdPattern[]> {
  const qs = buildQueryString({
    scope: params?.scope,
    podcast_id: params?.podcast_id,
    network_id: params?.network_id,
    active: params?.active,
    source: params?.source,
  });

  const response = await apiRequest<{ patterns: AdPattern[] }>(`/patterns${qs}`);
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
    scope?: PatternScope;
  }
): Promise<void> {
  await apiRequest(`/patterns/${id}`, {
    method: 'PUT',
    body: updates,
  });
}

export async function deletePattern(id: number): Promise<void> {
  await apiRequest(`/patterns/${id}`, {
    method: 'DELETE',
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

// Bulk + community-pattern API

export interface BulkPatternResult {
  deleted?: number;
  disabled?: number;
  ids: number[];
}

export async function bulkDeletePatterns(args: {
  ids?: number[];
  source?: 'local' | 'community' | 'imported';
  expected_count: number;
}): Promise<BulkPatternResult> {
  return apiRequest<BulkPatternResult>(`/patterns/bulk-delete`, {
    method: 'POST',
    body: { ...args, confirm: true },
  });
}

export async function bulkDisablePatterns(args: {
  ids?: number[];
  source?: 'local' | 'community' | 'imported';
  expected_count: number;
}): Promise<BulkPatternResult> {
  return apiRequest<BulkPatternResult>(`/patterns/bulk-disable`, {
    method: 'POST',
    body: { ...args, confirm: true },
  });
}

export interface CommunityExportResult {
  payload: Record<string, unknown>;
  filename: string;
  pr_url: string;
  too_large: boolean;
  sponsor_match: 'exact' | 'alias' | 'fuzzy' | 'unknown';
}

export async function submitPatternToCommunity(id: number): Promise<CommunityExportResult> {
  return apiRequest<CommunityExportResult>(`/patterns/${id}/submit-to-community`, {
    method: 'POST',
  });
}

export interface BundlePreviewRejection {
  id: number;
  sponsor: string | null;
  reasons: string[];
}

export interface BundlePreview {
  ready: number[];
  rejected: BundlePreviewRejection[];
  ready_count: number;
  rejected_count: number;
  pattern_count: number;
}

export async function previewExportBundle(ids: number[]): Promise<BundlePreview> {
  return apiRequest<BundlePreview>('/patterns/preview-export', {
    method: 'POST',
    body: { ids },
  });
}

// apiRequest assumes JSON responses; the bundle endpoint streams a file,
// so we use apiFileRequest which preserves CSRF + error-stringification.
// The actual browser download happens here so callers only deal with the
// resulting filename.
export async function downloadCommunityBundle(ids: number[]): Promise<{ filename: string }> {
  const { blob, filename } = await apiFileRequest('/patterns/submit-bundle', {
    method: 'POST',
    body: { ids },
    fallbackFilename: 'minuspod-community-submission.json',
  });
  downloadBlob(blob, filename);
  return { filename };
}

export async function protectPattern(id: number): Promise<void> {
  await apiRequest(`/patterns/${id}/protect`, { method: 'POST' });
}

export async function unprotectPattern(id: number): Promise<void> {
  await apiRequest(`/patterns/${id}/protect`, { method: 'DELETE' });
}
