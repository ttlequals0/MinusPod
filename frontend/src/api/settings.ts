import { apiRequest } from './client';
import { downloadBlob } from './history';
import { Settings, ClaudeModel, WhisperModel, SystemStatus, UpdateSettingsPayload, TokenUsageSummary, RetentionSettings, ProcessingTimeouts } from './types';

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

export async function getTokenUsage(): Promise<TokenUsageSummary> {
  return apiRequest<TokenUsageSummary>('/system/token-usage');
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
}

export async function getProcessingEpisodes(): Promise<ProcessingEpisode[]> {
  return apiRequest<ProcessingEpisode[]>('/episodes/processing');
}

export async function cancelProcessing(slug: string, episodeId: string): Promise<{ message: string }> {
  return apiRequest<{ message: string }>(`/feeds/${slug}/episodes/${episodeId}/cancel`, {
    method: 'POST',
  });
}

export async function getRetention(): Promise<RetentionSettings> {
  return apiRequest<RetentionSettings>('/settings/retention');
}

export async function updateRetention(days: number): Promise<RetentionSettings> {
  return apiRequest<RetentionSettings>('/settings/retention', {
    method: 'PUT',
    body: { retentionDays: days },
  });
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
  const response = await fetch(`/api/v1/feeds/export-opml?mode=${mode}`);
  if (!response.ok) throw new Error('Failed to export OPML');
  const blob = await response.blob();
  const filename = mode === 'modified' ? 'minuspod-feeds-modified.opml' : 'minuspod-feeds.opml';
  downloadBlob(blob, filename);
}

export async function downloadBackup(): Promise<void> {
  const response = await fetch('/api/v1/system/backup');
  if (!response.ok) throw new Error('Failed to download backup');
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename=([^;]+)/);
  const filename = match ? match[1] : 'minuspod-backup.db';
  downloadBlob(blob, filename);
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

export async function testWebhook(id: string): Promise<{ success: boolean; message: string }> {
  return apiRequest<{ success: boolean; message: string }>(`/settings/webhooks/${id}/test`, { method: 'POST' });
}

export async function validateTemplate(template: string): Promise<TemplateValidationResult> {
  return apiRequest<TemplateValidationResult>('/settings/webhooks/validate-template', {
    method: 'POST',
    body: { template },
  });
}
