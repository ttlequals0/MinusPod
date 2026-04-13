import { apiRequest } from './client';

export type ProviderName = 'anthropic' | 'openai' | 'openrouter' | 'whisper' | 'ollama';

export interface ProviderStatus {
  configured: boolean;
  source: 'db' | 'env' | 'none';
  baseUrl?: string;
  model?: string;
}

export interface ProvidersResponse {
  cryptoReady: boolean;
  anthropic: ProviderStatus;
  openai: ProviderStatus;
  openrouter: ProviderStatus;
  whisper: ProviderStatus;
  ollama: ProviderStatus;
}

export interface ProviderUpdatePayload {
  apiKey?: string | null;
  baseUrl?: string;
  model?: string;
}

export interface ProviderTestResult {
  ok: boolean;
  error?: string;
}

export function listProviders() {
  return apiRequest<ProvidersResponse>('/settings/providers');
}

export function updateProvider(name: ProviderName, payload: ProviderUpdatePayload) {
  return apiRequest<ProviderStatus>(`/settings/providers/${name}`, {
    method: 'PUT',
    body: payload,
  });
}

export function clearProvider(name: ProviderName) {
  return apiRequest<ProviderStatus>(`/settings/providers/${name}`, {
    method: 'DELETE',
  });
}

export function testProvider(name: ProviderName) {
  return apiRequest<ProviderTestResult>(`/settings/providers/${name}/test`, {
    method: 'POST',
  });
}

export function rotateMasterPassphrase(oldPassphrase: string, newPassphrase: string) {
  return apiRequest<{ rotated: number }>('/settings/providers/rotate-passphrase', {
    method: 'POST',
    body: { oldPassphrase, newPassphrase },
  });
}
