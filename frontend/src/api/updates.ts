import { apiRequest } from './client';
import type { UpdateCheckSettings, UpdateStatus } from './types';

export async function getUpdateStatus(refresh = false): Promise<UpdateStatus> {
  return apiRequest<UpdateStatus>(`/system/updates${refresh ? '?refresh=true' : ''}`);
}

export async function getUpdateCheckSettings(): Promise<UpdateCheckSettings> {
  return apiRequest<UpdateCheckSettings>('/settings/update-check');
}

export async function updateUpdateCheckSettings(
  payload: Partial<UpdateCheckSettings>,
): Promise<UpdateCheckSettings> {
  return apiRequest<UpdateCheckSettings>('/settings/update-check', {
    method: 'PUT',
    body: payload,
  });
}
