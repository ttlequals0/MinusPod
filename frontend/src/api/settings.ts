import { apiRequest, apiFileRequest } from './client';
import { downloadBlob } from './history';
import { Settings, ClaudeModel, WhisperModel, SystemStatus, UpdateSettingsPayload, RetentionSettings, ProcessingTimeouts, ReplacementAudio } from './types';

export async function getSettings(): Promise<Settings> {
  return apiRequest<Settings>('/settings');
}

export async function updateSettings(settings: UpdateSettingsPayload): Promise<{ message: string }> {
  return apiRequest<{ message: string }>('/settings/ad-detection', {
    method: 'PUT',
    body: settings,
  });
}

export async function resetSettings(): Promise<{ message: string }> {
  return apiRequest<{ message: string }>('/settings/ad-detection/reset', {
    method: 'POST',
  });
}

export async function resetPrompts(): Promise<{ message: string }> {
  return apiRequest<{ message: string }>('/settings/prompts/reset', {
    method: 'POST',
  });
}

export type PromptName = 'system' | 'verification' | 'review' | 'resurrect' | 'chapter';

export async function resetPrompt(name: PromptName): Promise<{ value: string; isDefault: boolean }> {
  return apiRequest<{ value: string; isDefault: boolean }>(`/settings/prompts/${name}/reset`, {
    method: 'POST',
  });
}

export async function regenerateFeedKey(): Promise<{ feedAuthKey: string }> {
  return apiRequest<{ feedAuthKey: string }>('/settings/feed-auth/regenerate-key', {
    method: 'POST',
  });
}

export async function getModels(provider?: string): Promise<ClaudeModel[]> {
  const params = provider ? `?provider=${encodeURIComponent(provider)}` : '';
  const response = await apiRequest<{ models: ClaudeModel[] }>(`/settings/models${params}`);
  return response.models;
}

export async function getWhisperModels(): Promise<WhisperModel[]> {
  const response = await apiRequest<{ models: WhisperModel[] }>('/settings/whisper-models');
  return response.models;
}

export async function refreshModels(): Promise<{ models: ClaudeModel[]; count: number }> {
  return apiRequest<{ models: ClaudeModel[]; count: number }>('/settings/models/refresh', {
    method: 'POST',
  });
}

export async function getSystemStatus(): Promise<SystemStatus> {
  return apiRequest<SystemStatus>('/system/status');
}

export async function runCleanup(): Promise<{ message: string; episodesRemoved: number; spaceFreedMb: number }> {
  return apiRequest<{ message: string; episodesRemoved: number; spaceFreedMb: number }>('/system/cleanup', {
    method: 'POST',
  });
}

// Processing Queue

export interface ProcessingEpisode {
  episodeId: string;
  slug: string;
  title: string;
  podcast: string;
  startedAt: string | null;
  /** Pipeline stage for the active job, or 'queued' while waiting its turn. */
  stage?: string | null;
  queuedAt?: string | null;
  /** 1-based position in the pending queue (queued entries only). */
  queuePosition?: number;
  /** Size of the whole backlog, which can exceed the rows the API returns. */
  queueTotal?: number;
  priority?: number | null;
}

export async function getProcessingEpisodes(params?: {
  queueOffset?: number;
  queueLimit?: number;
}): Promise<ProcessingEpisode[]> {
  const search = new URLSearchParams();
  if (params?.queueOffset) search.set('offset', String(params.queueOffset));
  if (params?.queueLimit) search.set('limit', String(params.queueLimit));
  const qs = search.toString();
  return apiRequest<ProcessingEpisode[]>(`/episodes/processing${qs ? `?${qs}` : ''}`);
}

export interface PendingRecut {
  slug: string;
  episodeId: string;
  title: string;
  podcast: string;
  pendingSince: string;
}

/** Episodes holding review decisions that are not in the audio yet.
 *  `slug` scopes the list to one feed. */
export async function getPendingRecuts(slug?: string): Promise<{
  count: number; episodes: PendingRecut[];
}> {
  const query = slug ? `?slug=${encodeURIComponent(slug)}` : '';
  return apiRequest(`/episodes/pending-recuts${query}`);
}

/** Recut every pending episode once, or one feed's when `slug` is given. */
export async function applyPendingRecuts(slug?: string): Promise<{
  queued: number; skipped: number;
}> {
  return apiRequest('/episodes/pending-recuts/apply', {
    method: 'POST',
    body: slug ? { slug } : {},
  });
}

