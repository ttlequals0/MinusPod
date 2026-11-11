import { apiRequest } from './client';

export interface ProcessingScheduleStatus {
  enabled: boolean;
  open: boolean;
  nextChangeAt: string | null;
}

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
  // Weekly processing window; absent on older builds.
  processingSchedule?: ProcessingScheduleStatus;
  // Provider failover (#806): which targets are on their failover account
  // right now, keyed by target name ('llm-a', 'llm-b', 'transcriber').
  failover?: {
    active: string[];
    targets: Record<string, { active: boolean; source: string | null; since: string | null }>;
  };
}

export function getProcessingStatus(): Promise<ProcessingStatus> {
  return apiRequest<ProcessingStatus>('/status', { skipRetry: true });
}
