import { apiRequest, csrfHeaders, extractErrorMessage } from './client';

export type CueTemplateScope = 'podcast' | 'network';

export type CueTemplateType =
  | 'ad_break_boundary'
  | 'ad_break_start'
  | 'ad_break_end'
  | 'show_intro'
  | 'show_outro'
  | 'content_transition';

// Fixed cue-type vocabulary for the capture dropdown. The label here is the
// human option text; the server keeps its own canonical phrase for the LLM.
export const CUE_TYPE_OPTIONS: { value: CueTemplateType; label: string }[] = [
  { value: 'ad_break_boundary', label: 'Ad-break boundary (both ends)' },
  { value: 'ad_break_start', label: 'Ad-break start' },
  { value: 'ad_break_end', label: 'Ad-break end' },
  { value: 'show_intro', label: 'Show intro (not an ad)' },
  { value: 'show_outro', label: 'Show outro (not an ad)' },
  { value: 'content_transition', label: 'Content transition (may or may not be an ad)' },
];

export function cueTypeLabel(cueType: CueTemplateType): string {
  return CUE_TYPE_OPTIONS.find((o) => o.value === cueType)?.label ?? cueType;
}

// Per-type capture ceiling (seconds). Intro/outro stingers run longer than
// ad-break dings; their ceilings are the DB-settable audio_cue_capture_max_
// intro/outro_seconds settings, passed in here. Other types fall back to the
// global capture-max setting. The server enforces the same bound.
export function captureMaxForType(
  cueType: CueTemplateType,
  globalMax: number,
  introMax: number,
  outroMax: number,
): number {
  if (cueType === 'show_intro') return Math.max(globalMax, introMax);
  if (cueType === 'show_outro') return Math.max(globalMax, outroMax);
  return globalMax;
}

export interface CueTemplate {
  id: number;
  podcastId: number;
  label: string;
  cueType: CueTemplateType;
  sourceEpisodeId: string | null;
  sourceOffsetS: number;
  durationS: number;
  sampleRate: number;
  nCoeffs: number;
  scope: CueTemplateScope;
  networkId: string | null;
  enabled: boolean;
  createdAt: string;
  createdBy: string | null;
  // False for a network template shared from another feed in this network;
  // such rows are read-only here and managed on the feed that created them.
  owned?: boolean;
  // True when the template has a stored PCM blob; the play button is shown only
  // when this is true (or absent, for templates from older servers).
  hasAudio?: boolean;
  // Per-template match-score threshold. Overrides per-feed and global when set.
  // null = inherit from feed/global. Range [0, 0.99].
  scoreThreshold?: number | null;
  // Create-response only: how many times the captured cue recurs in its source
  // episode, and whether that makes it a weak (non-recurring) ad-break cue.
  // longCapture is true when an ad-break cue exceeds captureWarnSeconds (issue
  // #350: long captures degrade match quality). Absent on list rows.
  selfMatchCount?: number;
  weakCue?: boolean;
  longCapture?: boolean;
  captureWarnSeconds?: number;
}

export interface CueTemplateListResponse {
  templates: CueTemplate[];
}

export interface CueTemplateMatch {
  start: number;
  end: number;
  confidence: number;
  score: number;
}

export interface CueTemplatePreviewResponse {
  templateId: number;
  peakScore: number;
  matches: CueTemplateMatch[];
}

export async function listCueTemplates(slug: string): Promise<CueTemplate[]> {
  const res = await apiRequest<CueTemplateListResponse>(
    `/feeds/${slug}/cue-templates`,
  );
  return res.templates;
}

export async function createCueTemplate(
  slug: string,
  episodeId: string,
  startS: number,
  endS: number,
  cueType: CueTemplateType,
): Promise<CueTemplate> {
  const res = await apiRequest<{ template: CueTemplate }>(
    `/feeds/${slug}/cue-templates`,
    {
      method: 'POST',
      body: { episodeId, startS, endS, cueType },
    },
  );
  return res.template;
}

