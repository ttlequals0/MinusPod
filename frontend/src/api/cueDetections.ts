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

export interface CueScoreBucket {
  scoreFrom: number;
  count: number;
}

// Global cue telemetry for threshold tuning. Extends the feed advisory shape
// (totals count above-threshold cues only) with a match-score histogram, a
// separate near-miss histogram + total, and a per-reason unused breakdown.
export interface CueAggregateStats extends CueFeedAdvisory {
  scoreHistogram: CueScoreBucket[];
  nearMissHistogram: CueScoreBucket[];
  nearMissTotal: number;
  unusedReasons: Record<string, number>;
}

export async function getCueAggregateStats(): Promise<CueAggregateStats> {
  return apiRequest<CueAggregateStats>('/cue-detections/aggregate');
}