export async function cancelProcessing(slug: string, episodeId: string): Promise<{ message: string }> {
  return apiRequest<{ message: string }>(`/feeds/${slug}/episodes/${episodeId}/cancel`, {
    method: 'POST',
  });
}

/** Pass `priority` to set an exact value, or `delta` to nudge the stored one. */
export async function setQueuePriority(
  slug: string, episodeId: string, change: { priority?: number; delta?: number },
): Promise<{ message: string; priority: number }> {
  return apiRequest<{ message: string; priority: number }>(
    `/feeds/${slug}/episodes/${episodeId}/queue-priority`,
    { method: 'POST', body: change },
  );
}

export async function getRetention(): Promise<RetentionSettings> {
  return apiRequest<RetentionSettings>('/settings/retention');
}

export async function updateRetention(
  days: number,
  originalDays?: number
): Promise<RetentionSettings> {
  const body: { retentionDays: number; originalRetentionDays?: number } = {
    retentionDays: days,
  };
  if (originalDays !== undefined) {
    body.originalRetentionDays = originalDays;
  }
  return apiRequest<RetentionSettings>('/settings/retention', {
    method: 'PUT',
    body,
  });
}

export async function getAudioSettings(): Promise<{ keepOriginalAudio: boolean }> {
  return apiRequest<{ keepOriginalAudio: boolean }>('/settings/audio');
}

export async function updateAudioSettings(keepOriginalAudio: boolean): Promise<{ keepOriginalAudio: boolean }> {
  return apiRequest('/settings/audio', { method: 'PUT', body: { keepOriginalAudio } });
}

export async function getProcessingTimeouts(): Promise<ProcessingTimeouts> {
  return apiRequest<ProcessingTimeouts>('/settings/processing-timeouts');
}

export async function updateProcessingTimeouts(
  softTimeoutSeconds: number,
  hardTimeoutSeconds: number,
): Promise<{ softTimeoutSeconds: number; hardTimeoutSeconds: number }> {
  return apiRequest('/settings/processing-timeouts', {
    method: 'PUT',
    body: { softTimeoutSeconds, hardTimeoutSeconds },
  });
}

// Webhook types

export interface Webhook {
  id: string;
  url: string;
  events: string[];
  enabled: boolean;
  payloadTemplate: string | null;
  contentType: string;
}

export interface WebhookPayload {
  url: string;
  events: string[];
  enabled: boolean;
  secret?: string;
  payloadTemplate?: string | null;
  contentType?: string;
}

export interface TemplateValidationResult {
  valid: boolean;
  preview: string;
  error: string | null;
}

// Data Management

export async function exportOpml(mode: 'original' | 'modified' = 'original'): Promise<void> {
  const fallback = mode === 'modified' ? 'minuspod-feeds-modified.opml' : 'minuspod-feeds.opml';
  const { blob, filename } = await apiFileRequest(`/feeds/export-opml?mode=${mode}`, {
    fallbackFilename: fallback,
  });
  downloadBlob(blob, filename);
}

export async function downloadBackup(): Promise<void> {
  const { blob, filename } = await apiFileRequest('/system/backup', {
    fallbackFilename: 'minuspod-backup.db',
  });
  downloadBlob(blob, filename);
}

// Scheduled DB backups

export interface DatabaseBackupSettings {
  enabled: boolean;
  cron: string;
  dest: string;
  effectiveDest: string;
  destWritable: boolean;
  keepCount: number;
  lastRun: string | null;
  lastError: string | null;
  lastSummary: string | null;
}

export interface DatabaseBackupRunSummary {
  path: string;
  sizeBytes: number;
  durationMs: number;
  mode: string;
  keepCount: number;
  prunedCount: number;
  finishedAt: string;
}

export async function getDatabaseBackupSettings(): Promise<DatabaseBackupSettings> {
  return apiRequest<DatabaseBackupSettings>('/settings/db-backup');
}

export async function updateDatabaseBackupSettings(
  args: Partial<Pick<DatabaseBackupSettings, 'enabled' | 'cron' | 'dest' | 'keepCount'>>,
): Promise<DatabaseBackupSettings> {
  return apiRequest<DatabaseBackupSettings>('/settings/db-backup', {
    method: 'PUT',
    body: args,
  });
}

export interface OfflineQueueSettings {
  enabled: boolean;
  ttlHours: number;
  deferredCount: number;
}

