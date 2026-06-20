import { apiRequest, csrfHeaders, extractErrorMessage } from './client';

export type CueTemplateScope = 'podcast' | 'network';

export type CueTemplateType =
  | 'ad_break_boundary'
  | 'ad_break_start'
  | 'ad_break_end'
  | 'show_intro'
  | 'show_outro';

// Fixed cue-type vocabulary for the capture dropdown. The label here is the
// human option text; the server keeps its own canonical phrase for the LLM.
export const CUE_TYPE_OPTIONS: { value: CueTemplateType; label: string }[] = [
  { value: 'ad_break_boundary', label: 'Ad-break boundary (both ends)' },
  { value: 'ad_break_start', label: 'Ad-break start' },
  { value: 'ad_break_end', label: 'Ad-break end' },
  { value: 'show_intro', label: 'Show intro (not an ad)' },
  { value: 'show_outro', label: 'Show outro (not an ad)' },
];

export function cueTypeLabel(cueType: CueTemplateType): string {
  return CUE_TYPE_OPTIONS.find((o) => o.value === cueType)?.label ?? cueType;
}

// Per-type capture ceiling (seconds), mirroring config.AUDIO_CUE_CAPTURE_MAX_BY_TYPE.
// Intro/outro stingers run longer than ad-break dings; other types fall back to
// the global capture-max setting. The server enforces the same bound.
const CAPTURE_MAX_BY_TYPE: Partial<Record<CueTemplateType, number>> = {
  show_intro: 60,
  show_outro: 60,
};

export function captureMaxForType(cueType: CueTemplateType, globalMax: number): number {
  const typeMax = CAPTURE_MAX_BY_TYPE[cueType];
  return typeMax != null ? Math.max(globalMax, typeMax) : globalMax;
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
  patch: { cueType?: CueTemplateType; enabled?: boolean; scope?: CueTemplateScope; networkId?: string },
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

export interface CueCandidate {
  start: number;
  end: number;
  prominenceDb: number | null;
  count: number;
}

export interface CueCandidatesResponse {
  episodeId: string;
  candidates: CueCandidate[];
}

// On-demand scan: decode the audio, cluster loud bursts by similarity, and
// return only sounds that recur (the ones worth templating). Slow.
export async function getCueCandidates(
  slug: string,
  episodeId: string,
): Promise<CueCandidatesResponse> {
  return apiRequest<CueCandidatesResponse>(
    `/feeds/${slug}/episodes/${episodeId}/cue-candidates`,
  );
}
