import { apiRequest } from './client';

export const patternCleanupQueryKey = ['patternCleanup'] as const;

export type CleanupKind = 'trim' | 'split' | 'rename' | 'retire' | 'flag';
export type CleanupStatus = 'pending' | 'approved' | 'rejected' | 'undone';

export interface PatternCleanupSettings {
  enabled: boolean;
  cron: string;
  batchSize: number;
  unusedDays: number;
  // Slot string as the AI Models card sends it; '' inherits detection.
  provider: string;
  // '' inherits the detection model.
  model: string;
}

export interface PatternCleanupSummary {
  runId: number;
  status: 'completed' | 'failed';
  trigger: 'schedule' | 'manual';
  forced: boolean;
  reviewed: number;
  suggested: number;
  skipped: number;
  errors: number;
  model: string | null;
  provider: string | null;
  credentialSlot: string | null;
  startedAt: string;
  finishedAt: string;
  durationMs: number;
  error: string | null;
}

export interface PatternCleanupStatus extends PatternCleanupSettings {
  inProgress: boolean;
  lastRun: string | null;
  lastError: string | null;
  lastSummary: PatternCleanupSummary | null;
  pending: { total: number; byKind: Partial<Record<CleanupKind, number>> };
}

export interface PatternCleanupRun {
  id: number;
  startedAt: string | null;
  finishedAt: string | null;
  status: 'running' | 'completed' | 'failed';
  forced: boolean;
  trigger: string | null;
  model: string | null;
  provider: string | null;
  credentialSlot: string | null;
  reviewedCount: number;
  suggestedCount: number;
  skippedCount: number;
  errorCount: number;
  error: string | null;
}

// Payload keys are snake_case as stored by the service.
export interface TrimPayload { text: string }
export interface SplitPayload { pieces: Array<{ text: string; sponsor: string }> }
export interface RenamePayload { sponsor: string }
export interface RetirePayload {
  unused_days: number;
  last_matched_at: string | null;
  confirmation_count: number;
}
export interface FlagPayload {
  false_positive_count: number;
  confirmation_count: number;
  contaminated: boolean;
  contamination_reason: string | null;
  recommended: 'disable' | 'trim';
  trim_text?: string;
}

export interface CleanupPatternSummary {
  id: number;
  sponsor: string | null;
  scope: string;
  podcastTitle: string | null;
  confirmationCount: number;
  falsePositiveCount: number;
  lastMatchedAt: string | null;
  createdAt: string | null;
}

export interface PatternCleanupSuggestion {
  id: number;
  runId: number | null;
  patternId: number;
  kind: CleanupKind;
  status: CleanupStatus;
  confidence: number | null;
  reasons: string[];
  payload: TrimPayload | SplitPayload | RenamePayload | RetirePayload | FlagPayload;
  before: Record<string, unknown> | null;
  applied: Record<string, unknown> | null;
  createdAt: string;
  reviewedAt: string | null;
  // Present on the list endpoint only.
  pattern?: CleanupPatternSummary;
}

export interface SuggestionQuery {
  status?: CleanupStatus;
  kind?: CleanupKind;
  limit?: number;
  offset?: number;
}

export type BulkResult = { id: number; status: CleanupStatus } | { id: number; error: 'not_found' | 'invalid_transition' };

export async function getPatternCleanupStatus(): Promise<PatternCleanupStatus> {
  return apiRequest<PatternCleanupStatus>('/patterns/cleanup');
}

export async function updatePatternCleanupSettings(
  body: Partial<PatternCleanupSettings>,
): Promise<PatternCleanupSettings> {
  return apiRequest<PatternCleanupSettings>('/settings/pattern-cleanup', { method: 'PUT', body });
}

export async function runPatternCleanup(force = false): Promise<{ runId: number }> {
  return apiRequest<{ runId: number }>('/patterns/cleanup/run', {
    method: 'POST',
    body: { force },
    skipRetry: true,
  });
}

export async function getPatternCleanupRuns(limit = 20): Promise<PatternCleanupRun[]> {
  const res = await apiRequest<{ runs: PatternCleanupRun[] }>(`/patterns/cleanup/runs?limit=${limit}`);
  return res.runs;
}

export async function getPatternCleanupSuggestions(
  query: SuggestionQuery = {},
): Promise<PatternCleanupSuggestion[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const qs = params.toString();
  const res = await apiRequest<{ suggestions: PatternCleanupSuggestion[] }>(
    `/patterns/cleanup/suggestions${qs ? `?${qs}` : ''}`,
  );
  return res.suggestions;
}

function suggestionAction(id: number, action: 'approve' | 'reject' | 'undo') {
  return apiRequest<PatternCleanupSuggestion>(`/patterns/cleanup/suggestions/${id}/${action}`, {
    method: 'POST',
  });
}

export const approveCleanupSuggestion = (id: number) => suggestionAction(id, 'approve');
export const rejectCleanupSuggestion = (id: number) => suggestionAction(id, 'reject');
export const undoCleanupSuggestion = (id: number) => suggestionAction(id, 'undo');

export async function bulkCleanupSuggestions(
  ids: number[],
  action: 'approve' | 'reject',
): Promise<BulkResult[]> {
  const res = await apiRequest<{ results: BulkResult[] }>('/patterns/cleanup/suggestions/bulk', {
    method: 'POST',
    body: { ids, action },
  });
  return res.results;
}