export async function getOfflineQueueSettings(): Promise<OfflineQueueSettings> {
  return apiRequest<OfflineQueueSettings>('/settings/offline-queue');
}

export async function updateOfflineQueueSettings(
  args: Partial<Pick<OfflineQueueSettings, 'enabled' | 'ttlHours'>>,
): Promise<OfflineQueueSettings> {
  return apiRequest<OfflineQueueSettings>('/settings/offline-queue', {
    method: 'PUT',
    body: args,
  });
}

export interface RateLimitHoldSettings {
  enabled: boolean;
  ttlHours: number;
  /** ISO timestamp until which new queue claims pause, or null when idle. */
  holdUntil: string | null;
  holdCount: number;
}

export async function getRateLimitHoldSettings(): Promise<RateLimitHoldSettings> {
  return apiRequest<RateLimitHoldSettings>('/settings/rate-limit-hold');
}

export async function updateRateLimitHoldSettings(
  args: Partial<Pick<RateLimitHoldSettings, 'enabled' | 'ttlHours'>>,
): Promise<RateLimitHoldSettings> {
  return apiRequest<RateLimitHoldSettings>('/settings/rate-limit-hold', {
    method: 'PUT',
    body: args,
  });
}

export async function runDatabaseBackupNow(): Promise<DatabaseBackupRunSummary> {
  return apiRequest<DatabaseBackupRunSummary>('/system/db-backup/run', {
    method: 'POST',
    skipRetry: true,
  });
}

// Webhooks

export async function getWebhooks(): Promise<Webhook[]> {
  const response = await apiRequest<{ webhooks: Webhook[] }>('/settings/webhooks');
  return response.webhooks;
}

export async function createWebhook(payload: WebhookPayload): Promise<Webhook> {
  return apiRequest<Webhook>('/settings/webhooks', { method: 'POST', body: payload });
}

export async function updateWebhook(id: string, payload: Partial<WebhookPayload>): Promise<Webhook> {
  return apiRequest<Webhook>(`/settings/webhooks/${id}`, { method: 'PUT', body: payload });
}

export async function deleteWebhook(id: string): Promise<{ message: string }> {
  return apiRequest<{ message: string }>(`/settings/webhooks/${id}`, { method: 'DELETE' });
}

export interface WebhookTestResult {
  success: boolean;
  results: { event: string; delivered: boolean }[];
  message: string;
}

export async function testWebhook(id: string): Promise<WebhookTestResult> {
  return apiRequest<WebhookTestResult>(`/settings/webhooks/${id}/test`, { method: 'POST' });
}

export async function validateTemplate(template: string): Promise<TemplateValidationResult> {
  return apiRequest<TemplateValidationResult>('/settings/webhooks/validate-template', {
    method: 'POST',
    body: { template },
  });
}

// Email notifications

export interface EmailNotificationSettings {
  enabled: boolean;
  events: string[];
  smtpHost: string;
  smtpPort: number;
  smtpSecurity: 'none' | 'starttls' | 'ssl';
  smtpUsername: string;
  smtpPasswordConfigured: boolean;
  fromAddress: string;
  recipients: string;
}

export type EmailNotificationSettingsPayload =
  Partial<Omit<EmailNotificationSettings, 'smtpPasswordConfigured'>> & { smtpPassword?: string };

export async function getEmailNotificationSettings(): Promise<EmailNotificationSettings> {
  return apiRequest<EmailNotificationSettings>('/settings/notifications/email');
}

export async function updateEmailNotificationSettings(
  payload: EmailNotificationSettingsPayload,
): Promise<EmailNotificationSettings> {
  return apiRequest<EmailNotificationSettings>('/settings/notifications/email', {
    method: 'PUT',
    body: payload,
  });
}

export async function sendTestEmail(): Promise<{ success: boolean; message: string }> {
  return apiRequest<{ success: boolean; message: string }>('/settings/notifications/email/test', {
    method: 'POST',
    skipRetry: true,
  });
}

export async function getReplacementAudio(): Promise<ReplacementAudio> {
  return apiRequest<ReplacementAudio>('/settings/replacement-audio');
}

export async function uploadReplacementAudio(file: File): Promise<ReplacementAudio> {
  const body = new FormData();
  body.append('file', file);
  return apiRequest<ReplacementAudio>('/settings/replacement-audio', { method: 'POST', body });
}

export async function revertReplacementAudio(): Promise<ReplacementAudio> {
  return apiRequest<ReplacementAudio>('/settings/replacement-audio', { method: 'DELETE' });
}
