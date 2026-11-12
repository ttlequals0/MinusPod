import type { QueryClient } from '@tanstack/react-query';
import { apiRequest, apiFileRequest, buildQueryString } from './client';
import { downloadBlob } from './history';
import type { SegmentCategory } from '../utils/segmentCategory';
import { apiUrl } from '../urlPrefix';
import { mediaUrl } from '../utils/mediaUrl';

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

// Mirrors pattern_service.compute_pattern_trust's return values. Computed
// server-side, not stored; local (non-community) patterns are never 'stale'.
export type PatternTrust = 'active' | 'unproven' | 'stale';

// Mirrors the audio summary on each pattern; absent in the demo fixtures.
export type PatternAudioState = 'active' | 'off' | 'none';

export interface PatternAudioSummary {
  state: PatternAudioState;
  available: boolean;
  matchCount: number;
  meanSimilarity: number | null;
}

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
  can_split: boolean;
  disabled_at: string | null;
  disabled_reason: string | null;
  created_by?: string | null;
  source?: PatternSource;
  community_id?: string | null;
  version?: number;
  submitted_app_version?: string | null;
  protected_from_sync?: number;
  // What kind of content this pattern matches (issue #565); a NULL/legacy
  // row normalizes to 'sponsor' server-side, so this is always present.
  category?: SegmentCategory;
  // Computed staleness-based trust tier; always present in list responses.
  trust?: PatternTrust;
  audio?: PatternAudioSummary;
}

export interface PatternCorrection {
  type: 'confirm' | 'reject' | 'adjust' | 'create' | 'split' | 'recategorize';
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
  category?: string | null;
  // 'split' type fields: divider times inside original_ad, plus optional
  // per-piece sponsor overrides in piece order.
  split_points?: number[];
  pieces?: Array<{ sponsor?: string }>;
}

export interface SplitCandidate {
  time: number;
  phrase: string;
}

export interface SplitPiece {
  start: number;
  end: number;
  text: string;
  sponsor: string | null;
}

export interface SplitCandidatesResponse {
  episodeId: string;
  start: number;
  end: number;
  candidates: SplitCandidate[];
  pieces: SplitPiece[];
}

