import { apiRequest } from './client';

export interface ProcessingStatus {
  currentJob: {
    slug: string;
    episodeId: string;
    title: string;
    podcastName: string;
    stage: string;
    progress: number;
    elapsed: number;
  } | null;
  jobs?: Array<NonNullable<ProcessingStatus['currentJob']>>;
  queueLength: number;
  hold?: {
    queuePaused: boolean;
    holdUntil: string | null;
    offlineHeld: number;
    offlineServices: Array<{
      service: string;
      held: number;
      reachable: boolean | null;
    }>;
  };
}

export function getProcessingStatus(): Promise<ProcessingStatus> {
  return apiRequest<ProcessingStatus>('/status', { skipRetry: true });
}
