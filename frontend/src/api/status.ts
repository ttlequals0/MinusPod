import { apiRequest } from './client';

/** An episode held by the SponsorBlock gate, as listed on the sponsorblock offline service. */
export interface WaitingEpisode {
  slug: string;
  episodeId: string;
  title: string;
  state: 'waiting' | 'probing';
  nextCheckAt: string | null;
  attempts: number;
}

export interface ProcessingScheduleStatus {
  enabled: boolean;
  open: boolean;
  nextChangeAt: string | null;
}

/** Sub-stage progress while a long stage runs; null or absent otherwise. */
export interface StageDetail {
  processed: number;
  total: number;
  unit: 'seconds' | 'chunks';
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
    stageDetail?: StageDetail | null;
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
      waiting?: WaitingEpisode[];
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