export async function updateCueTemplate(
  templateId: number,
  patch: { cueType?: CueTemplateType; enabled?: boolean; scope?: CueTemplateScope; networkId?: string; scoreThreshold?: number | null },
): Promise<CueTemplate> {
  const res = await apiRequest<{ template: CueTemplate }>(
    `/cue-templates/${templateId}`,
    { method: 'PATCH', body: patch },
  );
  return res.template;
}

// Direct URL for the export zip; an <a download> hits it with the session
// cookie (GET needs no CSRF).
export function cueTemplateExportUrl(templateId: number): string {
  return `/api/v1/cue-templates/${templateId}/export`;
}

// Direct URL for inline cue audio; an <audio src> hits it with the session
// cookie (GET needs no CSRF).
export function cueTemplateAudioUrl(templateId: number): string {
  return `/api/v1/cue-templates/${templateId}/audio`;
}

export async function importCueTemplate(slug: string, file: File): Promise<CueTemplate> {
  const formData = new FormData();
  formData.append('file', file);
  // Raw fetch: apiRequest would JSON.stringify the FormData. CSRF header still
  // required for the server-side double-submit check.
  const response = await fetch(`/api/v1/feeds/${slug}/cue-templates/import`, {
    method: 'POST',
    body: formData,
    headers: csrfHeaders('POST'),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ error: 'Import failed' }));
    throw new Error(extractErrorMessage(data, response.status));
  }
  const res = (await response.json()) as { template: CueTemplate };
  return res.template;
}

export async function deleteCueTemplate(templateId: number): Promise<void> {
  await apiRequest<{ deleted: boolean }>(
    `/cue-templates/${templateId}`,
    { method: 'DELETE' },
  );
}

export async function previewCueTemplate(
  slug: string,
  episodeId: string,
  templateId: number,
): Promise<CueTemplatePreviewResponse> {
  return apiRequest<CueTemplatePreviewResponse>(
    `/feeds/${slug}/episodes/${episodeId}/cue-template-preview`,
    { method: 'POST', body: { templateId } },
  );
}

export interface CueScanTemplateResult {
  id: number;
  label: string;
  durationS: number;
  peakScore: number;
  matchCount: number;
  matches: CueTemplateMatch[];
}

export interface CueScanResponse {
  episodeId: string;
  thresholdUsed: number;
  thresholdSource?: 'override' | 'global' | 'request';
  elapsedSeconds: number;
  templates: CueScanTemplateResult[];
}

export async function scanEpisodeCues(
  slug: string,
  episodeId: string,
  scoreThreshold?: number,
): Promise<CueScanResponse> {
  const body: Record<string, unknown> = {};
  if (scoreThreshold !== undefined) body.scoreThreshold = scoreThreshold;
  return apiRequest<CueScanResponse>(
    `/feeds/${slug}/episodes/${episodeId}/cue-scan`,
    { method: 'POST', body },
  );
}

export interface ThresholdSuggestion {
  confidence: 'high' | 'partial' | 'low';
  suggested: number | null;
  reason?: string;
  noiseCeiling?: number;
  signalFloor?: number;
  gapWidth?: number;
  signalCount?: number;
  effectFloor?: number;
  effectFloorWarning?: 'signal-below-floor' | null;
}

export interface ThresholdSuggestResponse {
  episodeId: string;
  status: 'scanning' | 'ready' | 'error';
  error?: string;
  suggestion?: ThresholdSuggestion;
  sampleEpisodes?: number;
  floorUsed?: number;
  currentThreshold?: number;
  scope?: 'feed' | 'global';
}

export async function suggestCueThreshold(
  slug: string,
  episodeId: string,
  rescan = false,
): Promise<ThresholdSuggestResponse> {
  return apiRequest<ThresholdSuggestResponse>(
    `/feeds/${slug}/cue-threshold-suggest`,
    { method: 'POST', body: { episodeId, rescan } },
  );
}

export type CueCandidateKind = 'recurring' | 'intro' | 'outro';

