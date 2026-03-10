export const EPISODE_STATUS_COLORS: Record<string, string> = {
  discovered: 'bg-blue-500/20 text-blue-600 dark:text-blue-400',
  pending: 'bg-muted text-muted-foreground',
  processing: 'bg-yellow-500/20 text-yellow-600 dark:text-yellow-400',
  completed: 'bg-green-500/20 text-green-600 dark:text-green-400',
  failed: 'bg-destructive/20 text-destructive',
  permanently_failed: 'bg-destructive/20 text-destructive',
};

export const EPISODE_STATUS_LABELS: Record<string, string> = {
  discovered: 'discovered',
  pending: 'pending',
  processing: 'processing',
  completed: 'completed',
  failed: 'failed',
  permanently_failed: 'permanently failed',
};
