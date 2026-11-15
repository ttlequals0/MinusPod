import { apiRequest } from './client';

// Show-bound community segments: opaque per-show entries in the community library.
export interface CommunitySegmentsSummary {
  enabled: boolean;
  counts: Record<string, number>;
  lastRun: string | null;
  lastSummary: Record<string, number> | null;
}

export async function getCommunitySegmentsSummary(): Promise<CommunitySegmentsSummary> {
  return apiRequest<CommunitySegmentsSummary>('/community-segments/summary', { skipRetry: true });
}

export async function setCommunitySegmentsEnabled(enabled: boolean): Promise<CommunitySegmentsSummary> {
  return apiRequest<CommunitySegmentsSummary>('/community-segments/settings', {
    method: 'PUT',
    body: { enabled },
  });
}

export async function removeAllCommunitySegments(): Promise<{ deleted: number }> {
  return apiRequest<{ deleted: number }>('/community-segments', { method: 'DELETE' });
}
