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

export interface WhisperConnectionTestResult {
  ok: boolean;
  reachable: boolean;
  status?: number;
  detail: string;
}

// Sends the values currently in the form (saved or not) so the user can
// probe an endpoint before committing it. The stored API key is only sent
// by the backend when the tested URL matches the saved base URL.
export function testWhisperConnection(baseUrl: string, model: string, skipFlacCompression: boolean) {
  return apiRequest<WhisperConnectionTestResult>('/settings/providers/whisper/test-connection', {
    method: 'POST',
    body: { baseUrl, model, skipFlacCompression },
  });
}

export function rotateMasterPassphrase(oldPassphrase: string, newPassphrase: string) {
  return apiRequest<{ rotated: number }>('/settings/providers/rotate-passphrase', {
    method: 'POST',
    body: { oldPassphrase, newPassphrase },
  });
}
