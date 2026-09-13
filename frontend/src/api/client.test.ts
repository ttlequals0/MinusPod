/**
 * apiRequest's retry-on-429 behavior (round-2 review finding 2): a 429
 * must retry (bounded, honoring the schedule in RETRY_DELAYS) rather than
 * immediately reject, and when the response carries a numeric-seconds
 * Retry-After header that value is used for the wait instead of the fixed
 * backoff schedule.
 *
 * fetch is mocked directly (not through an api/*.ts wrapper) since these
 * tests exercise apiRequest itself, the shared retry machinery every
 * wrapper funnels through.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { apiRequest } from './client';

// Minimal fetch Response stand-in: only the surface apiRequest actually
// reads (status/ok/headers.get/json), so this doesn't depend on whatever
// Response polyfill (or lack of one) the test environment provides.
function fakeResponse(body: unknown, status: number, headers: Record<string, string> = {}) {
  const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  if (!lower['content-type']) lower['content-type'] = 'application/json';
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => lower[name.toLowerCase()] ?? null },
    json: async () => body,
  } as Response;
}

describe('apiRequest: retry on 429', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('does not replay a POST after a retryable response', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(fakeResponse({ error: 'rate limited' }, 429, { 'Retry-After': '2' }))
      .mockResolvedValueOnce(fakeResponse({ staged: ['a.mp3'], rejected: [] }, 200));
    vi.stubGlobal('fetch', fetchMock);

    const promise = apiRequest('/feeds/show/import/upload', {
      method: 'POST',
      body: new FormData(),
    }).catch((error: Error) => error);

    expect(await promise).toBeInstanceOf(Error);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('reports an uncertain result without replaying a POST after a network error', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);

    const error = await apiRequest('/settings/webhooks', {
      method: 'POST',
      body: { url: 'https://example.com/hook' },
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toContain('may have completed');
    expect((error as Error).message).toContain('Check the current state');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('retries a POST only when the caller opts in', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(fakeResponse({ error: 'rate limited' }, 429))
      .mockResolvedValueOnce(fakeResponse({ ok: true }, 200));
    vi.stubGlobal('fetch', fetchMock);

    const promise = apiRequest('/idempotent-action', { method: 'POST', retry: true });
    await vi.advanceTimersByTimeAsync(1000);

    expect(await promise).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('falls back to the fixed backoff schedule when Retry-After is absent', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(fakeResponse({ error: 'rate limited' }, 429))
      .mockResolvedValueOnce(fakeResponse({ ok: true }, 200));
    vi.stubGlobal('fetch', fetchMock);

    const promise = apiRequest('/x', { method: 'GET' });
    await vi.advanceTimersByTimeAsync(1000); // RETRY_DELAYS[0]
    const result = await promise;

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result).toEqual({ ok: true });
  });

  it('gives up after 3 attempts (2 retries) and throws, rather than retrying forever', async () => {
    const fetchMock = vi.fn().mockResolvedValue(fakeResponse({ error: 'rate limited' }, 429));
    vi.stubGlobal('fetch', fetchMock);

    const promise = apiRequest('/x', { method: 'GET' });
    // Swallow the eventual rejection so it doesn't surface as an
    // unhandled rejection while timers are still being advanced below.
    const caught = promise.catch((e: Error) => e);

    await vi.advanceTimersByTimeAsync(1000); // RETRY_DELAYS[0]
    await vi.advanceTimersByTimeAsync(3000); // RETRY_DELAYS[1]

    const err = await caught;
    expect(err).toBeInstanceOf(Error);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
