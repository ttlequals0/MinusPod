import { describe, it, expect, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { ApiError } from '../api/client';
import { AuthProvider, useAuth, type LoginResult } from './AuthContext';

let nextError: unknown;
vi.mock('../api/auth', () => ({
  getAuthStatus: () => Promise.resolve({ passwordSet: true, authenticated: false }),
  login: async () => { throw nextError; },
  logout: vi.fn(),
}));

async function loginWith(err: unknown): Promise<LoginResult> {
  nextError = err;
  let result: LoginResult | undefined;
  let started = false;
  function Probe() {
    const { login, isLoading } = useAuth();
    if (!isLoading && !started) {
      started = true;
      login('pw', false).then((r) => { result = r; });
    }
    return null;
  }
  render(<AuthProvider><Probe /></AuthProvider>);
  await waitFor(() => expect(result).toBeDefined());
  return result as LoginResult;
}

describe('AuthContext login result', () => {
  it('prefers the body retryAfterSeconds over the header', async () => {
    const r = await loginWith(new ApiError('x', 429, { retryAfterSeconds: 300 }, 30000));
    expect(r).toEqual({ ok: false, kind: 'rateLimited', retryAfterSeconds: 300 });
  });

  it('falls back to the Retry-After header', async () => {
    const r = await loginWith(new ApiError('x', 429, { error: 'x' }, 90000));
    expect(r).toEqual({ ok: false, kind: 'rateLimited', retryAfterSeconds: 90 });
  });

  it('falls back to 60 seconds with neither source', async () => {
    const r = await loginWith(new ApiError('x', 429, null));
    expect(r).toEqual({ ok: false, kind: 'rateLimited', retryAfterSeconds: 60 });
  });

  it('maps 401 to invalid and other failures to error', async () => {
    expect(await loginWith(new ApiError('x', 401, null))).toEqual({ ok: false, kind: 'invalid' });
    expect(await loginWith(new Error('net'))).toEqual({ ok: false, kind: 'error' });
  });
});