export interface SplitCorrectionResult {
  message: string;
  markerCount: number;
  patternIds: number[];
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
    // null clears the category (uncategorized resolves as Sponsor).
    category?: SegmentCategory | null;
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

export interface UnresolvedCorrectionCandidate {
  slug: string;
  podcast_title: string | null;
  episode_title: string | null;
  episode_available: boolean;
  source: 'current' | 'history';
  history_run_count: number | null;
  history_latest_processed_at: string | null;
}

export interface UnresolvedCorrection {
  id: number;
  episode_id: string;
  podcast_title: string | null;
  episode_title: string | null;
  correction_type: string;
  created_at: string;
  original_bounds: { start: number; end: number } | null;
  corrected_bounds: { start: number; end: number } | null;
  candidates: UnresolvedCorrectionCandidate[];
}

export interface UnresolvedCorrectionsResponse {
  count: number;
  corrections: UnresolvedCorrection[];
}

export async function getUnresolvedCorrections(): Promise<UnresolvedCorrectionsResponse> {
  return apiRequest<UnresolvedCorrectionsResponse>('/patterns/corrections/unresolved');
}

export async function assignUnresolvedCorrection(id: number, slug: string): Promise<void> {
  await apiRequest(`/patterns/corrections/${id}/assign`, {
    method: 'POST',
    body: { slug, confirm: true },
  });
}

export async function deleteUnresolvedCorrection(id: number): Promise<void> {
  await apiRequest(`/patterns/corrections/${id}`, { method: 'DELETE' });
}

export async function bulkUpdateUnresolvedCorrections(
  action: 'assign' | 'delete', correctionIds: number[], slug?: string,
): Promise<void> {
  await apiRequest('/patterns/corrections/unresolved/bulk', {
    method: 'POST', body: { action, correctionIds, slug, confirm: true },
  });
}

export async function getSplitCandidates(
  slug: string,
  episodeId: string,
  start: number,
  end: number,
): Promise<SplitCandidatesResponse> {
  return apiRequest<SplitCandidatesResponse>(
    `/feeds/${slug}/episodes/${episodeId}/split-candidates`
    + `?start=${start}&end=${end}`,
  );
}

export async function submitSplit(
  slug: string,
  episodeId: string,
  originalAd: { start: number; end: number },
  splitPoints: number[],
  pieces: Array<{ sponsor?: string }>,
): Promise<SplitCorrectionResult> {
  return apiRequest<SplitCorrectionResult>(
    `/episodes/${slug}/${episodeId}/corrections`,
    {
      method: 'POST',
      body: {
        type: 'split',
        original_ad: originalAd,
        split_points: splitPoints,
        pieces,
      } satisfies PatternCorrection,
    },
  );
}

// Bulk + community-pattern API

export interface MergeSuggestionMember {
  id: number;
  text_template: string;
  confirmation_count: number;
  false_positive_count: number;
  category?: SegmentCategory;
}

export interface MergeSuggestion {
  sponsor_id: number | null;
  sponsor: string | null;
  suggested_keep_id: number;
  pattern_ids: number[];
  count: number;
  members: MergeSuggestionMember[];
  result_intro_variant_count: number;
  result_outro_variant_count: number;
}

export async function getMergeSuggestions(): Promise<MergeSuggestion[]> {
  const res = await apiRequest<{ suggestions: MergeSuggestion[] }>('/patterns/merge-suggestions');
  return res.suggestions;
}

export interface MergePatternsResult {
  message: string;
  kept_pattern_id: number;
  merged_count: number;
  total_confirmations: number;
  total_false_positives: number;
  intro_variant_count: number;
  outro_variant_count: number;
  warning?: string;
}

export async function mergePatterns(args: {
  keep_id: number;
  merge_ids: number[];
}): Promise<MergePatternsResult> {
  return apiRequest<MergePatternsResult>('/patterns/merge', {
    method: 'POST',
    body: args,
  });
}

export interface PatternOverride {
  sponsor?: string;
  sponsor_aliases?: string[];
  sponsor_tags?: string[];
}

export type PatternOverrides = Record<number, PatternOverride>;

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

export async function previewExportBundle(
  ids: number[],
  overrides?: PatternOverrides,
): Promise<BundlePreview> {
  return apiRequest<BundlePreview>('/patterns/preview-export', {
    method: 'POST',
    body: overrides ? { ids, overrides } : { ids },
  });
}

// apiRequest assumes JSON responses; the bundle endpoint streams a file,
// so we use apiFileRequest which preserves CSRF + error-stringification.
// The actual browser download happens here so callers only deal with the
// resulting filename.
export async function downloadCommunityBundle(
  ids: number[],
  overrides?: PatternOverrides,
): Promise<{ filename: string }> {
  const { blob, filename } = await apiFileRequest('/patterns/submit-bundle', {
    method: 'POST',
    body: overrides ? { ids, overrides } : { ids },
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

export interface SplitPatternResult {
  success: boolean;
  original_pattern_id: number;
  new_pattern_ids: number[];
  message: string;
}

export async function splitPattern(id: number): Promise<SplitPatternResult> {
  return apiRequest<SplitPatternResult>(`/patterns/${id}/split`, { method: 'POST' });
}

// Pattern audio fingerprint

export interface AudioSpan {
  podcastSlug: string;
  episodeId: string;
  start: number;
  end: number;
}

export interface AudioDetail {
  state: PatternAudioState;
  available: boolean;
  length?: number | null;
  createdAt?: string | null;
  builtFrom?: (AudioSpan & { available: boolean }) | null;
  source?: AudioSpan | null;
  counts: { matches: number; rejected: number; outOfScope: number };
  meanSimilarity?: number | null;
  lastMatchedAt?: string | null;
  // True while the one-time fill of match history from older episodes is still running.
  historyBackfillPending?: boolean;
  // False when the stored audio cannot match under the current settings.
  matchable?: boolean;
  // The span a fingerprint could be built from when the pattern has none; null when unknown.
  addSource?: (AudioSpan & { available: boolean }) | null;
}

export interface AudioMatch {
  id: number;
  episodeId: string;
  podcastSlug: string;
  episodeTitle: string | null;
  start: number;
  end: number;
  similarity: number | null;
  coverage: number | null;
  storedLength: number | null;
  inScope: boolean;
  audioOnly: boolean | null;
  rejected: boolean;
  available: boolean;
  createdAt: string;
}

export interface AudioMatchesPage {
  matches: AudioMatch[];
  page: number;
  perPage: number;
  total: number;
}

export type FingerprintSkippedReason =
  | 'audio_removed'
  | 'fingerprint_exists'
  | 'span_covers_multiple_patterns'
  | 'original_not_retained'
  | 'segment_too_short'
  | 'fingerprint_failed'
  | 'not_distinctive';

export type AudioRebuildResult = {
  fingerprintStored: boolean;
  fingerprintSkippedReason?: FingerprintSkippedReason;
} & AudioDetail;

const FINGERPRINT_SKIP_TEXT: Record<FingerprintSkippedReason, string> = {
  audio_removed: 'Audio was deleted for this pattern, so it was not added back.',
  fingerprint_exists: 'This pattern already has an audio fingerprint.',
  span_covers_multiple_patterns: 'That selection covers more than one pattern, so no audio fingerprint was stored.',
  original_not_retained: "This episode's original audio is no longer kept.",
  segment_too_short: 'That selection is too short for an audio fingerprint.',
  fingerprint_failed: 'Could not make an audio fingerprint from that selection.',
  not_distinctive: 'That audio is not distinctive enough to match reliably.',
};

export function describeFingerprintSkip(reason: FingerprintSkippedReason): string {
  return FINGERPRINT_SKIP_TEXT[reason];
}

export const patternAudioKey = (patternId: number) => ['pattern-audio', patternId] as const;

export function invalidatePatternAudio(queryClient: QueryClient, patternId: number) {
  queryClient.invalidateQueries({ queryKey: ['patterns'] });
  queryClient.invalidateQueries({ queryKey: patternAudioKey(patternId) });
  queryClient.invalidateQueries({ queryKey: ['pattern-audio-matches', patternId] });
}

export async function getPatternAudio(id: number): Promise<AudioDetail> {
  return apiRequest<AudioDetail>(`/patterns/${id}/audio`);
}

export async function setPatternAudioEnabled(id: number, enabled: boolean): Promise<AudioDetail> {
  return apiRequest<AudioDetail>(`/patterns/${id}/audio`, { method: 'PUT', body: { enabled } });
}

export async function deletePatternAudio(id: number): Promise<void> {
  await apiRequest(`/patterns/${id}/audio`, { method: 'DELETE' });
}

export async function rebuildPatternAudio(
  id: number,
  span: AudioSpan,
): Promise<AudioRebuildResult> {
  return apiRequest<AudioRebuildResult>(`/patterns/${id}/audio/rebuild`, { method: 'POST', body: span });
}

export async function getPatternAudioMatches(
  id: number,
  params: { page?: number; perPage?: number } = {},
): Promise<AudioMatchesPage> {
  const qs = buildQueryString({ page: params.page, perPage: params.perPage });
  return apiRequest<AudioMatchesPage>(`/patterns/${id}/audio/matches${qs}`);
}

// Direct URL for an <audio src>; null in the demo build, which has no audio.
export function patternAudioPreviewUrl(id: number, matchId?: number): string | null {
  const qs = buildQueryString({ matchId });
  return mediaUrl({ kind: 'audio', url: apiUrl(`/patterns/${id}/audio/preview${qs}`) });
}

export function describePreviewStatus(status: number, code: string | undefined, hasRetryAfter: boolean): string {
  if (status === 503 && hasRetryAfter) return 'Audio is being prepared. Try again.';
  if (status === 404 && code === 'original_not_retained') return describeFingerprintSkip('original_not_retained');
  if (status === 404 && code === 'no_audio') return 'This pattern has no audio fingerprint.';
  return 'Could not play this audio. Try again.';
}

// One request after a playback error: an <audio> element hides the status, so read it, then abort.
export async function explainPreviewFailure(url: string, signal: AbortSignal): Promise<string> {
  const inner = new AbortController();
  signal.addEventListener('abort', () => inner.abort(), { once: true });
  try {
    const res = await fetch(url, { signal: inner.signal });
    let code: string | undefined;
    if (!res.ok) {
      try { code = ((await res.json()) as { code?: string } | null)?.code; } catch { /* no JSON body */ }
    }
    return describePreviewStatus(res.status, code, res.headers.get('Retry-After') !== null);
  } catch {
    return describePreviewStatus(0, undefined, false);
  } finally {
    inner.abort();
  }
}
