import { apiRequest } from './client';

export interface ApiToken {
  id: number;
  name: string;
  prefix: string;
  createdAt: string;
  lastUsedAt: string | null;
}

export interface CreatedApiToken extends ApiToken {
  token: string;
}

export const API_TOKENS_QUERY_KEY = ['api-tokens'] as const;

export async function listApiTokens(): Promise<ApiToken[]> {
  return (await apiRequest<{ tokens: ApiToken[] }>('/auth/tokens')).tokens;
}

export async function createApiToken(name: string): Promise<CreatedApiToken> {
  return apiRequest<CreatedApiToken>('/auth/tokens', { method: 'POST', body: { name } });
}

export async function revokeApiToken(id: number) {
  return apiRequest<{ revoked: boolean }>(`/auth/tokens/${id}`, { method: 'DELETE' });
}
