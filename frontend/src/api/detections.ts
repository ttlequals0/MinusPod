import { apiRequest, buildQueryString } from './client';

export type DetectionStatus = 'accepted' | 'rejected' | 'pending';
export type DetectionResolution = 'unresolved' | 'confirmed' | 'dismissed';
export type DetectionStatusFilter =
  | 'needs_review' | 'pending' | 'rejected' | 'accepted' | 'all';
export type DetectionSort = 'date' | 'confidence' | 'podcast';

export interface ReviewDetection {
  feedSlug: string;
  feedTitle: string;
  episodeId: string;
  episodeTitle: string;
  publishDate: string | null;
  hasOriginalAudio: boolean;
  start: number;
  end: number;
  confidence: number | null;
  sponsor: string | null;
  reason: string | null;
  patternId: number | null;
  detectionStage: string | null;
  status: DetectionStatus;
  resolution: DetectionResolution;
}

export interface DetectionListResponse {
  detections: ReviewDetection[];
  total: number;
  page: number;
  totalPages: number;
  limit: number;
}

export interface DetectionListParams {
  page?: number;
  limit?: number;
  status?: DetectionStatusFilter;
  feed?: string;
  q?: string;
  sort?: DetectionSort;
  order?: 'asc' | 'desc';
}

export async function getDetections(
  params: DetectionListParams = {},
): Promise<DetectionListResponse> {
  const qs = buildQueryString({
    page: params.page,
    limit: params.limit,
    status: params.status,
    feed: params.feed,
    q: params.q,
    sort: params.sort,
    order: params.order,
  });
  return apiRequest<DetectionListResponse>(`/detections${qs}`);
}
