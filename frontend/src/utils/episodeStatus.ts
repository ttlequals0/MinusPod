import { EPISODE_STATUS_KEYS, type EpisodeStatusKey } from '../api/types';

export const EPISODE_STATUS_COLORS: Record<string, string> = {
  discovered: 'bg-c-blue/20 text-c-blue',
  pending: 'bg-muted text-muted-foreground',
  processing: 'bg-warning/20 text-warning',
  completed: 'bg-success/20 text-success',
  failed: 'bg-destructive/20 text-destructive',
  permanently_failed: 'bg-destructive/20 text-destructive',
  deferred: 'bg-c-purple/20 text-c-purple',
};

export const EPISODE_STATUS_LABELS: Record<string, string> = {
  discovered: 'discovered',
  pending: 'pending',
  processing: 'processing',
  completed: 'completed',
  failed: 'failed',
  permanently_failed: 'permanently failed',
  deferred: 'queued (offline)',
};

export function isFailedStatus(status: string): boolean {
  return status === 'failed' || status === 'permanently_failed';
}

// Single source of iteration order for status summaries and stat cards.
export const EPISODE_STATUS_ORDER: readonly EpisodeStatusKey[] = EPISODE_STATUS_KEYS;

// Text-only variants of the badge palette for big stat-card numbers.
export const EPISODE_STATUS_TEXT_COLORS: Record<EpisodeStatusKey, string> = {
  discovered: 'text-c-blue',
  pending: 'text-muted-foreground',
  processing: 'text-warning',
  completed: 'text-success',
  failed: 'text-destructive',
  permanently_failed: 'text-destructive',
  deferred: 'text-c-purple',
};

// Compact labels for the dashboard per-feed summary pills.
export const EPISODE_STATUS_SHORT_LABELS: Record<EpisodeStatusKey, string> = {
  discovered: 'Disc',
  pending: 'Pend',
  processing: 'Proc',
  completed: 'Comp',
  failed: 'Fail',
  permanently_failed: 'Perm Fail',
  deferred: 'Queued',
};
