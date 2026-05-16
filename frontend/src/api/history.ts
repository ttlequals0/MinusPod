import { apiRequest, apiFileRequest, buildQueryString } from './client';
import { ProcessingHistoryResponse, ProcessingHistoryStats } from './types';

export interface HistoryQueryParams {
  page?: number;
  limit?: number;
  status?: 'completed' | 'failed';
  podcastSlug?: string;
  sortBy?: 'processed_at' | 'processing_duration_seconds' | 'ads_detected' | 'reprocess_number' | 'llm_cost';
  sortDir?: 'asc' | 'desc';
}

export async function getProcessingHistory(
  params: HistoryQueryParams = {}
): Promise<ProcessingHistoryResponse> {
  const qs = buildQueryString({
    page: params.page,
    limit: params.limit,
    status: params.status,
    podcast_slug: params.podcastSlug,
    sort_by: params.sortBy,
    sort_dir: params.sortDir,
  });

  return apiRequest<ProcessingHistoryResponse>(`/history${qs}`);
}

export async function getProcessingHistoryStats(): Promise<ProcessingHistoryStats> {
  return apiRequest<ProcessingHistoryStats>('/history/stats');
}

export async function exportProcessingHistory(format: 'csv' | 'json'): Promise<Blob> {
  const { blob } = await apiFileRequest(`/history/export?format=${format}`, {
    fallbackFilename: `processing_history.${format}`,
  });
  return blob;
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
