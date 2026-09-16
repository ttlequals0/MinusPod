import { EPISODE_STATUS_KEYS, type EpisodeStatusKey } from '../api/types';
import { tint } from '../components/badgeStyles';

export const EPISODE_STATUS_COLORS: Record<string, string> = {
  discovered: tint.blue,
  pending: tint.neutral,
  queued: tint.purple,
  processing: tint.warning,
  completed: tint.success,
  failed: tint.destructive,
  permanently_failed: tint.destructive,
  deferred: tint.purple,
};

export const EPISODE_STATUS_LABELS: Record<string, string> = {
  discovered: 'discovered',
  pending: 'pending',
  queued: 'queued',
  processing: 'processing',
  completed: 'completed',
  failed: 'failed',
  permanently_failed: 'permanently failed',
  deferred: 'queued (offline)',
};

export function isFailedStatus(status: string): boolean {
  return status === 'failed' || status === 'permanently_failed';
}

// 'queued' isn't a real episode status; it overrides display while the
// episode's real status (still 'pending', etc.) waits in the run queue.
export function displayStatusKey(status: string, jobState?: string): string {
  return jobState === 'queued' ? 'queued' : status;
}

export function displayStatusLabel(status: string, jobState?: string): string {
  return EPISODE_STATUS_LABELS[displayStatusKey(status, jobState)] ?? status;
}

export function displayStatusColor(status: string, jobState?: string): string {
  return EPISODE_STATUS_COLORS[displayStatusKey(status, jobState)] ?? tint.neutral;
}

// Single source of iteration order for status summaries and stat cards.
export const EPISODE_STATUS_ORDER: readonly EpisodeStatusKey[] = EPISODE_STATUS_KEYS;

// Text-only variants of the badge palette for big stat-card numbers.
export const EPISODE_STATUS_TEXT_COLORS: Record<EpisodeStatusKey | 'queued', string> = {
  discovered: 'text-c-blue',
  pending: 'text-muted-foreground',
  queued: 'text-c-purple',
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
