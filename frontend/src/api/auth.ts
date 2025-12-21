import { apiRequest } from './client';

export interface AuthStatus {
  passwordSet: boolean;
  authenticated: boolean;
}

export interface LoginResponse {
  authenticated: boolean;
  message: string;
}

export interface PasswordChangeResponse {
  message: string;
  passwordSet: boolean;
}

export async function getAuthStatus(): Promise<AuthStatus> {
  return apiRequest<AuthStatus>('/auth/status');
}

export async function login(password: string): Promise<LoginResponse> {
  return apiRequest<LoginResponse>('/auth/login', {
    method: 'POST',
    body: { password },
  });
}

export async function logout(): Promise<LoginResponse> {
  return apiRequest<LoginResponse>('/auth/logout', {
    method: 'POST',
  });
}

export async function setPassword(
  newPassword: string,
  currentPassword?: string
): Promise<PasswordChangeResponse> {
  return apiRequest<PasswordChangeResponse>('/auth/password', {
    method: 'PUT',
    body: {
      currentPassword: currentPassword || '',
      newPassword,
    },
  });
}

export async function removePassword(
  currentPassword: string
): Promise<PasswordChangeResponse> {
  return apiRequest<PasswordChangeResponse>('/auth/password', {
    method: 'PUT',
    body: {
      currentPassword,
      newPassword: '',
    },
  });
}
