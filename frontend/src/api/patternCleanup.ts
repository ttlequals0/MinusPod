import { apiRequest } from './client';
import type { PatternScope } from './patterns';

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

export interface PatternCleanupStatus extends PatternCleanupSettings {
  inProgress: boolean;
  // Start of the newest run, including one still running.
  lastRun: string | null;
  lastError: string | null;
  // The newest finished run.
  lastSummary: PatternCleanupRun | null;
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

export interface TrimPayload { text: string; sponsor?: string }
export interface SplitPayload { pieces: Array<{ text: string; sponsor: string }> }
export interface RenamePayload { sponsor: string }
export interface RetirePayload {
  unusedDays: number;
  lastMatchedAt: string | null;
  confirmationCount: number;
}
export interface FlagPayload {
  falsePositiveCount: number;
  confirmationCount: number;
  contaminated: boolean;
  contaminationReason: string | null;
  recommended: 'disable' | 'trim';
  trimText?: string;
  sponsor?: string;
}

// Pattern snapshot taken when the suggestion was made.
export interface CleanupBefore {
  textTemplate: string | null;
  sourceContext?: string | null;
  sponsor: string | null;
  introVariants: string[];
  outroVariants: string[];
  isActive: boolean | number | null;
  disabledReason: string | null;
}

export interface CleanupPatternSummary {
  id: number;
  sponsor: string | null;
  scope: PatternScope;
  networkId: string | null;
  podcastTitle: string | null;
  isActive: boolean;
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
  before: CleanupBefore | null;
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
  // Keyset cursor: only suggestions with id below this are returned.
  beforeId?: number;
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

export async function getPatternCleanupSuggestions(
  query: SuggestionQuery = {},
): Promise<PatternCleanupSuggestion[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined) continue;
    params.set(key === 'beforeId' ? 'before_id' : key, String(value));
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
