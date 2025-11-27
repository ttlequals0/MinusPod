import { apiRequest } from './client';
import { Settings, ClaudeModel, SystemStatus, UpdateSettingsPayload } from './types';

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

export async function getModels(): Promise<ClaudeModel[]> {
  const response = await apiRequest<{ models: ClaudeModel[] }>('/settings/models');
  return response.models;
}

export async function getSystemStatus(): Promise<SystemStatus> {
  return apiRequest<SystemStatus>('/system/status');
}

export async function runCleanup(): Promise<{ message: string; deleted_count: number }> {
  return apiRequest<{ message: string; deleted_count: number }>('/system/cleanup', {
    method: 'POST',
  });
}
