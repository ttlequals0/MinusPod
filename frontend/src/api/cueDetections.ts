import { apiRequest } from './client';
import type { CueDetection } from './types';

export type CueVerdict = CueDetection['verdict'];

// Record the user's review verdict for one cue detection. Advisory only -- the
// server never changes the episode's cut list in response.
export async function setCueDetectionVerdict(
  detectionId: number,
  verdict: CueVerdict,
): Promise<void> {
  await apiRequest(`/cue-detections/${detectionId}/verdict`, {
    method: 'POST',
    body: { verdict },
  });
}

export interface CueFeedAdvisory {
  total: number;
  snapped: number;
  paired: number;
  unused: number;
  confirmed: number;
  rejected: number;
  pending: number;
  avgScore: number | null;
  minScore: number | null;
  maxScore: number | null;
  confirmRate: number | null;
}

// Per-feed cue health summary -- lets the user judge a feed's cues before
// enabling cue-pair synthesis.
export async function getCueFeedAdvisory(slug: string): Promise<CueFeedAdvisory> {
  return apiRequest<CueFeedAdvisory>(`/feeds/${slug}/cue-detections/advisory`);
}
