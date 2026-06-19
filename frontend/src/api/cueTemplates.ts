import { apiRequest, csrfHeaders, extractErrorMessage } from './client';

export type CueTemplateScope = 'podcast' | 'network';

export interface CueTemplate {
  id: number;
  podcastId: number;
  label: string;
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
  label: string,
): Promise<CueTemplate> {
  const res = await apiRequest<{ template: CueTemplate }>(
    `/feeds/${slug}/cue-templates`,
    {
      method: 'POST',
      body: { episodeId, startS, endS, label },
    },
  );
  return res.template;
}

export async function updateCueTemplate(
  templateId: number,
  patch: { label?: string; enabled?: boolean; scope?: CueTemplateScope; networkId?: string },
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
