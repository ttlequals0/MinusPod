import { apiRequest, buildQueryString } from './client';

export type InboxStatus = 'pending' | 'confirmed' | 'rejected' | 'adjusted';
export type InboxStatusFilter = InboxStatus | 'all';

export interface InboxItem {
  podcastSlug: string;
  podcastTitle: string;
  episodeId: string;
  episodeTitle: string | null;
  publishedAt: string | null;
  processedVersion: number | null;
  adIndex: number;
  start: number;
  end: number;
  duration: number;
  sponsor: string | null;
  reason: string | null;
  confidence: number | null;
  detectionStage: string | null;
  patternId: number | null;
  status: InboxStatus;
  correctedBounds: { start: number; end: number } | null;
}

export interface InboxResponse {
  items: InboxItem[];
  total: number;
  limit: number;
  offset: number;
  status: InboxStatusFilter;
  counts: {
    pending: number;
    confirmed: number;
    rejected: number;
    adjusted: number;
  };
}

export async function getAdInbox(
  status: InboxStatusFilter = 'pending',
  limit = 50,
  offset = 0,
): Promise<InboxResponse> {
  // buildQueryString already returns the leading "?" (or "" when empty).
  const qs = buildQueryString({ status, limit, offset });
  return apiRequest<InboxResponse>(`/ad-inbox${qs}`);
}

export interface PeaksResponse {
  episodeId: string;
  start: number;
  end: number | null;
  resolutionMs: number;
  peaks: number[];
}

export async function getEpisodePeaks(
  slug: string,
  episodeId: string,
  start: number,
  end: number,
  resolutionMs = 50,
): Promise<PeaksResponse> {
  const qs = buildQueryString({ start, end, resolution_ms: resolutionMs });
  return apiRequest<PeaksResponse>(
    `/feeds/${slug}/episodes/${episodeId}/peaks${qs}`,
  );
}