export interface CueCandidate {
  start: number;
  end: number;
  // 'recurring' (repeats within the episode -- an ad-break sting) or 'intro'/
  // 'outro' (a head/tail segment shared across sibling episodes). Older servers
  // omit kind and only returned recurring candidates, so missing = recurring.
  kind?: CueCandidateKind;
  count?: number;          // recurring: times the sound recurs within the episode
  episodeMatches?: number; // intro/outro: how many sibling episodes share it
  suggestedType?: CueTemplateType | null;  // capture-type hint
  adBoundaryHits?: number | null;    // recurring: occurrences near a known ad boundary
  boundaryAffinity?: number | null;  // adBoundaryHits / count; null = no ad history
  affinitySource?: 'episode' | 'siblings' | null;  // where affinity data came from
}

// Short badge label for a candidate.
export function cueCandidateLabel(c: CueCandidate): string {
  if (c.kind === 'intro') return `Intro (in ${c.episodeMatches ?? '?'} eps)`;
  if (c.kind === 'outro') return `Outro (in ${c.episodeMatches ?? '?'} eps)`;
  if (c.kind === 'recurring' && c.boundaryAffinity != null && c.adBoundaryHits != null) {
    if (c.affinitySource === 'siblings') {
      return `Repeats ${c.count ?? '?'}x -- ${Math.round(c.boundaryAffinity * 100)}% at known ad breaks (recent episodes)`;
    }
    return `Repeats ${c.count ?? '?'}x -- ${c.adBoundaryHits} of ${c.count ?? '?'} at known ad breaks`;
  }
  return `Repeats ${c.count ?? '?'}x`;
}

// Cue types the backend treats as non-ad (never cut) -- mirrors the 'non_ad' role
// in AUDIO_CUE_TYPES (src/config.py). Keep in sync when adding a non-ad type.
export function cueTypeIsNonAd(t: CueTemplateType): boolean {
  return t === 'show_intro' || t === 'show_outro' || t === 'content_transition';
}

export type CueCandidateScanStatus = 'scanning' | 'ready' | 'error' | 'idle';

export interface CueCandidatesResponse {
  episodeId: string;
  // Background-scan status. Older servers omit it; treat a missing status with
  // candidates present as 'ready'.
  status?: CueCandidateScanStatus;
  candidates: CueCandidate[];
  error?: string;
}

// Cross-episode scan types and client (D1b backend).

export type CrossEpisodeScanStatus = 'scanning' | 'ready' | 'error';

export interface CrossEpisodeCandidate {
  start: number;
  end: number;
  kind?: 'recurring';
  episodeMatches?: number;
}

export interface CrossEpisodeScanResponse {
  status: CrossEpisodeScanStatus;
  episodeIds?: string[];
  targetEpisodeId?: string;
  candidates?: CrossEpisodeCandidate[];
  error?: string;
}

// Claim or poll a cross-episode scan. POST-only, same body for both.
export async function crossEpisodeScan(
  slug: string,
  episodeIds: string[],
  rescan = false,
): Promise<CrossEpisodeScanResponse> {
  return apiRequest<CrossEpisodeScanResponse>(
    `/feeds/${slug}/cue-cross-episode-scan`,
    { method: 'POST', body: { episodeIds, rescan } },
  );
}

// On-demand scan: fingerprint the whole episode and return the sounds that
// recur across it (the ones worth templating). Loudness-independent, so it
// catches level-matched stings. The scan runs in the background and returns a
// status to poll; pass rescan to force a fresh run after an error.
export async function getCueCandidates(
  slug: string,
  episodeId: string,
  rescan = false,
  peek = false,
): Promise<CueCandidatesResponse> {
  // peek returns the cached result (or status 'idle') without starting a scan.
  const query = peek ? '?peek=1' : rescan ? '?rescan=1' : '';
  return apiRequest<CueCandidatesResponse>(
    `/feeds/${slug}/episodes/${episodeId}/cue-candidates${query}`,
  );
}
