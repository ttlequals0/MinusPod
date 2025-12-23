import { apiRequest } from './client';
import { Settings, ClaudeModel, WhisperModel, SystemStatus, UpdateSettingsPayload } from './types';

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

export async function getModels(): Promise<ClaudeModel[]> {
  const response = await apiRequest<{ models: ClaudeModel[] }>('/settings/models');
  return response.models;
}

export async function getWhisperModels(): Promise<WhisperModel[]> {
  const response = await apiRequest<{ models: WhisperModel[] }>('/settings/whisper-models');
  return response.models;
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
}

export async function getProcessingEpisodes(): Promise<ProcessingEpisode[]> {
  return apiRequest<ProcessingEpisode[]>('/episodes/processing');
}

export async function cancelProcessing(slug: string, episodeId: string): Promise<{ message: string }> {
  return apiRequest<{ message: string }>(`/feeds/${slug}/episodes/${episodeId}/cancel`, {
    method: 'POST',
  });
}
