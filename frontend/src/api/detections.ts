import { apiRequest, buildQueryString } from './client';

export type DetectionStatus = 'accepted' | 'rejected' | 'pending';
export type DetectionResolution = 'unresolved' | 'confirmed' | 'dismissed';
export type DetectionStatusFilter =
  | 'needs_review' | 'pending' | 'rejected' | 'accepted' | 'all';
export type DetectionSort = 'date' | 'confidence' | 'podcast';
export type DetectionReviewerFilter = '' | 'adjusted' | 'unadjusted';

export interface ReviewDetection {
  feedSlug: string;
  feedTitle: string;
  episodeId: string;
  episodeTitle: string;
  publishDate: string | null;
  hasOriginalAudio: boolean;
  processedUrl: string;
  start: number;
  end: number;
  confidence: number | null;
  sponsor: string | null;
  reason: string | null;
  patternId: number | null;
  detectionStage: string | null;
  category: string | null;
  actionApplied: string | null;
  // Set by the ad reviewer stage. The original span is present only when the
  // reviewer moved the span; held contradictions and split pieces keep the
  // 'adjust' verdict without it.
  reviewerVerdict: string | null;
  reviewerOriginalStart: number | null;
  reviewerOriginalEnd: number | null;
  // Whether the span was actually moved (by the reviewer or a human
  // approving a trimmed boundary); use this instead of inferring a move
  // from reviewerVerdict/reviewerOriginalStart/End.
  reviewerMoved: boolean;
  episodeDuration: number | null;
  status: DetectionStatus;
  resolution: DetectionResolution;
}

export interface DetectionCounts {
  total: number;
  needsReview: number;
  pending: number;
  rejected: number;
  accepted: number;
  confirmed: number;
  dismissed: number;
}

export interface CutSummary {
  count: number;
  durationSeconds: number;
  byCategory: Record<string, number>;
  distinctSponsors: number;
  distinctPodcasts: number;
}

export interface DetectionListResponse {
  detections: ReviewDetection[];
  total: number;
  page: number;
  totalPages: number;
  limit: number;
  counts: DetectionCounts;
  cutSummary: CutSummary;
}

// Type alias (not interface) so it satisfies buildQueryString's Record
// param via TypeScript's implicit index signature.
export type DetectionListParams = {
  page?: number;
  limit?: number;
  status?: DetectionStatusFilter;
  feed?: string;
  q?: string;
  sort?: DetectionSort;
  order?: 'asc' | 'desc';
  category?: string;
  reviewer?: DetectionReviewerFilter;
};

export async function getDetections(
  params: DetectionListParams = {},
): Promise<DetectionListResponse> {
  return apiRequest<DetectionListResponse>(`/detections${buildQueryString(params)}`);
}
